"""A check the page builders' tests share: every table on a generated page is named and can be scrolled
from the keyboard (#184). Not a test file itself: pytest puts tests/ on sys.path, so they `import table_checks`."""

from html.parser import HTMLParser

VOID = {"meta", "link", "br", "hr", "img", "input"}


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack: list[tuple[str, dict]] = []
        self.ids: list[str] = []
        self.tables: list[dict] = []
        self.headings: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append(tag)
        if tag == "table":
            self.tables.append({"wrapper": self.stack[-1] if self.stack else ("", {}), "first": None, "caption": "", "head": []})
        elif self.tables and self.stack and self.stack[-1][0] == "table" and self.tables[-1]["first"] is None:
            self.tables[-1]["first"] = (tag, a)
        if tag == "th" and any(t == "thead" for t, _ in self.stack):
            self.tables[-1]["head"].append(a.get("scope"))
        if tag not in VOID:
            self.stack.append((tag, a))

    def handle_endtag(self, tag):
        while self.stack:  # a missing </p> or </li> doesn't unbalance the rest
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] == "caption":
            self.tables[-1]["caption"] += data


def check_tables(page: str) -> list[str]:
    """Assert every <table> in `page` starts with a non-empty <caption>, sits directly in a .matrix-scroll wrapper
    with tabindex="0", role="region" and an aria-labelledby naming that caption, and has scope="col" on its
    <thead> cells; and that no id is used twice. Returns the captions, in page order."""
    parser = _Tables()
    parser.feed(page)
    parser.close()
    assert len(parser.ids) == len(set(parser.ids)), f"an id is used twice: {sorted(parser.ids)}"
    for table in parser.tables:
        tag, wrapper = table["wrapper"]
        first_tag, first = table["first"] or ("", {})
        assert first_tag == "caption", f"a table starts with <{first_tag}>, not a <caption>"
        assert table["caption"].strip(), "a table's caption is empty"
        assert tag == "div" and wrapper.get("class") == "matrix-scroll", f"{table['caption']!r} isn't in a .matrix-scroll"
        assert wrapper.get("tabindex") == "0", f"{table['caption']!r}: the wrapper can't be focused"
        assert wrapper.get("role") == "region", f"{table['caption']!r}: the wrapper isn't a region"
        assert first.get("id") and wrapper.get("aria-labelledby") == first["id"], (
            f"{table['caption']!r}: the wrapper isn't labelled by the caption"
        )
        assert table["head"] and all(scope == "col" for scope in table["head"]), (
            f"{table['caption']!r}: a <thead> cell has no scope=\"col\""
        )
    return [table["caption"] for table in parser.tables]


def headings(page: str) -> list[str]:
    """The page's heading tags, in order: ["h1", "h2", "h2", "h3", ...]."""
    parser = _Tables()
    parser.feed(page)
    return parser.headings
