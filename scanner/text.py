"""HTML → plain text with paragraph breaks preserved (SPEC §6a)."""
import html
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "ul", "ol", "table", "tr", "section", "article", "header", "footer",
          "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr"}
_SKIP = {"script", "style"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK:
            self.parts.append("\n\n" if tag != "br" else "\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK or tag == "li":
            self.parts.append("\n\n" if tag in _BLOCK and tag != "br" else "\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(re.sub(r"\s+", " ", data))  # HTML source newlines are spaces


def html_to_text(raw: str, *, unescape_first: bool = False) -> str:
    """Convert HTML to text. Set `unescape_first` for HTML-escaped payloads (Greenhouse `content`)."""
    if not raw:
        return ""
    if unescape_first:
        raw = html.unescape(raw)
    parser = _Extractor()
    parser.feed(raw)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # list items sit on single newlines; tidy the blank line a block start leaves before them
    text = re.sub(r"\n\n(- )", r"\n\1", text)
    return text.strip()
