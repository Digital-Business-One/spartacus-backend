"""LinkPreviewService — fetches Open Graph metadata from URLs (RFC-11)."""

from html.parser import HTMLParser
from urllib.request import Request, urlopen

from app.logging.decorator import log


class _OGParser(HTMLParser):
    """Minimal HTML parser that extracts Open Graph meta tags."""

    def __init__(self):
        super().__init__()
        self.og: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            attrs_dict = dict(attrs)
            prop = attrs_dict.get("property", "")
            content = attrs_dict.get("content", "")
            if prop.startswith("og:") and content:
                self.og[prop] = content

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False


class LinkPreviewService:

    @log
    def fetch(self, url: str) -> dict:
        """Fetch Open Graph metadata from a URL.

        Returns dict with keys: url, title, image, description.
        Falls back gracefully on any error.
        """
        try:
            req = Request(url, headers={"User-Agent": "SpartacusBot/1.0"})
            with urlopen(req, timeout=5) as resp:
                # Read only first 50KB to avoid large pages
                html = resp.read(50_000).decode("utf-8", errors="ignore")
        except Exception:
            return {"url": url, "title": "", "image": "", "description": ""}

        parser = _OGParser()
        try:
            parser.feed(html)
        except Exception:
            pass

        og = parser.og
        return {
            "url": url,
            "title": og.get("og:title", parser.title.strip()),
            "image": og.get("og:image", ""),
            "description": og.get("og:description", ""),
        }
