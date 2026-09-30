"""Authenticate the current reader API from an explicitly saved Blinkist session."""
import re
import threading
import time
from urllib.parse import urlsplit

from .common import AccessDenied, request

class ReaderAccessError(RuntimeError):
    pass


def audio_redirect(response, identifier, extension, language):
    """Validate the exact full-recording destination before a CDN request."""
    target = response.headers.get("location", "")
    parsed = urlsplit(target)
    expected = f"/content/{identifier}/{language}/" + ("default/main.m3u8" if extension == "m3u8" else "default.m4a")
    if (response.status_code not in (301, 302, 303, 307, 308) or parsed.scheme != "https"
            or parsed.hostname != "hls-audio.blnkstcdn.com" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment or parsed.path != expected):
        raise ReaderAccessError("Reader audio response did not identify the verified book's full CDN source")
    return target


class ReaderClient:
    def __init__(self, request_function=request):
        self.request = request_function
        self._jwt = None
        self._expires = 0
        self._lock = threading.Lock()

    def token(self, refresh=False):
        with self._lock:
            if not refresh and self._jwt is not None and time.monotonic() < self._expires:
                return self._jwt
            response = self.request("https://www.blinkist.com/api/mickey_mouse/setup",
                                    params={"locale": "en", "url": "https://www.blinkist.com/reader/"})
            try:
                payload = response.json()
            finally:
                response.close()
            token = payload.get("jwt") if isinstance(payload, dict) else None
            if (not isinstance(token, str) or len(token) > 65536 or len(token.split(".")) != 3
                    or payload.get("guest") is not False or payload.get("user_id") is None):
                raise ReaderAccessError("Saved Blinkist session did not authenticate the reader")
            self._jwt = token
            self._expires = time.monotonic() + 20 * 60
            return token

    def resolve(self, identifier, extension="m3u8", language="en"):
        if (not re.fullmatch(r"[a-f0-9]{24}", str(identifier)) or extension not in ("m3u8", "m4a")
                or language not in ("en", "de", "es", "pt")):
            raise ValueError("Reader audio requires a verified book ID, language and supported format")
        response = None
        for attempt in range(2):
            token = self.token(refresh=attempt > 0)
            try:
                response = self.request(f"https://api.blinkist.com/v4/audio/{identifier}.{extension}",
                                        params={"language": language}, headers={"Authorization": "Bearer " + token},
                                        allow_redirects=False)
                break
            except AccessDenied:
                if attempt:
                    raise
        try:
            return audio_redirect(response, identifier, extension, language)
        finally:
            response.close()

    def transcript(self, identifier, language="en"):
        if not re.fullmatch(r"[a-f0-9]{24}", str(identifier)) or language not in ("en", "de", "es", "pt"):
            raise ValueError("Transcript requires a verified book ID and language")
        for attempt in range(2):
            token = self.token(refresh=attempt > 0)
            try:
                response = self.request(f"https://api.blinkist.com/transcripts/{identifier}",
                                        params={"language": language}, headers={"Authorization": "Bearer " + token},
                                        allow_redirects=False)
                break
            except AccessDenied:
                if attempt:
                    raise
        try:
            if response.status_code != 200:
                raise ReaderAccessError("Reader did not return the requested transcript")
            payload = response.json()
            transcript = payload.get("transcript") if isinstance(payload, dict) else None
            if not isinstance(transcript, dict) or not transcript.get("transcriptSections"):
                raise ReaderAccessError("Reader returned no complete transcript sections")
            return transcript
        finally:
            response.close()
