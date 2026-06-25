"""Convert Google Workspace native docs to Office files on Google Drive.

Native Google docs (Docs/Sheets/Slides/Drawings) have no downloadable bytes, so a
plain sync turns them into useless link/shortcut files on OneDrive. This module
EXPORTS each native doc to its Office equivalent and writes that real file back
into the SAME Google Drive folder, so the normal sync then copies a usable
.docx/.xlsx/.pptx to OneDrive.

How: rclone can only export a Google doc during a download, so for each doc we
`copyto` it to a temp file with `--drive-export-formats <fmt>`, then `copyto` that
file back to Drive (uploading a .docx keeps it a .docx — as long as no rclone
import-format is configured). The step is idempotent: a doc is (re)converted only
when its Office twin is missing or older than the doc.
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tidysync import rclone
from tidysync.progress import Counter
from tidysync.state import utcnow_iso

# Google native MIME type -> Office export extension.
EXPORT_MAP = {
    "application/vnd.google-apps.document": "docx",
    "application/vnd.google-apps.spreadsheet": "xlsx",
    "application/vnd.google-apps.presentation": "pptx",
    "application/vnd.google-apps.drawing": "svg",
}
GOOGLE_PREFIX = "application/vnd.google-apps."


@dataclass
class ConvertResult:
    remote: str
    scope_desc: str
    apply: bool
    started: str
    finished: str = ""
    duration_s: float = 0.0
    converted: List[dict] = field(default_factory=list)   # {path, out, ext}
    skipped_uptodate: List[str] = field(default_factory=list)
    skipped_unsupported: List[str] = field(default_factory=list)  # forms, sites, ...
    errors: List[str] = field(default_factory=list)

    @property
    def totals(self) -> Dict[str, int]:
        return {
            "converted": len(self.converted),
            "uptodate": len(self.skipped_uptodate),
            "unsupported": len(self.skipped_unsupported),
            "errors": len(self.errors),
        }


def _join(remote: str, rel: str) -> str:
    if not rel:
        return remote
    if remote.endswith(":"):
        return remote + rel
    return remote.rstrip("/") + "/" + rel


def _scan_paths(remote: str, folders: Optional[List[str]]) -> List[Tuple[str, str]]:
    if folders:
        return [(_join(remote, f), f) for f in folders]
    return [(remote, "")]


def out_name(full_path: str, ext: str) -> str:
    return f"{full_path}.{ext}"


def run_convert(remote: str, folders: Optional[List[str]] = None,
                filters: Optional[List[str]] = None,
                dry_run: bool = False, progress: bool = False,
                refresh: bool = False) -> ConvertResult:
    """Export native Google docs to Office files on Drive, recursively, in place.

    By default only creates a copy where one does NOT already exist. With
    refresh=True, also re-converts a doc whose Office copy is older than the doc.
    """
    rclone.ensure_rclone()
    start = time.time()
    result = ConvertResult(
        remote=remote,
        scope_desc=("folders: " + ", ".join(folders)) if folders else "whole drive",
        apply=not dry_run, started=utcnow_iso(),
    )

    # 1. List everything once (lsjson includes MimeType + ModTime).
    items: List[dict] = []
    for scan_path, prefix in _scan_paths(remote, folders):
        try:
            listing = rclone.lsjson(
                scan_path, filters=filters,
                spinner_label=(f"scanning {scan_path} for Google docs" if progress else None))
            for it in listing:
                it["_full"] = (prefix + "/" + it["Path"]) if prefix else it["Path"]
                items.append(it)
        except rclone.RcloneError as exc:
            result.errors.append(str(exc))

    # 2. Map of existing (non-native) files -> ModTime, for idempotency checks.
    existing = {it["_full"]: it.get("ModTime", "")
                for it in items if not it.get("MimeType", "").startswith(GOOGLE_PREFIX)}

    # 3. Decide what needs converting.
    todo: List[Tuple[dict, str, str]] = []
    for it in items:
        mime = it.get("MimeType", "")
        if not mime.startswith(GOOGLE_PREFIX):
            continue
        ext = EXPORT_MAP.get(mime)
        if not ext:
            result.skipped_unsupported.append(it["_full"])
            continue
        out = out_name(it["_full"], ext)
        if out in existing:
            # Office copy already exists. Skip unless refresh + the doc is newer.
            if not refresh or existing[out] >= it.get("ModTime", ""):
                result.skipped_uptodate.append(it["_full"])
                continue
        todo.append((it, out, ext))

    if dry_run:
        for it, out, ext in todo:
            result.converted.append({"path": it["_full"], "out": out, "ext": ext,
                                     "applied": False})
        result.finished = utcnow_iso()
        result.duration_s = round(time.time() - start, 1)
        return result

    # 4. Export each doc to a temp file, then upload the Office file back to Drive.
    tmp = tempfile.mkdtemp(prefix="tidysync_gdocs_")
    counter = Counter(len(todo), "converting", on=progress)
    try:
        for it, out, ext in todo:
            counter.step(it["_full"])
            src = _join(remote, it["_full"])
            local = os.path.join(tmp, out.replace("/", "__"))
            ok, err = rclone.copyto(src, local, extra=["--drive-export-formats", ext])
            if not ok:
                result.errors.append(f"{it['_full']}: export failed: {err}")
                continue
            ok, err = rclone.copyto(local, _join(remote, out))
            if not ok:
                result.errors.append(f"{out}: upload failed: {err}")
                continue
            result.converted.append({"path": it["_full"], "out": out, "ext": ext,
                                     "applied": True})
        counter.close()
    finally:
        for root, _dirs, files in os.walk(tmp, topdown=False):
            for f in files:
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass
            try:
                os.rmdir(root)
            except OSError:
                pass

    result.finished = utcnow_iso()
    result.duration_s = round(time.time() - start, 1)
    return result
