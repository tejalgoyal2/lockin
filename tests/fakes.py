"""A tiny in-memory Notion: enough of the REST API for the feed code under test."""
import itertools
import json as jsonlib
from datetime import datetime, timezone

from scanner.notion_feed import SIGNAL_OPTIONS, SOURCE_OPTIONS

NAMES = {"name": "Name", "company": "Company", "link": "Link", "source": "Source", "fit": "Fit %",
         "signals": "Signals", "jd": "JD", "interested": "Interested", "apply": "Apply"}


def good_schema():
    def opts(names):
        return {"options": [{"name": n} for n in names]}
    return {"object": "data_source", "id": "ds-1", "properties": {
        "Name": {"id": "title", "type": "title", "title": {}},
        "Company": {"id": "p_company", "type": "rich_text", "rich_text": {}},
        "Link": {"id": "p_link", "type": "url", "url": {}},
        "Source": {"id": "p_source", "type": "select", "select": opts(SOURCE_OPTIONS)},
        "Fit %": {"id": "p_fit", "type": "number", "number": {"format": "percent"}},
        "Signals": {"id": "p_signals", "type": "multi_select", "multi_select": opts(SIGNAL_OPTIONS)},
        "JD": {"id": "p_jd", "type": "rich_text", "rich_text": {}},
        "Interested": {"id": "p_int", "type": "checkbox", "checkbox": {}},
        "Apply": {"id": "p_apply", "type": "checkbox", "checkbox": {}},
    }}


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body if body is not None else {}, headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    @property
    def text(self):
        return jsonlib.dumps(self._body)


class FakeNotion:
    """Acts as the `session` of NotionClient. Stores pages; records every request."""

    def __init__(self, schema=None, rows=()):
        self.schema = schema or good_schema()
        self.pages: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.requests: list[dict] = []
        self._ids = itertools.count(1)
        self.scripted: list[Resp] = []        # responses returned first, in order (for 429/5xx tests)
        for r in rows:
            self.add_row(**r)

    def add_row(self, link, created, interested=False, apply=False, jd=""):
        pid = f"page-{next(self._ids)}"
        self.pages[pid] = {"id": pid, "created_time": created, "in_trash": False,
                           "props": {"Link": link, "Interested": interested, "Apply": apply, "JD": jd}}
        return pid

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        path = url.split("api.notion.com", 1)[1]
        self.calls.append((method, path))
        self.requests.append({"method": method, "path": path, "json": json, "params": params, "headers": headers})
        if self.scripted:
            return self.scripted.pop(0)
        if method == "GET" and path.startswith("/v1/data_sources/"):
            return Resp(body=self.schema)
        if method == "POST" and path.endswith("/query"):
            live = [p for p in self.pages.values() if not p["in_trash"]]
            return Resp(body={"results": [self._row(p) for p in live], "has_more": False, "next_cursor": None})
        if method == "POST" and path == "/v1/pages":
            pid = f"page-{next(self._ids)}"
            props = json["properties"]
            jd = "".join(i["text"]["content"] for i in props["JD"]["rich_text"])
            self.pages[pid] = {"id": pid, "created_time": datetime.now(timezone.utc).isoformat(),
                               "in_trash": False, "created_payload": json,
                               "props": {"Link": props["Link"]["url"], "Interested": False, "Apply": False, "JD": jd}}
            return Resp(body={"object": "page", "id": pid})
        if method == "PATCH" and path.startswith("/v1/pages/"):
            self.pages[path.rsplit("/", 1)[1]]["in_trash"] = json.get("in_trash", False)
            return Resp(body={"object": "page"})
        if method == "GET" and "/properties/" in path:
            pid = path.split("/")[3]
            jd = self.pages[pid]["props"]["JD"]
            size = 2000 * 3                     # three items per page of results, to exercise pagination
            start = int((params or {}).get("start_cursor", 0))
            piece = jd[start:start + size]
            items = [{"object": "property_item", "type": "rich_text",
                      "rich_text": {"type": "text", "text": {"content": piece[i:i + 2000]},
                                    "plain_text": piece[i:i + 2000]}}
                     for i in range(0, len(piece), 2000)]
            more = start + size < len(jd)
            return Resp(body={"object": "list", "results": items, "has_more": more,
                              "next_cursor": str(start + size) if more else None})
        return Resp(404, {"code": "object_not_found", "message": f"no route {method} {path}"})

    @staticmethod
    def _row(p):
        return {"id": p["id"], "created_time": p["created_time"], "properties": {
            "Link": {"type": "url", "url": p["props"]["Link"]},
            "Interested": {"type": "checkbox", "checkbox": p["props"]["Interested"]},
            "Apply": {"type": "checkbox", "checkbox": p["props"]["Apply"]}}}
