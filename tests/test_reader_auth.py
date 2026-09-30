"""Current-reader authentication, refresh, identity and credential scope."""
import unittest
from unittest.mock import Mock

from blinkist.common import AccessDenied
from blinkist.reader_auth import ReaderClient, ReaderAccessError

ID = "1234567890abcdef12345678"
TOKEN = "synthetic.private.token"
SOURCE = f"https://hls-audio.blnkstcdn.com/content/{ID}/en/default/main.m3u8?verify=synthetic"


def setup(token=TOKEN, **changes):
    return Mock(json=Mock(return_value={"jwt": token, "guest": False, "user_id": "example", **changes}))


def redirect(source=SOURCE):
    return Mock(status_code=302, headers={"location": source})


class ReaderChecks(unittest.TestCase):
    def test_cookie_setup_and_bearer_audio_do_not_follow_cdn_redirect(self):
        request = Mock(side_effect=[setup(), redirect(), redirect()])
        client = ReaderClient(request)
        self.assertEqual(client.resolve(ID), SOURCE)
        self.assertEqual(client.resolve(ID), SOURCE)
        self.assertEqual(request.call_count, 3)
        self.assertNotIn("headers", request.call_args_list[0].kwargs)
        for call in request.call_args_list[1:]:
            self.assertTrue(call.args[0].startswith("https://api.blinkist.com/v4/audio/"))
            self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer " + TOKEN})
            self.assertIs(call.kwargs["allow_redirects"], False)

    def test_denied_api_refreshes_once_and_retries_with_new_token(self):
        request = Mock(side_effect=[setup(), AccessDenied(401, "login_required"), setup("new.private.token"), redirect()])
        self.assertEqual(ReaderClient(request).resolve(ID), SOURCE)
        self.assertEqual(request.call_args_list[-1].kwargs["headers"]["Authorization"], "Bearer new.private.token")

    def test_repeated_denial_is_not_bypassed(self):
        request = Mock(side_effect=[setup(), AccessDenied(403, "forbidden"), setup(), AccessDenied(403, "forbidden")])
        with self.assertRaises(AccessDenied):
            ReaderClient(request).resolve(ID)
        self.assertEqual(request.call_count, 4)

    def test_guest_and_invalid_setup_are_rejected_without_disclosing_token(self):
        for changes in ({"guest": True}, {"user_id": None}, {"jwt": "invalid"}):
            with self.subTest(changes=changes), self.assertRaises(ReaderAccessError) as error:
                ReaderClient(Mock(return_value=setup(**changes))).token()
            self.assertNotIn(TOKEN, str(error.exception))

    def test_foreign_cross_book_preview_and_insecure_sources_are_rejected(self):
        for source in (SOURCE.replace("hls-audio.blnkstcdn.com", "attacker.test"),
                       SOURCE.replace(ID, "f" * 24), SOURCE.replace("/default/", "/preview/"),
                       SOURCE.replace("https:", "http:"), SOURCE + "#fragment"):
            response = redirect(source)
            with self.subTest(source=source), self.assertRaises(ReaderAccessError) as error:
                ReaderClient(Mock(side_effect=[setup(), response])).resolve(ID)
            self.assertNotIn("synthetic", str(error.exception))
            response.close.assert_called_once()

    def test_invalid_id_is_rejected_before_any_request(self):
        request = Mock()
        with self.assertRaises(ValueError):
            ReaderClient(request).resolve("../another-book")
        request.assert_not_called()

    def test_language_matches_both_request_and_redirect(self):
        for language in ("de", "es", "pt"):
            target = SOURCE.replace("/en/", "/" + language + "/")
            request = Mock(side_effect=[setup(), redirect(target)])
            self.assertEqual(ReaderClient(request).resolve(ID, language=language), target)
            self.assertEqual(request.call_args.kwargs["params"], {"language": language})
            with self.assertRaises(ReaderAccessError):
                ReaderClient(Mock(side_effect=[setup(), redirect(SOURCE)])).resolve(ID, language=language)

    def test_transcript_refreshes_denied_access_and_closes_response(self):
        document = {"transcriptSections": [{"transcriptComponents": []}]}
        response = Mock(status_code=200, json=Mock(return_value={"transcript": document}))
        request = Mock(side_effect=[setup(), AccessDenied(401, "login_required"), setup(), response])
        self.assertEqual(ReaderClient(request).transcript(ID, "de"), document)
        self.assertEqual(request.call_args.kwargs["params"], {"language": "de"})
        response.close.assert_called_once()

    def test_invalid_language_and_empty_transcript_are_rejected(self):
        request = Mock()
        with self.assertRaises(ValueError):
            ReaderClient(request).resolve(ID, language="../en")
        request.assert_not_called()
        response = Mock(status_code=200, json=Mock(return_value={"transcript": {}}))
        with self.assertRaises(ReaderAccessError):
            ReaderClient(Mock(side_effect=[setup(), response])).transcript(ID)
        response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
