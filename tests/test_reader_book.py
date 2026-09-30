"""Offline coverage for authenticated complete-book media integration."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from blinkist.book import Book
from blinkist import common
import main

ID = "1234567890abcdef12345678"

def book():
    return Book({"id": ID, "slug": "example-en", "title": "Example", "language": "en", "isAudio": True})

class ReaderBookChecks(unittest.TestCase):
    @patch.dict(os.environ, {"BLINKIST_COOKIES_FILE": "synthetic-cookies.json"})
    def test_paid_transcript_and_audio_share_a_reader_client(self):
        reader = Mock()
        transcript = {"transcriptSections": [{"transcriptComponents": []}]}
        reader.transcript.return_value = transcript
        reader.resolve.return_value = "https://hls-audio.blnkstcdn.com/synthetic"
        with patch("blinkist.book.ReaderClient", return_value=reader), patch("blinkist.book.download") as download:
            selected = book()
            self.assertEqual(selected.transcript, transcript)
            selected.download_audio(Path("output"))
            reader.transcript.assert_called_once_with(ID, "en")
            reader.resolve.assert_called_once_with(ID, extension="m4a", language="en")
            download.assert_called_once_with(reader.resolve.return_value, Path("output/book.m4a"), audio=True)

    def test_audio_only_does_not_request_transcripts_or_chapters(self):
        selected = Mock(id=ID, slug="example-en", title="Example", is_audio=True)
        with tempfile.TemporaryDirectory() as temporary:
            main.download_book(selected, "en", Path(temporary), yaml=False, markdown=False, cover=False)
        selected.download_audio.assert_called_once()
        selected.download_raw_yaml.assert_not_called()
        selected.download_text_md.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    def test_free_audio_uses_validated_redirect_and_separate_download(self):
        source = f"https://hls-audio.blnkstcdn.com/content/{ID}/en/default.m4a?verify=synthetic"
        response = Mock(status_code=302, headers={"location": source})
        with patch("blinkist.book.request", return_value=response) as request, patch("blinkist.book.download") as download:
            book().download_audio(Path("output"))
        request.assert_called_once_with(f"https://api.blinkist.com/v4/audio/{ID}.m4a",
                                        params={"language": "en"}, allow_redirects=False)
        download.assert_called_once_with(source, Path("output/book.m4a"), audio=True)
        response.close.assert_called_once()

    @patch.dict(os.environ, {}, clear=True)
    def test_free_audio_rejects_preview_and_closes_response(self):
        response = Mock(status_code=302, headers={"location": "https://example.org/preview.m4a"})
        with patch("blinkist.book.request", return_value=response), patch("blinkist.book.download") as download:
            with self.assertRaises(RuntimeError):
                book().download_audio(Path("output"))
        download.assert_not_called()
        response.close.assert_called_once()

    def test_complete_transfer_preserves_bytes_and_closes_response(self):
        body = b"synthetic container bytes"
        response = Mock(status_code=200, headers={"content-length": str(len(body))})
        response.iter_content.return_value = iter((body[:7], body[7:]))
        with tempfile.TemporaryDirectory() as temporary, patch.object(common, "request", return_value=response):
            target = Path(temporary) / "book.m4a"
            self.assertEqual(common.download("https://example.org/audio", target), len(body))
            self.assertEqual(target.read_bytes(), body)
            self.assertEqual(list(Path(temporary).iterdir()), [target])
        response.close.assert_called_once()

    def test_interrupted_transfer_preserves_existing_destination(self):
        response = Mock(status_code=200, headers={"content-length": "100"})
        response.iter_content.return_value = iter((b"incomplete",))
        with tempfile.TemporaryDirectory() as temporary, patch.object(common, "request", return_value=response):
            target = Path(temporary) / "book.m4a"
            target.write_bytes(b"prior complete file")
            with self.assertRaises(RuntimeError):
                common.download("https://example.org/audio", target)
            self.assertEqual(target.read_bytes(), b"prior complete file")
            self.assertEqual(list(Path(temporary).iterdir()), [target])
        response.close.assert_called_once()

    def test_error_page_is_not_published_as_audio(self):
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = iter((b"<html>not audio</html>",))
        with tempfile.TemporaryDirectory() as temporary, patch.object(common, "request", return_value=response):
            target = Path(temporary) / "book.m4a"
            with self.assertRaises(RuntimeError):
                common.download("https://example.org/audio", target, audio=True)
            self.assertFalse(target.exists())
            self.assertFalse(list(Path(temporary).iterdir()))
        response.close.assert_called_once()

    def test_download_error_does_not_disclose_signed_source(self):
        private = "https://hls-audio.blnkstcdn.com/source?verify=synthetic-private"
        with tempfile.TemporaryDirectory() as temporary, patch.object(common, "request", side_effect=RuntimeError(private)):
            with self.assertRaises(RuntimeError) as caught:
                common.download(private, Path(temporary) / "book.m4a", audio=True)
        self.assertNotIn("synthetic-private", str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))

if __name__ == "__main__":
    unittest.main()
