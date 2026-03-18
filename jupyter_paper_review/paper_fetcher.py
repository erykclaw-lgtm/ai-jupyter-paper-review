"""Fetch paper content from URLs (arXiv, DOI, generic).

Uses tornado's AsyncHTTPClient (already a dependency) to avoid
relying on Claude Code's WebFetch tool, which has a known bug
with duplicate tool_use IDs.
"""

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional

from tornado.httpclient import AsyncHTTPClient, HTTPRequest

logger = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 80_000  # Truncate very long papers


@dataclass
class PaperContent:
    """Structured paper content."""

    title: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    url: str = ""
    body_text: str = ""
    source: str = ""  # "arxiv", "generic", etc.
    error: str = ""


class _HTMLTextExtractor(HTMLParser):
    """Simple HTML → plain text extractor."""

    def __init__(self):
        super().__init__()
        self._result: list[str] = []
        self._skip = False
        self._skip_tags = {"script", "style", "noscript", "head", "nav", "footer", "header"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip = True
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self._result.append("\n")

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._result.append(data)

    def get_text(self) -> str:
        text = "".join(self._result)
        # Collapse excessive whitespace but keep paragraph breaks
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text."""
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.get_text()


def _extract_arxiv_id(url: str) -> Optional[str]:
    """Extract arXiv ID from a URL like https://arxiv.org/abs/2502.11089."""
    patterns = [
        r"arxiv\.org/abs/(\d+\.\d+(?:v\d+)?)",
        r"arxiv\.org/pdf/(\d+\.\d+(?:v\d+)?)",
        r"arxiv\.org/html/(\d+\.\d+(?:v\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


async def _fetch_url(url: str, **kwargs) -> Optional[str]:
    """Fetch a URL and return its body as text, or None on failure."""
    client = AsyncHTTPClient()
    try:
        request = HTTPRequest(
            url,
            connect_timeout=15,
            request_timeout=30,
            follow_redirects=True,
            max_redirects=5,
            user_agent="Mozilla/5.0 (compatible; PaperReview/1.0)",
            **kwargs,
        )
        response = await client.fetch(request, raise_error=False)
        if response.code == 200:
            body = response.body
            if isinstance(body, bytes):
                # Try to detect encoding from content-type header
                ct = response.headers.get("Content-Type", "")
                encoding = "utf-8"
                if "charset=" in ct:
                    encoding = ct.split("charset=")[-1].split(";")[0].strip()
                try:
                    return body.decode(encoding)
                except (UnicodeDecodeError, LookupError):
                    return body.decode("utf-8", errors="replace")
            return str(body)
        else:
            logger.warning(f"HTTP {response.code} fetching {url}")
            return None
    except Exception as e:
        logger.warning(f"Error fetching {url}: {e}")
        return None


async def _fetch_arxiv(url: str) -> PaperContent:
    """Fetch an arXiv paper using the arXiv API + HTML version."""
    arxiv_id = _extract_arxiv_id(url)
    if not arxiv_id:
        return PaperContent(url=url, error=f"Could not extract arXiv ID from {url}")

    paper = PaperContent(url=url, source="arxiv")

    # Step 1: Fetch metadata via arXiv API
    api_url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    xml_text = await _fetch_url(api_url)

    if xml_text:
        try:
            root = ET.fromstring(xml_text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            entry = root.find("atom:entry", ns)
            if entry is not None:
                title_el = entry.find("atom:title", ns)
                if title_el is not None and title_el.text:
                    paper.title = " ".join(title_el.text.split())

                summary_el = entry.find("atom:summary", ns)
                if summary_el is not None and summary_el.text:
                    paper.abstract = summary_el.text.strip()

                for author_el in entry.findall("atom:author", ns):
                    name_el = author_el.find("atom:name", ns)
                    if name_el is not None and name_el.text:
                        paper.authors.append(name_el.text.strip())
        except ET.ParseError as e:
            logger.warning(f"Failed to parse arXiv API response: {e}")

    # Step 2: Try to fetch the HTML version for full text
    html_url = f"https://arxiv.org/html/{arxiv_id}"
    html_text = await _fetch_url(html_url)

    if html_text:
        body = _html_to_text(html_text)
        if len(body) > 500:  # Only use if we got substantial content
            paper.body_text = body[:MAX_CONTENT_LENGTH]
    else:
        # Fall back to the abstract page
        abs_url = f"https://arxiv.org/abs/{arxiv_id}"
        abs_html = await _fetch_url(abs_url)
        if abs_html:
            paper.body_text = _html_to_text(abs_html)[:MAX_CONTENT_LENGTH]

    if not paper.title and not paper.body_text:
        paper.error = f"Could not retrieve content for arXiv paper {arxiv_id}"

    return paper


async def _fetch_generic(url: str) -> PaperContent:
    """Fetch a generic URL and extract text content."""
    paper = PaperContent(url=url, source="generic")

    html = await _fetch_url(url)
    if not html:
        paper.error = f"Could not fetch content from {url}"
        return paper

    # Try to extract title from <title> tag
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if title_match:
        paper.title = _html_to_text(title_match.group(1)).strip()

    paper.body_text = _html_to_text(html)[:MAX_CONTENT_LENGTH]

    return paper


async def fetch_paper_content(url: str) -> PaperContent:
    """Fetch paper content from a URL.

    Handles arXiv, DOI, and generic URLs.
    Returns a PaperContent dataclass with whatever we could extract.
    """
    url = url.strip()

    # Handle DOI URLs by following the redirect
    if url.startswith("https://doi.org/") or url.startswith("http://doi.org/"):
        return await _fetch_generic(url)

    # arXiv URLs
    if "arxiv.org" in url:
        return await _fetch_arxiv(url)

    # Generic URL
    return await _fetch_generic(url)
