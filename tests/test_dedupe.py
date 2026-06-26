"""Offline smoke test for content-hash dedupe (stubbed rclone)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tidysync import dedupe, rclone
from tidysync.report import write_dedupe_report

# Files on a single remote. Two share content hash H1 (different names/folders).
FILES = {
    "gdrive:": [
        {"Path": "a.txt", "Size": 100, "ModTime": "2026-06-10T00:00:00Z",
         "IsDir": False, "Hashes": {"md5": "H1"}},
        {"Path": "copies/a_copy.txt", "Size": 100, "ModTime": "2026-06-12T00:00:00Z",
         "IsDir": False, "Hashes": {"md5": "H1"}},            # newer -> kept
        {"Path": "b.txt", "Size": 200, "ModTime": "2026-06-11T00:00:00Z",
         "IsDir": False, "Hashes": {"md5": "H2"}},            # unique
        {"Path": "native.gdoc", "Size": 50, "ModTime": "2026-06-11T00:00:00Z",
         "IsDir": False, "Hashes": {}},                       # no hash -> skipped_no_hash
        {"Path": "empty1.txt", "Size": 0, "ModTime": "2026-06-11T00:00:00Z",
         "IsDir": False, "Hashes": {"md5": "EMPTY"}},         # empty -> skipped_small
        {"Path": "empty2.txt", "Size": 0, "ModTime": "2026-06-11T00:00:00Z",
         "IsDir": False, "Hashes": {"md5": "EMPTY"}},         # empty -> skipped_small (NOT a dup)
        {"Path": "_duplicates/old.txt", "Size": 100, "ModTime": "2026-06-01T00:00:00Z",
         "IsDir": False, "Hashes": {"md5": "H1"}},            # already quarantined -> ignored
    ],
}

MOVES = []


def fake_ensure():
    return "rclone"


def fake_lsjson(path, max_age=None, filters=None, with_hash=False, **kwargs):
    assert with_hash, "dedupe must request hashes"
    return FILES.get(path, [])


def fake_moveto(src, dst, dry_run=False, **kwargs):
    MOVES.append((src, dst, dry_run))
    return True, ""


def main():
    rclone.ensure_rclone = fake_ensure
    rclone.lsjson = fake_lsjson
    rclone.moveto = fake_moveto

    result = dedupe.find_duplicates("gdrive:")
    t = result.totals
    print("totals:", t)
    for g in result.groups:
        print(f"  group {g.hash_type}:{g.hash_value} keep={g.kept['_full']} "
              f"quarantine={[f['_full'] for f in g.quarantined]}")

    assert t["files_scanned"] == 6, f"expected 6 scanned (quarantine ignored), got {t['files_scanned']}"
    assert t["duplicate_groups"] == 1, t
    assert t["duplicates"] == 1, t
    assert t["skipped_no_hash"] == 1, t                       # native.gdoc
    assert t["skipped_small"] == 2, t                         # two empty files, NOT grouped as dups
    g = result.groups[0]
    assert g.kept["_full"] == "copies/a_copy.txt", "newest copy must be kept"
    assert [f["_full"] for f in g.quarantined] == ["a.txt"], "older copy must be quarantined"

    dedupe.apply_quarantine(result, dry_run=False)
    assert MOVES == [("gdrive:a.txt", "gdrive:_duplicates/a.txt", False)], MOVES
    assert result.groups[0].quarantined[0]["_moved"] is True
    print("moves:", MOVES)

    with tempfile.TemporaryDirectory() as d:
        html, csv_p, json_p = write_dedupe_report(result, Path(d))
        for p in (html, csv_p, json_p):
            assert p.exists() and p.stat().st_size > 0, p
        print("reports written:", html.name, csv_p.name, json_p.name)

    print("\nALL DEDUPE CHECKS PASSED")


if __name__ == "__main__":
    main()
