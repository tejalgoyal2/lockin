"""Cluster = software | data | ml | security. The title decides first, JD terms second (SPEC §5)."""
import re

DEFAULT = "software"


class ClusterResolver:
    def __init__(self, cfg: dict):
        c = cfg["cluster"]
        self.default = c.get("default", DEFAULT)
        self.rules: list[tuple[str, list[re.Pattern]]] = []   # first matching cluster wins
        for name, spec in c["title_keywords"].items():
            pats = [re.compile(r"\b" + re.escape(t), re.IGNORECASE) for t in spec.get("terms", [])]
            pats += [re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE) for w in spec.get("words", [])]
            self.rules.append((name, pats))

    def from_title(self, title: str) -> str | None:
        for name, pats in self.rules:
            if any(p.search(title or "") for p in pats):
                return name
        return None

    def resolve(self, title: str, jd_cluster: str | None) -> str:
        """Title keyword, else the cluster with the most matched JD terms, else the default."""
        return self.from_title(title) or jd_cluster or self.default
