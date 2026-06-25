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
        {"Path": "edited_both.txt", "Size": 200, "ModTime": "2026-06-21T09:00:00Z", "IsDir": False},
        {"Path": "identical.txt", "Size": 50, "ModTime": "2026-06-19T08:00:00Z", "IsDir": False},
    ],
    "onedrive:Active": [
        {"Path": "new_on_onedrive.txt", "Size": 300, "ModTime": "2026-06-20T11:00:00Z", "IsDir": False},
        {"Path": "edited_both.txt", "Size": 200, "ModTime": "2026-06-21T12:00:00Z", "IsDir": False},
    ],
}


def fake_ensure():
    return "rclone"


def fake_lsjson(path, max_age, filters):
    return FAKE_LSJSON.get(path, [])


def fake_copy(src, dst, max_age, filters, dry_run=False, extra=None):
    res = rclone.CopyResult(src=src, dst=dst)
    # Simulate rclone: new files get copied; edited_both copied only where source is newer
    # (--update). identical.txt is skipped (not transferred).
    for f in FAKE_LSJSON.get(src, []):
        p = f["Path"]
        if p == "identical.txt":
            continue  # skipped by rclone hash check
        if p == "edited_both.txt":
            # onedrive copy is newer (12:00) -> only onedrive->gdrive transfers it
            if src.startswith("onedrive:"):
                res.updated.append(p)
            continue
        res.created.append(p)
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
        assert "Active/edited_both.txt" in result.conflicts, "should detect both-sides edit"
        assert t["created"] == 2, f"expected 2 created (one per side), got {t['created']}"
        assert t["updated"] == 1, f"expected 1 updated (newer onedrive copy), got {t['updated']}"
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
