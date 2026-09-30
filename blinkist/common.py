"""HTTP requests and atomic, quality-preserving file downloads."""
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import tenacity

from .auth import get_session, get_cdn_session
from .config import HEADERS


class TransientHTTPError(RuntimeError):
    pass


class AccessDenied(RuntimeError):
    """An HTTP authorization failure without response bodies or credentials."""

    def __init__(self, status_code, error_code):
        self.status_code = status_code
        self.status = status_code
        self.error_code = error_code
        if error_code == "not_premium":
            detail = "Blinkist requires an authenticated Premium subscription for this content"
        elif status_code == 401:
            detail = "Blinkist login is required; the supplied session may be missing or expired"
        else:
            detail = "Blinkist denied access to this content"
        super().__init__(f"HTTP {status_code} ({error_code}); {detail}")


def _access_denied(response, stream=False):
    code = "login_required" if response.status_code == 401 else "forbidden"
    try:
        if stream:
            body = bytearray()
            for chunk in response.iter_content():
                if chunk:
                    body.extend(chunk[:16384 - len(body)])
                if len(body) >= 16384:
                    break
            payload = json.loads(body)
        else:
            payload = response.json()
        reported = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(reported, dict):
            reported = reported.get("code")
        if reported in ("not_premium", "not_authenticated", "unauthorized", "login_required", "forbidden"):
            code = reported
    except Exception:
        pass
    return AccessDenied(response.status_code, code)


@tenacity.retry(
    retry=tenacity.retry_if_exception_type(TransientHTTPError),
    wait=tenacity.wait_exponential(multiplier=1, max=8),
    stop=tenacity.stop_after_attempt(3),
    reraise=True,
)
def request(url, **kwargs):
    kwargs.setdefault("headers", HEADERS)
    kwargs.setdefault("timeout", (10, 60))
    host = urlsplit(url).hostname or ""
    if host == "blnkstcdn.com" or host.endswith(".blnkstcdn.com"):
        kwargs["headers"] = {**kwargs["headers"], "Accept-Encoding": "identity"}
        response = get_cdn_session().get(url, **kwargs)
    else:
        response = get_session().get(url, **kwargs)
    if response.status_code in (429, 500, 502, 503, 504):
        status = response.status_code
        response.close()
        raise TransientHTTPError(f"HTTP {status}; service temporarily unavailable")
    if response.status_code in (401, 403):
        try:
            error = _access_denied(response, stream=kwargs.get("stream", False))
        finally:
            response.close()
        raise error
    try:
        # Check the parsed status explicitly; failed streaming transfers still
        # raise while reading the body.
        if not 200 <= response.status_code < 400:
            response.raise_for_status()
    except Exception:
        response.close()
        raise
    return response


def api_request(base_url, endpoint, params=None):
    response = request(base_url + endpoint, params=params)
    try:
        return response.json()
    finally:
        response.close()


def api_request_web(endpoint, params=None):
    return api_request("https://www.blinkist.com/api/", endpoint, params)


def api_request_app(endpoint, params=None):
    return api_request("https://api.blinkist.com/", endpoint, params)


def validate_audio(path):
    """Reject error pages and broken containers without changing audio bytes."""
    with Path(path).open("rb") as handle:
        if b"ftyp" not in handle.read(64):
            raise ValueError("The response is not an MP4/M4A audio file")
    probe = shutil.which("ffprobe")
    if not probe:
        logging.warning("ffprobe unavailable; audio container validation is limited")
        return
    result = subprocess.run(
        [probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode:
        raise ValueError(f"ffprobe rejected the audio: {result.stderr.strip()}")
    metadata = json.loads(result.stdout)
    streams = [s for s in metadata.get("streams", []) if s.get("codec_type") == "audio"]
    duration = float(metadata.get("format", {}).get("duration", 0))
    if not streams or duration <= 0:
        raise ValueError("The file has no playable audio or positive duration")


def download(url, file_path, *, audio=False):
    """Save the original response bytes; publish only a complete validated file."""
    file_path = Path(file_path)
    descriptor, temporary = tempfile.mkstemp(prefix=file_path.name + ".", suffix=".part", dir=file_path.parent)
    partial = Path(temporary)
    os.close(descriptor)
    response = None
    try:
        response = request(
            url, stream=True, headers={**HEADERS, "Accept-Encoding": "identity"},
        )
        if response.status_code != 200:
            raise ValueError(f"Expected a complete download, received HTTP {response.status_code}")
        size = 0
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    handle.write(chunk)
                    size += len(chunk)
        if not size:
            raise ValueError("The download is empty")
        length = response.headers.get("content-length")
        encoding = response.headers.get("content-encoding", "identity").lower()
        if length and encoding in ("identity", "") and size != int(length):
            raise ValueError(f"Incomplete download: expected {length} bytes, received {size}")
        if audio:
            validate_audio(partial)
        partial.replace(file_path)
        logging.info("Saved %s (%s bytes)", file_path.name, f"{size:,}")
        return size
    except Exception as error:
        # HTTP client exceptions can contain a signed CDN URL. The CLI prints
        # download errors, so expose only a safe type instead of private URLs.
        raise RuntimeError("File transfer failed (" + type(error).__name__ + ")") from None
    finally:
        if response is not None:
            response.close()
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            # Cleanup must not mask a transfer error or touch another writer.
            pass
