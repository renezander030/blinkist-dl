"""Load an explicitly supplied Blinkist cookie export into isolated sessions."""
import json
import math
import os
import re
import threading
import time
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

from curl_cffi import requests as curl_requests
from curl_cffi.const import CurlOpt


class CookieFileError(ValueError):
    """A safe diagnostic which never contains exported cookie values."""


_session_state = threading.local()
_domain_pattern = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\Z")
_name_pattern = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def get_cdn_session():
    """A cookie-free CDN client tolerating S3's stale aws-chunked metadata.

    The CDN serves already decoded files with a residual Content-Encoding:
    aws-chunked header on some objects. Request identity bytes and disable
    libcurl's content decoder; playlist, size and audio checks remain required.
    """
    session = getattr(_session_state, "cdn_session", None)
    if session is None:
        session = curl_requests.Session(impersonate="chrome", headers={"Accept-Encoding": "identity"},
                                        curl_options={CurlOpt.HTTP_CONTENT_DECODING: 0})
        _session_state.cdn_session = session
    return session


def _allowed_domain(value):
    if not isinstance(value, str):
        return None
    domain = value.lower()
    host = domain[1:] if domain.startswith(".") else domain
    if not _domain_pattern.fullmatch(host):
        return None
    if host != "blinkist.com" and not host.endswith(".blinkist.com"):
        return None
    return domain


def _expiry(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise CookieFileError("The cookie export contains an invalid expiration time")
    try:
        seconds = float(value)
        if not math.isfinite(seconds):
            raise ValueError
        return int(seconds) if seconds != 0 else None
    except (ValueError, TypeError, OverflowError):
        raise CookieFileError("The cookie export contains an invalid expiration time") from None


def _cookie(domain, name, value, path="/", secure=False, host_only=False,
            expires=None, http_only=False):
    if (not isinstance(name, str) or not _name_pattern.fullmatch(name)
            or not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or not isinstance(path, str) or not path.startswith("/")
            or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        raise CookieFileError("The cookie export contains an invalid Blinkist cookie")
    return Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=not host_only,
        domain_initial_dot=domain.startswith("."), path=path,
        path_specified=True, secure=secure, expires=expires,
        discard=expires is None, comment=None, comment_url=None,
        rest={"HttpOnly": None} if http_only else {}, rfc2109=False,
    )


def _json_cookies(text):
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise CookieFileError("The JSON cookie export is invalid") from None
    if isinstance(data, dict):
        data = data.get("cookies")
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise CookieFileError("Use a JSON array of exported cookies or a cookies array wrapper")
    for item in data:
        domain = _allowed_domain(item.get("domain"))
        if domain is None:
            continue
        for flag in ("secure", "hostOnly", "httpOnly", "session"):
            if flag in item and not isinstance(item[flag], bool):
                raise CookieFileError("The cookie export contains an invalid Blinkist cookie flag")
        expires = None if item.get("session") else _expiry(item.get("expirationDate", item.get("expires")))
        yield _cookie(
            domain, item.get("name"), item.get("value"), item.get("path", "/"),
            secure=item.get("secure", False),
            host_only=item.get("hostOnly", not domain.startswith(".")),
            expires=expires, http_only=item.get("httpOnly", False),
        )


def _netscape_cookies(text):
    for line in text.splitlines():
        http_only = line.startswith("#HttpOnly_")
        if not line or (line.startswith("#") and not http_only):
            continue
        if http_only:
            line = line[len("#HttpOnly_"):]
        fields = line.split("\t")
        if len(fields) != 7:
            raise CookieFileError("Use a Netscape cookie export with seven tab-separated fields")
        domain, subdomains, path, secure, expires, name, value = fields
        domain = _allowed_domain(domain)
        if domain is None:
            continue
        if subdomains not in ("TRUE", "FALSE") or secure not in ("TRUE", "FALSE"):
            raise CookieFileError("The Netscape cookie export contains invalid cookie flags")
        yield _cookie(
            domain, name, value, path, secure=secure == "TRUE",
            host_only=subdomains == "FALSE", expires=_expiry(expires), http_only=http_only,
        )


def load_cookies(path):
    """Read only the selected export; discard other domains and expired cookies."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise CookieFileError("The selected cookie export cannot be read") from None
    parse = _json_cookies if text.lstrip().startswith(("[", "{")) else _netscape_cookies
    jar = CookieJar()
    now = time.time()
    for cookie in parse(text):
        if cookie.expires is None or cookie.expires > now:
            jar.set_cookie(cookie)
    if not list(jar):
        raise CookieFileError("No unexpired Blinkist cookies were found in the export")
    return jar


def get_session():
    """Each worker owns its session; changing the export switches session state."""
    selected = os.environ.get("BLINKIST_COOKIES_FILE")
    path = None
    if selected:
        try:
            path = Path(selected).expanduser().resolve()
            key = (str(path), path.stat().st_mtime_ns)
        except (OSError, ValueError, RuntimeError):
            raise CookieFileError("The selected cookie export cannot be read") from None
    else:
        key = (None, None)
    if getattr(_session_state, "key", None) == key:
        return _session_state.session
    cookies = load_cookies(path) if path is not None else None
    session = curl_requests.Session(impersonate="chrome", cookies=cookies)
    previous = getattr(_session_state, "session", None)
    if previous is not None:
        previous.close()
    _session_state.session = session
    _session_state.key = key
    return session
