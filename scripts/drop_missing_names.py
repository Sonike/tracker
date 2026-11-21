"""Leave names that PyPI no longer has out of a scan, before check_scan.py validates it.

    python scripts/drop_missing_names.py data/coverage.json previous.json

Only an exact `not on index` error is a missing project, and a name is left out only once two
scans in a row can't find it: the scan before this one (previous.json) must list it with the same
error, or have left it out already. One night's 404 for a real project stays in the scan as
`unknown`. Timeouts and other lookup failures stay in the scan too. Without a readable previous
scan nothing is left out. Refuse a large batch of names to drop rather than hide a possible
outage, and leave a scan with nothing to drop byte-for-byte unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_DROPPED_NAMES = 25
NOT_FOUND = "not on index"


class PreviousScanUnusable(Exception):
    """The previous scan can't say which names were already missing."""


def gone_before(previous: Path) -> set[str]:
    """Names the scan before this one couldn't find either: still listed with the error, or already left out."""
    try:
        old = json.loads(previous.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # missing, unreadable, not UTF-8 or not JSON
        raise PreviousScanUnusable(f"can't read {previous}: {exc}") from exc
    packages = old.get("packages") if isinstance(old, dict) else None
    if not isinstance(packages, list) or any(
        not isinstance(p, dict) or not isinstance(p.get("name"), str) for p in packages
    ):
        raise PreviousScanUnusable(f"{previous} has no usable list of packages")
    names = {p["name"] for p in packages if p.get("error") == NOT_FOUND}
    if "dropped_names" in old:
        dropped = old["dropped_names"]
        left_out = dropped.get("names") if isinstance(dropped, dict) else None
        if not isinstance(left_out, list) or any(not isinstance(name, str) for name in left_out):
            raise PreviousScanUnusable(f"{previous} has a malformed dropped_names")
        names.update(left_out)
    return names


def drop(path: Path, previous: Path | None) -> int:
    scan = json.loads(path.read_text(encoding="utf-8"))
    not_found = [p for p in scan["packages"] if p.get("error") == NOT_FOUND]
    if not not_found:
        return 0
    if previous is None:
        print("drop_missing_names: no previous scan given, so nothing is left out", file=sys.stderr)
        return 0
    try:
        before = gone_before(previous)
    except PreviousScanUnusable as exc:
        print(f"drop_missing_names: {exc}, so nothing is left out", file=sys.stderr)
        return 0
    # One night's 404 for a real project stays in the scan as `unknown`; it is left out only
    # when the scan before this one couldn't find it either.
    missing = [p for p in not_found if p["name"] in before]
    kept = len(not_found) - len(missing)
    if kept:
        print(f"drop_missing_names: {kept} not found for the first time, kept as unknown")
    if not missing:
        return 0
    names = scan.get("dropped_names", {}).get("names", []) + [p["name"] for p in missing]
    if len(names) > MAX_DROPPED_NAMES:
        raise ValueError(f"refusing to drop {len(names)} names (limit {MAX_DROPPED_NAMES}); check the scan")

    errors = scan.get("lookup_errors", sum(bool(p.get("error")) for p in scan["packages"]))
    for p in missing:
        for target, result in zip(scan["targets"], p["results"], strict=True):
            scan["summary"][target["label"]][result] -= 1
    gone = {id(p) for p in missing}
    scan["packages"] = [p for p in scan["packages"] if id(p) not in gone]
    scan["lookup_errors"] = errors - len(missing)
    scan["dropped_names"] = {"count": len(names), "names": names}
    path.write_text(json.dumps(scan, separators=(",", ":")) + "\n", encoding="utf-8")
    return len(missing)


if __name__ == "__main__":
    try:
        count = drop(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
    except ValueError as exc:
        print(f"drop_missing_names: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"drop_missing_names: {count} {'name' if count == 1 else 'names'} left out")
