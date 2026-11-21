import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import build_lookup, build_pages


PUBLIC_SOURCE = "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json"


def target(platform):
    return {
        "label": f"3.14 {platform}",
        "python": "3.14",
        "platform": platform,
        "glibc": "2.31",
        "musl": "1.2",
        "macos": "13.0",
    }


def package(rank, name, statuses, **extra):
    return {"rank": rank, "name": name, "version": "2.0", "pure": False, "error": None, "results": statuses, **extra}


def snapshot(packages):
    return {
        "schema": 1, "generated": "2026-11-16T03:17:00Z", "wheelgap": "0.9.0",
        "top_packages": {"source": PUBLIC_SOURCE},
        "targets": [target("win-arm64"), target("macos-arm64"), target("linux-x86_64")],
        "packages": packages,
    }


def write_scan(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_package_publication_shows_suggestions_only_for_gaps(tmp_path):
    hints = {
        "3.14 win-arm64": {"release": "1.4<test>", "kind": "older"},
        "3.14 macos-arm64": {"release": "2.1", "kind": "newer"},
        "3.14 linux-x86_64": {"release": "never shown", "kind": "newer"},
    }
    data = snapshot([
        package(1, "demo", ["source", "missing", "wheel"], way_out=hints),
        package(2, "older-snapshot", ["source", "wheel", "wheel"]),
    ])
    scan = write_scan(tmp_path / "coverage.json", data)
    out = tmp_path / "data" / "packages"
    site = tmp_path / "site"
    build_lookup.build(scan, out, root=tmp_path)
    build_pages.build(scan, site, root=tmp_path)
    demo = json.loads((out / "demo.json").read_text(encoding="utf-8"))
    old = json.loads((out / "older-snapshot.json").read_text(encoding="utf-8"))
    assert demo.get("way_out") == hints
    assert demo["schema"] == 1
    assert "way_out" not in old
    page = (site / "p" / "demo" / "index.html").read_text(encoding="utf-8")
    assert "1.4&lt;test&gt; is the last release with a wheel here." in page
    assert "Upgrade to 2.1 for a wheel here." in page
    assert "never shown" not in page and "1.4<test>" not in page
    assert "the last release with a wheel here" not in (site / "p" / "older-snapshot" / "index.html").read_text(encoding="utf-8")


GOOD_HINT = {"3.14 macos-arm64": {"release": "2.1", "kind": "newer"}}


@pytest.mark.parametrize(
    ("way_out", "dropped"),
    [
        ([], 1),
        ({"3.14 win-arm64": "46.0.3", **GOOD_HINT}, 1),
        ({"3.14 win-arm64": {"release": "46.0.3"}, **GOOD_HINT}, 1),
        ({"3.14 win-arm64": {"release": 46, "kind": "older"}, **GOOD_HINT}, 1),
        ({"3.14 win-arm64": {"release": "46.0.3", "kind": "sideways"}, "3.14 macos-arm64": None}, 2),
    ],
)
def test_malformed_way_out_is_left_out_with_one_warning(tmp_path, capsys, way_out, dropped):
    """#179: scan_way_out.py's output isn't checked before it's committed, so the pages must not trust it."""
    data = snapshot([
        package(1, "demo", ["source", "missing", "wheel"], way_out=way_out),
        package(2, "other", ["source", "missing", "wheel"], way_out="46.0.3"),
    ])
    build_pages.build(write_scan(tmp_path / "coverage.json", data), tmp_path / "site", root=tmp_path)
    out = capsys.readouterr().out
    assert out.count("::warning::") == 1
    assert f"left out {dropped + 1} malformed way_out or install entries on 2 package pages" in out
    page = (tmp_path / "site" / "p" / "demo" / "index.html").read_text(encoding="utf-8")
    assert "46.0.3" not in page
    # The page is the one the usable hints alone give: its other cells still show.
    kept = {k: v for k, v in way_out.items() if v == GOOD_HINT.get(k)} if isinstance(way_out, dict) else {}
    data["packages"][0]["way_out"] = kept
    del data["packages"][1]["way_out"]
    build_pages.build(write_scan(tmp_path / "clean.json", data), tmp_path / "clean", root=tmp_path)
    assert capsys.readouterr().out == ""
    assert page == (tmp_path / "clean" / "p" / "demo" / "index.html").read_text(encoding="utf-8")
    assert ("Upgrade to 2.1 for a wheel here." in page) == bool(kept)
    assert 'class="status s-source"' in page and 'class="status s-wheel"' in page


@pytest.fixture
def wheelgap_api(monkeypatch):
    make_targets = Mock(side_effect=lambda _python, platform, **_baselines: platform)
    lookup = Mock(return_value=({}, {}))
    origin = SimpleNamespace(other_index=Mock(return_value=None), index_env=Mock(return_value=None))
    monkeypatch.setitem(sys.modules, "wheelgap", SimpleNamespace(origin=origin))
    monkeypatch.setitem(sys.modules, "wheelgap.scan", SimpleNamespace(TOP_PACKAGES_URL=PUBLIC_SOURCE))
    monkeypatch.setitem(sys.modules, "wheelgap.suggest", SimpleNamespace(look_up_all=lookup))
    monkeypatch.setitem(sys.modules, "wheelgap.targets", SimpleNamespace(build_targets=make_targets))
    return make_targets, lookup, origin


def test_scan_looks_up_each_eligible_project_once_and_retains_target_limits(tmp_path, wheelgap_api):
    from scripts import scan_way_out

    make_targets, lookup, _origin = wheelgap_api
    lookup.return_value = {
        "demo-project": [SimpleNamespace(found={"macos-arm64": "2.1"}, last={"win-arm64": "1.4"})],
    }, {"failed": "no usable answer"}
    scan = write_scan(tmp_path / "coverage.json", snapshot([
        package(1, "Demo_Project", ["source", "missing", "wheel"], install={"keep": True}),
        package(2, "demo-project", ["source", "missing", "wheel"]),
        package(3, "good", ["wheel", "bundled", "unknown"]),
        {**package(4, "error", ["source", "source", "wheel"]), "error": "lookup failed"},
        package(5, "failed", ["source", "bundled", "unknown"], way_out={"old": True}),
        package(6, "invalid/name", ["source", "source", "wheel"]),
    ]))
    scan_way_out.scan(scan)
    output = json.loads(scan.read_text(encoding="utf-8"))
    assert output["packages"][0]["way_out"] == {
        "3.14 macos-arm64": {"release": "2.1", "kind": "newer"},
        "3.14 win-arm64": {"release": "1.4", "kind": "older"},
    }
    assert output["packages"][0]["results"] == ["source", "missing", "wheel"]
    assert output["packages"][0]["install"] == {"keep": True}
    assert all("way_out" not in row for row in output["packages"][1:])
    assert lookup.call_count == 1
    assert lookup.call_args.args[0] == {
        "demo-project": [("2.0", ["win-arm64", "macos-arm64"], ["linux-x86_64"])],
        "failed": [("2.0", ["win-arm64"], [])],
    }
    assert 0 < lookup.call_args.kwargs["deadline"] <= 300
    make_targets.assert_any_call(["3.14"], ["macos-arm64"], glibc="2.31", musl="1.2", macos="13.0")


@pytest.mark.parametrize("guard", ["other_index", "index_env", "snapshot_source"])
def test_scan_refuses_non_public_inputs_without_changing_snapshot(tmp_path, wheelgap_api, guard):
    from scripts import scan_way_out

    make_targets, lookup, origin = wheelgap_api
    data = snapshot([package(1, "local", ["source", "source", "wheel"])])
    if guard == "snapshot_source":
        data["top_packages"]["source"] = "local-list.json"
    else:
        getattr(origin, guard).return_value = guard
    scan = write_scan(tmp_path / "coverage.json", data)
    before = scan.read_bytes()
    with pytest.raises(ValueError, match="public PyPI"):
        scan_way_out.scan(scan)
    assert scan.read_bytes() == before
    lookup.assert_not_called()
    make_targets.assert_not_called()
