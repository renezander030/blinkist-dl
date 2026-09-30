"""Offline checks for export scoping, session isolation and safe denial errors."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from blinkist import auth, common


class CookieExports(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "cookies.txt"

    def tearDown(self):
        self.directory.cleanup()

    def export(self, content):
        self.path.write_text(content, encoding="utf-8")
        return auth.load_cookies(self.path)

    def test_netscape_scopes_flags_and_expiration_are_preserved(self):
        future = int(time.time()) + 3600
        jar = self.export(
            "# Netscape HTTP Cookie File\n"
            f"#HttpOnly_.blinkist.com\tTRUE\t/\tTRUE\t{future}\tsession\tprivate-value\n"
            "www.blinkist.com\tFALSE\t/api\tFALSE\t0\thost\tlocal-value\n"
            "evilblinkist.com\tTRUE\t/\tTRUE\t0\tevil\tignore\n"
            "blinkist.com.evil.test\tTRUE\t/\tTRUE\t0\tevil\tignore\n"
            "api.blinkist.com\tFALSE\t/\tTRUE\t1\texpired\tignore\n"
        )
        cookies = {cookie.name: cookie for cookie in jar}
        self.assertEqual(set(cookies), {"session", "host"})
        self.assertEqual(cookies["session"].domain, ".blinkist.com")
        self.assertTrue(cookies["session"].domain_specified)
        self.assertTrue(cookies["session"].secure)
        self.assertTrue(cookies["session"].has_nonstandard_attr("HttpOnly"))
        self.assertEqual(cookies["session"].expires, future)
        self.assertEqual(cookies["host"].path, "/api")
        self.assertFalse(cookies["host"].domain_specified)
        self.assertIsNone(cookies["host"].expires)

    def test_json_wrapper_accepts_blinkist_subdomains_and_session_cookies(self):
        jar = self.export(json.dumps({"cookies": [
            {"domain": ".Blinkist.com", "name": "root", "value": "one", "secure": True},
            {"domain": "api.blinkist.com", "name": "api", "value": "two", "hostOnly": True,
             "session": True, "expirationDate": -1},
            {"domain": ".a.b.blinkist.com", "name": "nested", "value": "three"},
            {"domain": "blinkist.com.attacker.test", "name": "wrong", "value": "ignore"},
            {"domain": "..blinkist.com", "name": "wrong", "value": "ignore"},
            {"domain": "https://blinkist.com", "name": "wrong", "value": "ignore"},
            {"domain": "blinkist.com", "name": "expired", "value": "ignore", "expires": -1},
        ]}))
        cookies = {cookie.name: cookie for cookie in jar}
        self.assertEqual(set(cookies), {"root", "api", "nested"})
        self.assertEqual(cookies["root"].domain, ".blinkist.com")
        self.assertFalse(cookies["api"].domain_specified)
        self.assertIsNone(cookies["api"].expires)

    def test_unsupported_and_malformed_formats_never_echo_values(self):
        secret = "DO-NOT-PRINT-COOKIE-SECRET"
        invalid = [
            secret,
            json.dumps({"session": secret}),
            json.dumps([{"domain": ".blinkist.com", "name": "session", "value": secret + "\n"}]),
            json.dumps([{"domain": "blinkist.com", "name": "session", "value": secret,
                         "expirationDate": secret}]),
        ]
        for content in invalid:
            with self.subTest(content_type=content[:1]):
                with self.assertNoLogs():
                    with self.assertRaises(auth.CookieFileError) as failure:
                        self.export(content)
                self.assertNotIn(secret, str(failure.exception))
                self.assertTrue(failure.exception.__suppress_context__ or failure.exception.__context__ is None)

    def test_expired_or_unrelated_only_exports_fail_clearly(self):
        for domain, expires in (("unrelated.test", 0), (".blinkist.com", 1)):
            with self.subTest(domain=domain):
                with self.assertRaisesRegex(auth.CookieFileError, "No unexpired Blinkist cookies"):
                    self.export(f"{domain}\tTRUE\t/\tTRUE\t{expires}\tsession\tsecret\n")

    def test_missing_file_diagnostic_does_not_echo_private_path(self):
        with self.assertRaisesRegex(auth.CookieFileError, "cannot be read") as failure:
            auth.load_cookies(self.path.with_name("PRIVATE-FILENAME"))
        self.assertNotIn("PRIVATE-FILENAME", str(failure.exception))


class Sessions(unittest.TestCase):
    def setUp(self):
        auth._session_state.__dict__.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "cookies.json"
        self.path.write_text(json.dumps([
            {"domain": ".blinkist.com", "name": "session", "value": "private-value"}
        ]), encoding="utf-8")
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()

    def tearDown(self):
        auth._session_state.__dict__.clear()
        self.environment.stop()
        self.directory.cleanup()

    def test_lazy_cache_switches_guest_file_revision_and_guest_again(self):
        sessions = [Mock() for _ in range(4)]
        with patch("blinkist.auth.curl_requests.Session", side_effect=sessions) as create:
            self.assertEqual(create.call_count, 0)
            self.assertIs(auth.get_session(), sessions[0])
            self.assertIs(auth.get_session(), sessions[0])
            os.environ["BLINKIST_COOKIES_FILE"] = str(self.path)
            self.assertIs(auth.get_session(), sessions[1])
            self.assertIs(auth.get_session(), sessions[1])
            self.assertEqual(len(list(create.call_args.kwargs["cookies"])), 1)
            previous = self.path.stat()
            os.utime(self.path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
            self.assertIs(auth.get_session(), sessions[2])
            del os.environ["BLINKIST_COOKIES_FILE"]
            self.assertIs(auth.get_session(), sessions[3])
        for session in sessions[:3]:
            session.close.assert_called_once()
        self.assertIsNone(create.call_args.kwargs["cookies"])
        self.assertEqual(create.call_args.kwargs["impersonate"], "chrome")

    def test_workers_do_not_share_sessions_or_cookie_jars(self):
        os.environ["BLINKIST_COOKIES_FILE"] = str(self.path)
        results = []
        jars = []

        def create(**kwargs):
            jars.append(kwargs["cookies"])
            return object()

        def worker():
            first = auth.get_session()
            results.append((first, auth.get_session()))

        with patch("blinkist.auth.curl_requests.Session", side_effect=create):
            threads = [threading.Thread(target=worker) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(len(results), 4)
        self.assertEqual(len({id(first) for first, _ in results}), 4)
        self.assertTrue(all(first is second for first, second in results))
        self.assertEqual(len({id(jar) for jar in jars}), 4)

    def test_selected_missing_file_never_silently_uses_guest_session(self):
        os.environ["BLINKIST_COOKIES_FILE"] = str(self.path.with_name("missing"))
        with patch("blinkist.auth.curl_requests.Session") as create:
            with self.assertRaises(auth.CookieFileError):
                auth.get_session()
            create.assert_not_called()


class Denials(unittest.TestCase):
    class Response:
        def __init__(self, status, payload):
            self.status_code = status
            self.payload = payload
            self.closed = False

        def json(self):
            assert not self.closed
            return self.payload

        def iter_content(self):
            assert not self.closed
            data = json.dumps(self.payload).encode()
            yield data[:8]
            yield data[8:]

        def close(self):
            self.closed = True

    def denial(self, response, stream=False):
        session = Mock()
        session.get.return_value = response
        with patch("blinkist.common.get_session", return_value=session):
            with self.assertNoLogs():
                with self.assertRaises(common.AccessDenied) as failure:
                    common.request("https://api.blinkist.com/v4/audio/test", stream=stream)
        self.assertTrue(response.closed)
        session.get.assert_called_once()
        return failure.exception

    def test_premium_denial_is_parsed_before_stream_close_without_secret_echo(self):
        error = self.denial(self.Response(403, {
            "error": "not_premium", "error_description": "DO-NOT-PRINT-SECRET"
        }), stream=True)
        self.assertEqual(error.status_code, 403)
        self.assertEqual(error.error_code, "not_premium")
        self.assertIn("Premium subscription", str(error))
        self.assertNotIn("DO-NOT-PRINT-SECRET", str(error))

    def test_login_denial_and_unknown_server_messages_are_safe(self):
        error = self.denial(self.Response(401, {"error": "DO-NOT-PRINT-SECRET"}))
        self.assertEqual(error.status_code, 401)
        self.assertEqual(error.error_code, "login_required")
        self.assertNotIn("DO-NOT-PRINT-SECRET", str(error))


if __name__ == "__main__":
    unittest.main()
