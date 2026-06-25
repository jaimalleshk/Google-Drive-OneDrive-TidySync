"""Offline test for Google-doc -> Office conversion logic (stubbed rclone)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tidysync import gdocs, rclone

# One Drive folder: a Doc with no twin (convert), a Doc whose .docx is newer (skip),
# a Sheet (convert -> xlsx), an unsupported Form (skip), and a plain file (ignore).
LISTING = {
    "gdrive:": [
        {"Path": "Plan", "ModTime": "2026-06-10T00:00:00Z",
         "MimeType": "application/vnd.google-apps.document"},
        {"Path": "Budget", "ModTime": "2026-06-10T00:00:00Z",
         "MimeType": "application/vnd.google-apps.spreadsheet"},
        {"Path": "Notes", "ModTime": "2026-06-10T00:00:00Z",
         "MimeType": "application/vnd.google-apps.document"},
        {"Path": "Notes.docx", "ModTime": "2026-06-12T00:00:00Z",  # newer twin -> skip
         "MimeType": "application/octet-stream"},
        {"Path": "Survey", "ModTime": "2026-06-10T00:00:00Z",
         "MimeType": "application/vnd.google-apps.form"},          # unsupported
        {"Path": "photo.jpg", "ModTime": "2026-06-10T00:00:00Z",
         "MimeType": "image/jpeg"},                                # plain file
    ],
}

CALLS = []


def fake_ensure():
    return "rclone"


def fake_lsjson(path, max_age=None, filters=None, with_hash=False, **kwargs):
    return LISTING.get(path, [])


def fake_copyto(src, dst, extra=None, dry_run=False):
    CALLS.append((src, dst, tuple(extra or ())))
    return True, ""


def main():
    rclone.ensure_rclone = fake_ensure
    rclone.lsjson = fake_lsjson
    rclone.copyto = fake_copyto

    # --- dry run: decides correctly, performs nothing ---
    res = gdocs.run_convert("gdrive:", dry_run=True)
    t = res.totals
    print("dry-run totals:", t)
    conv_paths = sorted(c["path"] for c in res.converted)
    assert conv_paths == ["Budget", "Plan"], conv_paths        # Notes skipped (up-to-date)
    assert t["uptodate"] == 1, t                               # Notes
    assert t["unsupported"] == 1, t                            # Survey form
    assert CALLS == [], "dry run must not call copyto"

    # --- real run: exports + uploads each convertible doc ---
    res = gdocs.run_convert("gdrive:", dry_run=False)
    outs = sorted(c["out"] for c in res.converted)
    assert outs == ["Budget.xlsx", "Plan.docx"], outs
    # 2 docs x (export + upload) = 4 copyto calls
    assert len(CALLS) == 4, CALLS
    # export calls carry the right --drive-export-formats
    export_fmts = sorted(c[2][1] for c in CALLS if c[2] and c[2][0] == "--drive-export-formats")
    assert export_fmts == ["docx", "xlsx"], export_fmts
    print("real-run outs:", outs)
    print("copyto calls:", len(CALLS))

    print("\nALL GDOCS CHECKS PASSED")


if __name__ == "__main__":
    main()
