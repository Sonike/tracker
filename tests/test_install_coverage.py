"""Offline checks for publishing package install coverage to JSON and HTML."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_lookup, build_pages
from table_checks import check_tables, headings


class InstallCoverageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.install = {
            "3.14 linux-x86_64": {"status": "wheel", "blockers": []},
            "3.14 win-arm64": {
                "status": "source",
                "blockers": [
                    {"name": "crypto<test>", "version": "2&3", "status": "source", "via": ["demo", "middle>", "crypto<test>"]},
                ],
            },
            "3.14 macos-arm64": {"status": "not_checked", "blockers": []},
            "3.14 musl-aarch64": {
                "status": "missing",
                "blockers": [{"name": "native", "version": None, "status": "missing", "via": ["demo", "native"]}],
            },
        }
        package = {
            "rank": 1, "name": "demo", "version": "1.2", "pure": True,
            "error": None, "results": ["wheel"] * len(self.install),
        }
        self.scan = {
            "schema": 1,
            "generated": "2026-11-16T03:20:00+00:00",
            "wheelgap": "0.11.0",
            "targets": [
                {"label": label, "python": "3.14", "platform": label.split(" ", 1)[1]}
                for label in self.install
            ],
            "packages": [{**package, "install": self.install}, {**package, "name": "older", "rank": 2}],
        }

    def coverage(self):
        source = self.root / "coverage.json"
        source.write_text(json.dumps(self.scan), encoding="utf-8")
        return source

    def test_lookup_preserves_optional_install_results(self):
        output = self.root / "data" / "packages"
        build_lookup.build(self.coverage(), output, root=self.root)
        current = json.loads((output / "demo.json").read_text(encoding="utf-8"))
        older = json.loads((output / "older.json").read_text(encoding="utf-8"))
        self.assertEqual(current["schema"], 1)
        self.assertEqual(current.get("install"), self.install)
        self.assertNotIn("install", older)
        self.assertEqual(current["results"], {label: "wheel" for label in self.install})

    def test_every_table_is_named_and_keyboard_scrollable(self):
        # #184: the coverage matrix and the install table (the build lab's is checked in test_build_lab.py).
        output = self.root / "site"
        build_pages.build(self.coverage(), output, root=self.root)
        page = (output / "p" / "demo" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(check_tables(page), ["demo 1.2 by platform and Python", "Install results with dependencies"])
        self.assertEqual(page.count("<table"), 2)

    def test_headings_go_h1_then_h2s(self):
        output = self.root / "site"
        build_pages.build(self.coverage(), output, root=self.root)
        for name in ("demo", "older"):
            with self.subTest(name=name):
                tags = headings((output / "p" / name / "index.html").read_text(encoding="utf-8"))
                self.assertEqual(tags[:2], ["h1", "h2"])  # the card's heading; it was an h3 (#184)
                self.assertEqual(tags.count("h1"), 1)
                self.assertIn("h2", tags[2:] if name == "demo" else tags)  # demo also has the install section

    def test_page_shows_target_outcomes_and_escapes_dependency_paths(self):
        output = self.root / "site"
        build_pages.build(self.coverage(), output, root=self.root)
        page = (output / "p" / "demo" / "index.html").read_text(encoding="utf-8")
        older = (output / "p" / "older" / "index.html").read_text(encoding="utf-8")
        self.assertIn('aria-labelledby="install-coverage"', page)
        self.assertIn("Wheels for the whole tree", page)
        self.assertIn("Builds from source", page)
        self.assertIn("Missing distribution", page)
        self.assertIn("Not checked", page)
        self.assertIn("could not resolve the full dependency tree", page)
        self.assertIn('<th scope="row">3.14 win-arm64</th>', page)
        self.assertIn("<code>crypto&lt;test&gt;==2&amp;3</code>", page)
        self.assertIn("<code>demo → middle&gt; → crypto&lt;test&gt;</code>", page)
        self.assertIn("<code>native</code>", page)
        self.assertNotIn("crypto<test>", page)
        self.assertNotIn("installs everywhere", page)
        self.assertNotIn("install-coverage", older)

    def test_links_only_known_blockers_and_via_packages(self):
        # All pages are pre-indexed, regardless of scan order.
        self.scan["packages"][0]["install"]["3.14 win-arm64"]["blockers"] = [
            {"name": "native", "version": "1.0", "status": "source",
             "via": ["demo", "PyYAML", "native"]},
            {"name": "unscanned", "version": "2.0", "status": "missing",
             "via": ["demo", "unscanned"]},
            {"name": "Typing_Extensions", "version": "4", "status": "source",
             "via": ["demo", "Typing_Extensions"]},
        ]
        self.scan["packages"].append({**self.scan["packages"][1], "name": "PyYAML", "rank": 3})
        self.scan["packages"].append({**self.scan["packages"][1], "name": "native", "rank": 4})
        self.scan["packages"].append(
            {**self.scan["packages"][1], "name": "typing.extensions", "rank": 5})
        output = self.root / "site"
        build_pages.build(self.coverage(), output, root=self.root)
        page = (output / "p" / "demo" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<code><a href="../native/">native</a>==1.0</code>', page)
        self.assertIn('<a href="../pyyaml/">PyYAML</a>', page)
        self.assertIn('via <code>demo → <a href="../pyyaml/">PyYAML</a> → '
                      '<a href="../native/">native</a></code>', page)
        self.assertIn('<a href="../typing-extensions/">Typing_Extensions</a>==4', page)
        self.assertIn('<code>unscanned==2.0</code>', page)
        self.assertNotIn('href="../unscanned/"', page)
        self.assertNotIn('href="../demo/"', page)

    def test_escaped_unknown_names_never_become_links(self):
        output = self.root / "site"
        build_pages.build(self.coverage(), output, root=self.root)
        page = (output / "p" / "demo" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('href="../crypto', page)

    def build_page(self, site):
        """The demo page and what the build printed."""
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            build_pages.build(self.coverage(), self.root / site, root=self.root)
        return (self.root / site / "p" / "demo" / "index.html").read_text(encoding="utf-8"), printed.getvalue()

    def test_malformed_install_results_are_left_out_with_one_warning(self):
        """#179: scan_install.py's output isn't checked before it's committed, so the page must not trust it."""
        good = {"name": "native", "version": "1.0", "status": "missing", "via": ["demo", "native"]}
        cases = {
            "no blockers": ({"status": "source"}, 1),
            "blockers not a list": ({"status": "source", "blockers": "x"}, 1),
            "blocker not an object": ({"status": "source", "blockers": ["x", good]}, 1),
            "blocker fields": (
                {"status": "source", "blockers": [
                    {"version": "1", "status": "source", "via": []},
                    {"name": 5, "version": "1", "status": "source", "via": []},
                    {"name": "bad-status", "version": "1", "status": "wheel", "via": []},
                    {"name": "bad-via", "version": "1", "status": "source", "via": "demo"},
                    {"name": "bad-via-item", "version": "1", "status": "source", "via": ["demo", 5]},
                    good,
                ]},
                5,
            ),
            "result not an object": ("source", 1),
            "status not a string": ({"status": ["source"], "blockers": []}, 1),
        }
        valid, printed = self.build_page("valid")
        self.assertEqual(printed, "")
        for number, (label, (result, dropped)) in enumerate(cases.items()):
            with self.subTest(label):
                self.scan["packages"][0]["install"] = {**self.install, "3.14 win-arm64": result}
                page, printed = self.build_page(f"site{number}")
                self.assertEqual(printed.count("::warning::"), 1)
                noun = "entry" if dropped == 1 else "entries"
                self.assertIn(f"left out {dropped} malformed way_out or install {noun} on 1 package page\n", printed)
                # The other targets' results still show.
                self.assertIn("Wheels for the whole tree", page)
                self.assertIn("Missing distribution", page)
                self.assertIn("<code>native</code> has no distribution via", page)
                self.assertNotIn("bad-", page)
                shown = '<th scope="row">3.14 win-arm64</th>' in page
                self.assertEqual(shown, isinstance(result, dict))
                if isinstance(result, dict) and isinstance(result.get("blockers"), list) and good in result["blockers"]:
                    self.assertIn(
                        '<span class="status s-source">Builds from source</span><ul><li><code>native==1.0</code> '
                        "has no distribution via <code>demo → native</code></li></ul>", page)

    def test_missing_result_left_with_only_a_source_blocker_still_says_it_cannot_install(self):
        """The malformed `missing` blocker is dropped; the summary must not read as "installs after a build"."""
        install = self.scan["packages"][0]["install"]
        install["3.14 win-arm64"] = {"status": "wheel", "blockers": []}  # keeps the summary short enough for details
        install["3.14 musl-aarch64"]["blockers"] = [
            {"name": "slow", "version": "1.0", "status": "source", "via": ["demo", "slow"]},
            {"name": "native", "status": "missing"},  # no `via`
        ]
        page, printed = self.build_page("site")
        self.assertIn("left out 1 malformed way_out or install entry on 1 package page", printed)
        self.assertIn("on Alpine ARM64 it builds slow from source and can&#x27;t install", page)
        self.assertNotIn("it builds slow from source;", page)
        self.assertNotIn("it builds slow from source.", page)

    def test_version_that_is_not_a_string_reads_as_none(self):
        self.scan["packages"][0]["install"]["3.14 win-arm64"]["blockers"] = [
            {"name": "native", "version": 3, "status": "source", "via": ["demo", "native"]},
            {"name": "other", "status": "source", "via": ["demo", "other"]},
        ]
        page, printed = self.build_page("site")
        self.assertEqual(printed, "")
        self.assertIn("<li><code>native</code> builds from source via <code>demo → native</code></li>", page)
        self.assertIn("<li><code>other</code> builds from source via <code>demo → other</code></li>", page)

    def test_install_that_is_not_an_object_gives_no_section(self):
        self.scan["packages"][0]["install"] = ["3.14 win-arm64"]
        page, printed = self.build_page("site")
        self.assertIn("left out 1 malformed way_out or install entry on 1 package page", printed)
        self.assertNotIn("install-coverage", page)

    def test_clean_install_keeps_well_formed_results_as_they_are(self):
        self.assertEqual(build_pages.clean_install(self.install), (self.install, 0))
        hints = {"3.14 win-arm64": {"release": "1.4", "kind": "older"}}
        self.assertEqual(build_pages.clean_way_out(hints), (hints, 0))

def blocker(name, status="source"):
    return {"name": name, "version": "1.0", "status": status, "via": ["mcp", name]}


class DescribeWholeTreeTests(unittest.TestCase):
    """The meta description leads with whole-tree install results (#99)."""

    PLATFORMS = ["linux-x86_64", "linux-aarch64", "musl-x86_64", "musl-aarch64",
                 "macos-arm64", "macos-x86_64", "win-amd64", "win-arm64"]

    def setUp(self):
        self.targets = [{"label": f"3.14 {pl}", "python": "3.14", "platform": pl} for pl in self.PLATFORMS]
        self.targets += [{"label": "3.14t linux-x86_64", "python": "3.14t", "platform": "linux-x86_64"},
                         {"label": "3.13 win-arm64", "python": "3.13", "platform": "win-arm64"}]
        self.columns = {pl: i for i, pl in enumerate(self.PLATFORMS)}

    def package(self, install=None, **changes):
        package = {"rank": 1, "name": "mcp", "version": "1.2.0", "pure": True, "error": None,
                   "results": ["wheel"] * len(self.targets)}
        if install is not None:
            package["install"] = install
        return {**package, **changes}

    def install(self, **special):
        result = {f"3.14 {pl}": {"status": "wheel", "blockers": []} for pl in self.PLATFORMS}
        for platform, value in special.items():
            result[f"3.14 {platform.replace('_', '-', 1)}"] = value
        return result

    def describe(self, package):
        text = build_pages.describe(package, self.columns, "3.14", self.targets)
        self.assertLessEqual(len(text), build_pages.DESCRIPTION_LIMIT)
        return text

    def test_whole_tree_ok(self):
        self.assertEqual(self.describe(self.package(self.install())),
                         "pip install mcp on Python 3.14: wheels for the whole tree on all 8 platforms checked.")

    def test_one_blocker(self):
        install = self.install(win_arm64={"status": "source", "blockers": [blocker("cryptography")]})
        self.assertEqual(
            self.describe(self.package(install)),
            "pip install mcp on Python 3.14: wheels for the whole tree on 7 of the 8 platforms checked; "
            "on Windows ARM64 it builds cryptography from source.",
        )

    def test_platforms_with_the_same_blockers_are_grouped(self):
        same = {"status": "source", "blockers": [blocker("cffi")]}
        install = self.install(win_arm64=same, musl_aarch64=same)
        self.assertEqual(
            self.describe(self.package(install)),
            "pip install mcp on Python 3.14: wheels for the whole tree on 6 of the 8 platforms checked; "
            "on Alpine ARM64 and Windows ARM64 it builds cffi from source.",
        )

    def test_too_long_to_name_the_blockers_falls_back_to_the_platforms(self):
        both = {"status": "missing", "blockers": [blocker("cryptography"), blocker("rpds-py", "missing")]}
        install = self.install(win_arm64=both, musl_aarch64=both)
        self.assertEqual(
            self.describe(self.package(install)),
            "pip install mcp on Python 3.14: wheels for the whole tree on 6 of the 8 platforms checked; "
            "can't install on Alpine ARM64 and Windows ARM64.",
        )

    def test_too_long_to_name_the_platforms_falls_back_to_the_counts(self):
        both = {"status": "missing", "blockers": [blocker("cryptography"), blocker("rpds-py", "missing")]}
        install = self.install(win_arm64=both, musl_aarch64=both,
                               musl_x86_64={"status": "source", "blockers": [blocker("pydantic-core")]})
        self.assertEqual(
            self.describe(self.package(install)),
            "pip install mcp on Python 3.14: wheels for the whole tree on 5 of the 8 platforms checked; "
            "builds from source on 1 platform and can't install on 2 platforms.",
        )

    def test_long_lists_are_shortened_to_stay_under_the_limit(self):
        many = {"status": "source", "blockers": [blocker(f"native-extension-{i}") for i in range(6)]}
        install = self.install(**{pl.replace("-", "_", 1): dict(many, blockers=many["blockers"][i:])
                                  for i, pl in enumerate(self.PLATFORMS[1:])})
        self.assertEqual(self.describe(self.package(install)),
                         "pip install mcp on Python 3.14: wheels for the whole tree on 1 of the 8 platforms checked; "
                         "builds from source on 7 platforms.")

    def test_a_long_name_keeps_only_the_lead_clause(self):
        name = "opentelemetry-exporter-otlp-proto-common"
        self.assertEqual(len(name), 40)
        source = {"status": "source", "blockers": [blocker("grpcio")]}
        missing = {"status": "missing", "blockers": [blocker("protobuf", "missing")]}
        install = self.install(linux_aarch64=source, musl_x86_64=source, musl_aarch64=source, macos_x86_64=source,
                               macos_arm64=missing, win_amd64=missing, win_arm64=missing)
        self.assertEqual(self.describe(self.package(install, name=name)),
                         f"pip install {name} on Python 3.14: wheels for the whole tree on 1 of the 8 platforms checked.")

    def test_not_checked_targets_are_left_out_and_never_called_working(self):
        install = self.install(win_arm64={"status": "not_checked", "blockers": []},
                               win_amd64={"status": "not_checked", "blockers": []})
        self.assertEqual(self.describe(self.package(install)),
                         "pip install mcp on Python 3.14: wheels for the whole tree on all 6 platforms checked.")

    def test_partial_results_count_only_the_checked_platforms(self):
        install = self.install(win_amd64={"status": "not_checked", "blockers": []},
                               win_arm64={"status": "source", "blockers": [blocker("cryptography")]})
        self.assertEqual(
            self.describe(self.package(install)),
            "pip install mcp on Python 3.14: wheels for the whole tree on 6 of the 7 platforms checked; "
            "on Windows ARM64 it builds cryptography from source.",
        )

    def test_all_not_checked_keeps_the_old_sentence(self):
        install = {label: {"status": "not_checked", "blockers": []} for label in self.install()}
        self.assertEqual(self.describe(self.package(install)), "mcp 1.2.0 ships a pure Python wheel.")

    def test_no_install_key_keeps_the_old_sentence(self):
        self.assertEqual(self.describe(self.package()), "mcp 1.2.0 ships a pure Python wheel.")

    def test_other_pythons_and_free_threading_dont_count(self):
        install = self.install()
        install["3.14t linux-x86_64"] = {"status": "missing", "blockers": [blocker("x", "missing")]}
        install["3.13 win-arm64"] = {"status": "source", "blockers": [blocker("y")]}
        self.assertEqual(self.describe(self.package(install)),
                         "pip install mcp on Python 3.14: wheels for the whole tree on all 8 platforms checked.")

    def test_pyodide_doesnt_count(self):
        self.targets.append({"label": "3.14 pyodide", "python": "3.14", "platform": "pyodide"})
        install = self.install()
        install["3.14 pyodide"] = {"status": "missing", "blockers": [blocker("cryptography", "missing")]}
        self.assertEqual(self.describe(self.package(install)),
                         "pip install mcp on Python 3.14: wheels for the whole tree on all 8 platforms checked.")

    def test_one_checked_platform_is_named_not_counted(self):
        install = self.install()
        for label in install:
            if label != "3.14 linux-x86_64":
                install[label] = {"status": "not_checked", "blockers": []}
        self.assertEqual(self.describe(self.package(install)),
                         "pip install mcp on Python 3.14: wheels for the whole tree on Linux x86_64, the only platform "
                         "checked.")
        install["3.14 linux-x86_64"] = {"status": "source", "blockers": [blocker("cryptography")]}
        text = self.describe(self.package(install))
        self.assertTrue(text.startswith("pip install mcp on Python 3.14: no wheels for the whole tree on Linux x86_64, "
                                        "the only platform checked"), text)
        self.assertNotIn("all 1", text)
        self.assertNotIn("of the 1", text)

    def test_a_newer_python_without_install_results_uses_the_newest_checked_one(self):
        # coverage.json scans 3.15, but install coverage (scripts/scan_install.py) only checks 3.14.
        self.targets += [{"label": f"3.15 {pl}", "python": "3.15", "platform": pl} for pl in self.PLATFORMS]
        install = self.install(win_arm64={"status": "source", "blockers": [blocker("cryptography")]})
        expected = ("pip install mcp on Python 3.14: wheels for the whole tree on 7 of the 8 platforms checked; "
                    "on Windows ARM64 it builds cryptography from source.")
        columns = {pl: len(self.PLATFORMS) + 2 + i for i, pl in enumerate(self.PLATFORMS)}
        text = build_pages.describe(self.package(install), columns, "3.15", self.targets)
        self.assertEqual(text, expected)
        install["3.15 linux-x86_64"] = {"status": "not_checked", "blockers": []}
        text = build_pages.describe(self.package(install), columns, "3.15", self.targets)
        self.assertEqual(text, expected)
        scan = {"schema": 1, "generated": "2026-11-16T03:20:00+00:00", "wheelgap": "0.11.0",
                "targets": self.targets, "packages": [self.package(install)]}
        python = build_pages.newest_non_free_threaded(self.targets)
        self.assertEqual(python, "3.15")
        self.assertIn(f'<meta name="description" content="{expected}">', build_pages.page(scan["packages"][0], scan, python))

    def test_page_and_og_description_use_the_same_sentence(self):
        install = self.install(win_arm64={"status": "source", "blockers": [blocker("crypto<x>")]})
        scan = {"schema": 1, "generated": "2026-11-16T03:20:00+00:00", "wheelgap": "0.11.0",
                "targets": self.targets, "packages": [self.package(install)]}
        page = build_pages.page(scan["packages"][0], scan, "3.14")
        sentence = "on Windows ARM64 it builds crypto&lt;x&gt; from source."
        self.assertIn('<meta name="description" content="pip install mcp on Python 3.14: wheels for the whole tree '
                      f'on 7 of the 8 platforms checked; {sentence}">', page)
        self.assertIn('<meta property="og:description" content="pip install mcp on Python 3.14: wheels', page)
        self.assertEqual(page.count(sentence), 3)  # meta, og and the visible summary
