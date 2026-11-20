"""Links from the site to the tool's repo, so a visitor can find (and star) wheelgap: the footer of
the generated pages and the home page's intro. The repo checker's line is in tests/site/check-page.test.mjs."""

import re
from html.parser import HTMLParser
from pathlib import Path

from scripts import build_campaigns, build_drops, build_new_wheels, build_pages

ROOT = Path(__file__).resolve().parent.parent
TOOL = "https://github.com/wheelgap/wheelgap"
DATA = "https://github.com/wheelgap/tracker"


class Links(HTMLParser):
    """Every <a> of a page as (href, text), the text without aria-hidden parts, as a screen reader says it."""

    def __init__(self):
        super().__init__()
        self.links: list[list[str]] = []
        self._open = False
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            self._open = True
            self.links.append([a.get("href", ""), ""])
        elif self._open and (self._hidden or a.get("aria-hidden") == "true"):
            self._hidden += 1

    def handle_endtag(self, tag):
        if tag == "a":
            self._open = False
            self._hidden = 0
        elif self._hidden:
            self._hidden -= 1

    def handle_data(self, data):
        if self._open and not self._hidden:
            self.links[-1][1] += data


def links(markup: str) -> list[tuple[str, str]]:
    parser = Links()
    parser.feed(markup)
    return [(href, " ".join(text.split())) for href, text in parser.links]


def footer(page: str) -> str:
    """The page's one <footer>, which comes after its <main>."""
    assert page.count("<footer") == 1
    assert page.index("</main>") < page.index("<footer")
    return page[page.index("<footer") : page.index("</footer>")]


def test_footer_names_the_checker_and_the_tool_first_then_the_data_repo():
    assert links(build_campaigns.FOOTER) == [
        ("https://wheelgap.github.io/tracker/check/", "in your browser"),
        (TOOL, "wheelgap (GitHub)"),
        (DATA, "wheelgap/tracker"),
    ]
    assert "Check your own project: <a" in build_campaigns.FOOTER
    # Plain links only: nothing the pages' Content-Security-Policy would block, nothing fetched.
    assert not re.search(r"<(img|iframe|script|link)\b", build_campaigns.FOOTER)


def test_package_page_has_the_footer():
    package = {"rank": 1, "name": "numpy", "version": "2.3.1", "downloads": 1, "pure": False, "error": None,
               "results": ["wheel"]}
    scan = {"schema": 1, "generated": "2026-11-16T03:20:00+00:00", "wheelgap": "0.11.0", "summary": {},
            "targets": [{"label": "3.13 linux-x86_64", "python": "3.13", "platform": "linux-x86_64"}],
            "packages": [package]}
    page = build_pages.page(package, scan, "3.13")
    assert footer(page) in build_campaigns.FOOTER
    assert page.index("</footer>") < page.index('<script type="application/ld+json">')


def test_drops_and_new_wheels_pages_have_the_footer():
    for page in (build_drops.page([], 0, set()), build_new_wheels.page([], set())):
        assert footer(page) in build_campaigns.FOOTER
        assert "<script" not in page


def test_home_page_intro_has_one_star_link_to_the_tool():
    home = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    hero = home[home.index('<header class="hero">') : home.index('id="kpi-grid"')]
    star = [link for link in links(home) if re.search(r"\bstar\b", link[1], re.IGNORECASE)]
    # One link, worded for a screen reader too: the ★ is decoration next to the words, not the name.
    assert star == [(TOOL, "Star wheelgap on GitHub")]
    assert star[0] in links(hero)
    assert '<span aria-hidden="true">★</span> Star wheelgap on GitHub</a>' in hero
    assert not re.search(r"<(img|iframe)\b", hero)  # no star-count widget: the CSP allows no remote content
