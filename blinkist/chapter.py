import logging
import re
from pathlib import Path  # typing only

from .common import api_request_web, download


class Chapter:
    def __init__(self, chapter_data: dict):
        self.data = chapter_data

        # pylint: disable=C0103
        self.id = chapter_data['id']

    @staticmethod
    def from_id(book, chapter_id) -> 'Chapter':
        # NOTE: Legacy REST endpoint, removed by Blinkist in 2026 (returns 404).
        # Kept for reference; content now comes via Chapter.from_transcript_section().
        chapter_data = api_request_web(f'books/{book.id}/chapters/{chapter_id}')
        return Chapter(chapter_data)

    @staticmethod
    def from_transcript_sections(sections: list, order_no: int) -> 'Chapter':
        """
        Builds a Chapter from a group of reader-transcript sections
        (https://api.blinkist.com/transcripts/{book_id}?language={locale}).

        A chapter starts at a section carrying a non-empty `header` component
        (the chapter title); any headerless sections that follow are body
        continuations of that same chapter, so a chapter can span several
        sections. `text` components hold the body (already HTML-wrapped);
        `marker` components are positional anchors with no content and are dropped.
        """
        title = ''
        body_parts = []
        for section in sections:
            for comp in section.get('transcriptComponents', []):
                html = ((comp.get('value') or {}).get('html') or '').strip()
                if not html:
                    continue
                ctype = comp.get('componentType')
                if ctype == 'header' and not title:
                    title = re.sub(r'<[^>]+>', '', html).strip()
                elif ctype == 'text':
                    body_parts.append(html)
        return Chapter({
            'id': f'section-{order_no}',
            'order_no': order_no,
            'action_title': title,
            # Body stays as HTML — download_text_md relies on that (no MD escaping needed).
            'text': '\n\n'.join(body_parts),
        })

    def serialize(self) -> dict:
        """
        Serializes the chapter to a dict.
        """
        return self.data

    def download_audio(self, target_dir: Path) -> None:
        if not self.data.get('signed_audio_url'):
            # NOTE: In books where is_audio is true, every chapter should have audio, so this should never happen.
            logging.warning(f'No audio for chapter {self.id}')
            return

        file_path = target_dir / f"chapter_{self.data['order_no']}.m4a"

        assert 'm4a' in self.data['signed_audio_url']
        download(self.data['signed_audio_url'], file_path)
