from functools import cached_property
from pathlib import Path  # typing only
from typing import List

import yaml

from .chapter import Chapter
from .common import api_request_web, download, request
from .config import BASE_URL, FILENAME_COVER, FILENAME_RAW, FILENAME_TEXT
from .console import track


def _section_has_header(section: dict) -> bool:
    """True if a transcript section starts a new chapter (has a non-empty header)."""
    for comp in section.get('transcriptComponents', []):
        if comp.get('componentType') == 'header' and \
                ((comp.get('value') or {}).get('html') or '').strip():
            return True
    return False


class Book:
    def __init__(self, book_data: dict) -> None:
        self.data = book_data

        # pylint: disable=C0103
        self.id = book_data['id']
        self.language = book_data['language']
        self.slug = book_data['slug']
        self.title = book_data['title']
        self.is_audio: bool = book_data['isAudio']

    def __repr__(self) -> str:
        return f"Book <{self.title}>"

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, o) -> bool:
        return isinstance(o, Book) and self.id == o.id

    @staticmethod
    def from_slug(slug: str) -> 'Book':
        """
        Loads a book from the API by its slug.
        """
        return Book(api_request_web(f'books/{slug}'))

    @cached_property
    def transcript(self) -> dict:
        """
        The reader transcript — Blinkist's content source since the 2026 API
        migration (the old REST chapter endpoints now return 404). Free dailies
        are readable by guests, so no auth token is required.
        """
        url = f'https://api.blinkist.com/transcripts/{self.id}?language={self.language}'
        transcript = request(url).json().get('transcript') or {}
        sections = transcript.get('transcriptSections') or []
        if not sections:
            raise RuntimeError(
                f"No transcript sections for “{self.title}” ({self.slug}). "
                f"Book may not be accessible to guests."
            )
        return transcript

    @cached_property
    def chapter_list(self) -> List[dict]:
        """
        Returns the raw transcript sections (one per chapter). Kept under the
        original name so the download orchestration stays unchanged.
        """
        return self.transcript['transcriptSections']

    @cached_property
    def chapters(self) -> List[Chapter]:
        """
        Returns a list of Chapter objects, which contain the actual content.

        Transcript sections don't map 1:1 to chapters: a section with a
        non-empty `header` component starts a new chapter, and any headerless
        sections after it are continuations. So we group sections by header
        before building each Chapter. Shows a progress bar while building.
        """
        groups: List[List[dict]] = []
        for section in self.chapter_list:
            if not groups or _section_has_header(section):
                groups.append([section])
            else:
                groups[-1].append(section)
        return [
            Chapter.from_transcript_sections(group, order_no)
            for order_no, group in enumerate(
                track(groups, description="Fetching chapters…")
            )
        ]

    def download_cover(self, target_dir: Path) -> None:
        """
        Downloads the cover image to the given directory,
        in the highest resolution available.
        """
        # find the URL of the largest version
        urls = set()
        for source in self.data['image']['sources']:
            urls.add(source['src'])
            urls |= set(source['srcset'].values())
        # example: 'https://images.blinkist.io/images/books/617be9b56cee07000723559e/1_1/470.jpg' → 470
        url = sorted(urls, key=lambda x: int(x.split('/')[-1].rstrip('.jpg')), reverse=True)[0]

        file_path = target_dir / f"{FILENAME_COVER}.jpg"

        assert url.endswith('.jpg')
        download(url, file_path)

    def download_text_md(self, target_dir: Path) -> None:
        """
        Downloads the text content as Markdown to the given directory.
        """
        def md_section(level: int, title: str, text: str) -> str:
            return f"{'#' * level} {title}\n\n{text}"

        parts = [
            f"# {self.data['title']} – *{self.data['subtitle']}*",
            f"_{self.data['author']}_",
            f"Reading time: {self.data['minutesToRead']} minutes",
            # TODO: It's hard to ensure that the cover image is reasonably scaled.
            # One possibility is <img src="cover.jpg" alt="cover" width="256" />
            # I'm leaving it out for now.
            # f"![cover]({cover_path})" if cover_path else "",

            md_section(3, "Synopsis", self.data['aboutTheBook']),
            # @ptrstn's version also has “Who is it for?” ('for_who')
            # @ptrstn's version also has “About the author” ('about_author')

            "---",

            *[
                md_section(
                    2,
                    (
                        # "Was ist drin für dich:" / "Fazit" → keine Blink-Nummer
                        f"Blink {number} - {chapter.data['action_title']}"
                        if number not in [0, len(self.chapters) - 1]
                        else chapter.data['action_title']
                    ),
                    # NOTE: Since the chapter text is wrapped in HTML tags,
                    # we don't need to escape Markdown special characters.
                    chapter.data['text']
                )
                for number, chapter in enumerate(self.chapters)
            ],

            "---",

            f"Source: {BASE_URL + self.data['url']}",
        ]

        markdown_text = "\n\n\n".join(parts)

        file_path = target_dir / f"{FILENAME_TEXT}.md"
        file_path.write_text(markdown_text, encoding='utf-8')

    def serialize(self) -> dict:
        """
        Serializes the book (including its complete chapters) to a dict.
        """
        return {
            **self.data,
            'chapters': [
                chapter.serialize()
                for chapter in self.chapters
            ],
        }

    def download_raw_yaml(self, target_dir: Path) -> None:
        """
        Downloads the raw YAML to the given directory.
        """
        file_path = target_dir / f"{FILENAME_RAW}.yaml"
        file_path.write_text(
            yaml.dump(
                self.serialize(),
                default_flow_style=False,
                allow_unicode=True,
            ),
            encoding='utf-8',
        )
