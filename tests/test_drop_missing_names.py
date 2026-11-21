"""The post-scan filter never needs a live PyPI lookup."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_scan, drop_missing_names

ROOT = Path(__file__).resolve().parent.parent


def scan(missing=1):
    packages = [
        {"rank": i, "name": f"pkg{i}", "version": "1.0", "pure": False, "error": None,
         "results": ["wheel", "source"]}
        for i in range(1, 21)
    ]
    packages += [
        {"rank": 21 + i, "name": f"gone{i}", "version": None, "pure": False,
         "error": "not on index", "results": ["unknown", "unknown"]}
        for i in range(missing)
    ]
    return {
        "schema": 1, "generated": "2026-11-20T03:20:00Z", "wheelgap": "0.9.0",
        "targets": [{"label": "3.13 linux-x86_64"}, {"label": "3.13 win-arm64"}],
        "summary": {"3.13 linux-x86_64": {"wheel": 20, "unknown": missing},
                    "3.13 win-arm64": {"source": 20, "unknown": missing}},
        "packages": packages, "lookup_errors": missing,
    }


def write(tmp_path, data, name="coverage.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data, indent=2) + "\n\n", encoding="utf-8")
    return path


def write_both(tmp_path, data):
    """Tonight's scan, and a previous scan that couldn't find the same names either."""
    return write(tmp_path, data), write(tmp_path, data, "previous.json")


def run(path, *previous):
    return subprocess.run([sys.executable, str(ROOT / "scripts/drop_missing_names.py"), str(path), *map(str, previous)],
                          capture_output=True, text=True)


def test_missing_name_is_removed_from_every_summary_and_the_scan_still_passes(tmp_path):
    data = scan()
    # Removing a name in the middle of the ranking must not renumber the other packages.
    data["packages"][10]["error"] = "not on index"
    data["packages"][10]["results"] = ["unknown", "unknown"]
    data["packages"][-1].update(rank=22, name="kept", version="1.0", error=None, results=["wheel", "source"])
    original_packages = [p for p in data["packages"] if p["name"] != "pkg11"]
    path, previous = write_both(tmp_path, data)
    result = run(path, previous)
    assert result.returncode == 0, result.stderr
    assert "drop_missing_names: 1 name left out" in result.stdout
    saved = json.loads(path.read_text())
    assert saved["packages"] == original_packages
    assert saved["dropped_names"] == {"count": 1, "names": ["pkg11"]}
    assert saved["lookup_errors"] == 0
    assert saved["summary"] == {"3.13 linux-x86_64": {"wheel": 20, "unknown": 0},
                                "3.13 win-arm64": {"source": 20, "unknown": 0}}
    assert check_scan.problems(saved, [], top=21) == []
    validated = subprocess.run([sys.executable, str(ROOT / "scripts/check_scan.py"), str(path),
                                str(tmp_path / "no-history.json"), "21"], capture_output=True, text=True)
    assert validated.returncode == 0, validated.stderr
    once = path.read_bytes()
    assert drop_missing_names.drop(path, previous) == 0
    assert path.read_bytes() == once


@pytest.mark.parametrize("error", ["timeout", "HTTP 503", "not on index: temporary error"])
def test_other_lookup_errors_are_kept(tmp_path, error):
    data = scan(2)
    data["packages"][-1]["error"] = error
    path, previous = write_both(tmp_path, data)
    assert drop_missing_names.drop(path, previous) == 1
    saved = json.loads(path.read_text())
    assert saved["packages"][-1] == data["packages"][-1]
    assert saved["lookup_errors"] == 1
    assert all(summary["unknown"] == 1 for summary in saved["summary"].values())


def test_lookup_errors_can_be_counted_when_the_field_is_absent(tmp_path):
    data = scan()
    del data["lookup_errors"]
    path, previous = write_both(tmp_path, data)
    assert drop_missing_names.drop(path, previous) == 1
    assert json.loads(path.read_text())["lookup_errors"] == 0


def test_more_than_the_limit_is_refused_without_rewriting_the_scan(tmp_path):
    path, previous = write_both(tmp_path, scan(26))
    original = path.read_bytes()
    result = run(path, previous)
    assert result.returncode == 1
    assert "refusing to drop 26 names (limit 25)" in result.stderr
    assert path.read_bytes() == original


def test_the_limit_is_allowed(tmp_path):
    path, previous = write_both(tmp_path, scan(25))
    assert drop_missing_names.drop(path, previous) == 25
    saved = json.loads(path.read_text())
    assert saved["dropped_names"]["count"] == 25
    assert check_scan.problems(saved, [], top=45) == []


def test_scan_with_nothing_to_drop_is_byte_for_byte_unchanged(tmp_path):
    data = scan(1)
    data["packages"][-1]["error"] = "timeout"
    path, previous = write_both(tmp_path, data)
    original = path.read_bytes()
    assert run(path, previous).returncode == 0
    assert path.read_bytes() == original
    assert "dropped_names" not in json.loads(path.read_text())


def test_a_name_not_found_for_the_first_time_is_kept_and_the_scan_is_unchanged(tmp_path):
    # One night's 404 for a real project: the scan before this one found every name.
    path = write(tmp_path, scan(1))
    previous = write(tmp_path, scan(0), "previous.json")
    original = path.read_bytes()
    result = run(path, previous)
    assert result.returncode == 0, result.stderr
    assert "drop_missing_names: 0 names left out" in result.stdout
    assert "1 not found for the first time, kept as unknown" in result.stdout
    assert path.read_bytes() == original


def test_only_the_names_the_previous_scan_could_not_find_either_are_dropped(tmp_path):
    path = write(tmp_path, scan(2))
    previous = write(tmp_path, scan(1), "previous.json")  # gone0 was already missing; gone1 is new tonight
    assert drop_missing_names.drop(path, previous) == 1
    saved = json.loads(path.read_text())
    assert saved["dropped_names"] == {"count": 1, "names": ["gone0"]}
    assert saved["packages"][-1] == scan(2)["packages"][-1]
    assert saved["lookup_errors"] == 1
    assert all(summary["unknown"] == 1 for summary in saved["summary"].values())


def test_a_name_the_previous_scan_already_left_out_is_dropped_again(tmp_path):
    before = scan(0)
    before["dropped_names"] = {"count": 1, "names": ["gone0"]}
    path = write(tmp_path, scan(1))
    previous = write(tmp_path, before, "previous.json")
    assert drop_missing_names.drop(path, previous) == 1
    saved = json.loads(path.read_text())
    assert saved["dropped_names"] == {"count": 1, "names": ["gone0"]}
    assert [p["name"] for p in saved["packages"]] == [f"pkg{i}" for i in range(1, 21)]


def test_without_a_previous_scan_nothing_is_dropped(tmp_path):
    path = write(tmp_path, scan(1))
    original = path.read_bytes()
    result = run(path)
    assert result.returncode == 0, result.stderr
    assert "no previous scan given, so nothing is left out" in result.stderr
    assert "drop_missing_names: 0 names left out" in result.stdout
    assert path.read_bytes() == original


@pytest.mark.parametrize("content", [
    None,  # no file at all
    b"",
    b"not json",
    b"\xff\xfe",
    b"[]",
    b'{"dropped_names": {"count": 1, "names": ["gone0"]}}',
    b'{"packages": {"gone0": {"error": "not on index"}}}',
    b'{"packages": ["gone0"]}',
    b'{"packages": [{"name": null, "error": "not on index"}]}',
    b'{"packages": [], "dropped_names": ["gone0"]}',
    b'{"packages": [], "dropped_names": {"count": 1}}',
    b'{"packages": [], "dropped_names": {"count": 1, "names": "gone0"}}',
    b'{"packages": [], "dropped_names": {"count": 2, "names": ["gone0", 1]}}',
])
def test_a_missing_or_malformed_previous_scan_drops_nothing(tmp_path, content):
    path = write(tmp_path, scan(1))
    previous = tmp_path / "previous.json"
    if content is not None:
        previous.write_bytes(content)
    original = path.read_bytes()
    result = run(path, previous)
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert "so nothing is left out" in result.stderr
    assert "drop_missing_names: 0 names left out" in result.stdout
    assert path.read_bytes() == original


def test_the_limit_counts_only_the_names_that_would_be_dropped(tmp_path):
    # 30 names not found tonight, 5 of them for the second scan in a row: 5 is under the limit.
    path = write(tmp_path, scan(30))
    previous = write(tmp_path, scan(5), "previous.json")
    result = run(path, previous)
    assert result.returncode == 0, result.stderr
    assert "drop_missing_names: 5 names left out" in result.stdout
    saved = json.loads(path.read_text())
    assert saved["dropped_names"] == {"count": 5, "names": [f"gone{i}" for i in range(5)]}
    assert [p["name"] for p in saved["packages"][20:]] == [f"gone{i}" for i in range(5, 30)]
    assert saved["lookup_errors"] == 25
    assert all(summary["unknown"] == 25 for summary in saved["summary"].values())


def test_many_names_not_found_for_the_first_time_are_all_kept(tmp_path):
    path = write(tmp_path, scan(26))
    previous = write(tmp_path, scan(0), "previous.json")
    original = path.read_bytes()
    result = run(path, previous)
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == original
