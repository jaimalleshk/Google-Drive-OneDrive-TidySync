"""Offline smoke test: stub rclone, exercise syncjob + report end-to-end.

Run with:  python tests/test_smoke.py
(No real cloud or rclone binary needed.)
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tidysync import rclone
from tidysync.config import load_config
from tidysync.report import write_reports
from tidysync.state import StateStore
from tidysync.syncjob import run_pair

CONFIG = """\
remotes:
  gdrive: "gdrive:"
  onedrive: "onedrive:"
pairs:
  - name: projects
    left: gdrive
    right: onedrive
    mode: two-way
    scope: folders
    folders: ["Active"]
    delta: {since: last-sync}
"""

# Fake remote contents per (src path) -> delta files returned by lsjson.
FAKE_LSJSON = {
    "gdrive:Active": [
        {"Path": "new_on_gdrive.txt", "Size": 100, "ModTime": "2026-06-20T10:00:00Z", "IsDir": False},
        # different SIZE on each side -> real conflict (content diverged)
        {"Path": "bigconflict.txt", "Size": 200, "ModTime": "2026-06-21T09:00:00Z", "IsDir": False},
        # same size, different modtime -> quiet newest-wins update, NOT a conflict
        {"Path": "samesize.txt", "Size": 80, "ModTime": "2026-06-22T08:00:00Z", "IsDir": False},
        # same size + same modtime -> identical, skipped
        {"Path": "identical.txt", "Size": 50, "ModTime": "2026-06-19T08:00:00Z", "IsDir": False},
    ],
    "onedrive:Active": [
        {"Path": "new_on_onedrive.txt", "Size": 300, "ModTime": "2026-06-20T11:00:00Z", "IsDir": False},
        {"Path": "bigconflict.txt", "Size": 250, "ModTime": "2026-06-21T12:00:00Z", "IsDir": False},
        {"Path": "samesize.txt", "Size": 80, "ModTime": "2026-06-22T10:00:00Z", "IsDir": False},
        {"Path": "identical.txt", "Size": 50, "ModTime": "2026-06-19T08:00:00Z", "IsDir": False},
    ],
}


def fake_ensure():
    return "rclone"


def fake_lsjson(path, max_age, filters, **kwargs):
    return FAKE_LSJSON.get(path, [])


def fake_copy(src, dst, max_age, filters, dry_run=False, extra=None, **kwargs):
    """Simulate rclone copy --update: new files created; src copied only when newer
    than dest and not identical (same size + same modtime)."""
    res = rclone.CopyResult(src=src, dst=dst)
    dst_files = {f["Path"]: f for f in FAKE_LSJSON.get(dst, [])}
    for sf in FAKE_LSJSON.get(src, []):
        p = sf["Path"]
        df = dst_files.get(p)
        if df is None:
            res.created.append(p)
        elif sf["ModTime"] > df["ModTime"]:   # src newer (--update)
            if sf["Size"] != df["Size"] or sf["ModTime"][:19] != df["ModTime"][:19]:
                res.updated.append(p)          # differs -> would copy
            # identical -> skipped
        # src older/equal -> skipped
    return res


def main():
    rclone.ensure_rclone = fake_ensure
    rclone.lsjson = fake_lsjson
    rclone.copy = fake_copy
    rclone.remote_type = lambda r: ""   # not Google Drive -> skip gdoc conversion here

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        cfg_path = d / "config.yaml"
        cfg_path.write_text(CONFIG, encoding="utf-8")
        cfg = load_config(cfg_path)
        store = StateStore(cfg.state_dir)

        result = run_pair(cfg, cfg.pair("projects"), store, since_override="2026-06-01")
        t = result.totals
        print("totals:", t)
        print("conflicts:", result.conflicts)
        print("items:")
        for it in sorted(result.items, key=lambda x: (x["path"], x["direction"])):
            print(f"  {it['direction']:<4} {it['action']:<18} {it['path']}")

        # Assertions
        assert "Active/bigconflict.txt" in result.conflicts, "size-diff file is a conflict"
        assert "Active/samesize.txt" not in result.conflicts, \
            "same-size/diff-modtime is quiet newest-wins, NOT a conflict"
        assert t["conflicts"] == 1, f"only the size-diff file is a conflict, got {t['conflicts']}"
        assert t["created"] == 2, f"expected 2 created (one per side), got {t['created']}"
        assert t["updated"] == 2, \
            f"expected 2 updated (bigconflict + samesize, newest-wins), got {t['updated']}"
        assert t["skipped_identical"] >= 1, "identical file should be skipped"
        assert t["errors"] == 0, "no errors expected"

        html, csv_p, json_p = write_reports(result, cfg.reports_dir)
        for p in (html, csv_p, json_p):
            assert p.exists() and p.stat().st_size > 0, f"report missing: {p}"
        print("reports written:", html.name, csv_p.name, json_p.name)

        # Guard: a fresh last-sync pair with no state and no --since must refuse.
        store2 = StateStore(d / "state2")
        try:
            run_pair(cfg, cfg.pair("projects"), store2, since_override=None)
            raise AssertionError("expected SyncError for missing last-sync window")
        except Exception as exc:
            assert "no recorded last-sync" in str(exc), exc
            print("guard ok: refused full-drive copy without --since")

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
