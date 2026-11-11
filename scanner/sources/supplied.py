"""Job descriptions a source hands over with its postings (Google for Jobs answers with the whole text).

The source puts the text here while it reads; phase 2 uses it for a job whose own page cannot be read (an aggregator's
link, a 403, a site lockin has no reader for). A job whose link is an ATS lockin reads still gets the ATS text first:
it is the employer's own and carries the locations.
"""
_TEXT: dict[str, str] = {}


def put(url: str, text: str) -> None:
    if url and text and text.strip():
        _TEXT[url] = text.strip()


def get(url: str) -> str:
    return _TEXT.get(url, "")


def clear() -> None:
    _TEXT.clear()
