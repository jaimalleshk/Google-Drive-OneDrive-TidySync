"""Render a sync run as HTML + CSV + JSON reports."""

from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Tuple

from tidysync.dedupe import DedupeResult
from tidysync.syncjob import RunResult


def _human_bytes(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>tidysync report - {pair}</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1b1b1b;}}
 h1{{font-size:20px;margin-bottom:4px;}}
 .sub{{color:#666;margin-bottom:16px;}}
 .cards{{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0;}}
 .card{{background:#f4f6f8;border:1px solid #e0e4e8;border-radius:8px;padding:10px 16px;min-width:96px;}}
 .card .n{{font-size:22px;font-weight:600;}}
 .card .l{{font-size:12px;color:#666;text-transform:uppercase;}}
 .card.warn .n{{color:#b26a00;}} .card.err .n{{color:#c0392b;}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:12px;}}
 th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #eee;}}
 th{{background:#fafafa;}}
 .tag{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;}}
 .created{{background:#e3f4e4;color:#1e7a32;}}
 .updated{{background:#e3edfb;color:#1b5fb0;}}
 .skipped-identical{{background:#eee;color:#666;}}
 .conflict{{background:#fdeccf;color:#9a5b00;font-weight:600;}}
 .dry{{background:#fff3cd;border:1px solid #ffe69c;padding:8px 12px;border-radius:6px;margin:8px 0;}}
 .errbox{{background:#fdecea;border:1px solid #f5c6c2;padding:8px 12px;border-radius:6px;margin:8px 0;}}
 code{{background:#f0f0f0;padding:1px 4px;border-radius:3px;}}
</style></head><body>
<h1>Sync report &mdash; {pair}</h1>
<div class="sub">mode <code>{mode}</code> &middot; scope <code>{scope}</code> &middot;
 delta since <code>{since_spec}</code> (window <code>{window}</code>) &middot;
 {started} &rarr; {finished} ({duration}s)</div>
{dry_banner}
{err_banner}
<div class="cards">
 <div class="card"><div class="n">{created}</div><div class="l">Created</div></div>
 <div class="card"><div class="n">{updated}</div><div class="l">Updated</div></div>
 <div class="card"><div class="n">{skipped}</div><div class="l">Skipped (identical)</div></div>
 <div class="card warn"><div class="n">{conflicts}</div><div class="l">Conflicts</div></div>
 <div class="card err"><div class="n">{errors}</div><div class="l">Errors</div></div>
 <div class="card"><div class="n">{converted}</div><div class="l">GDocs converted</div></div>
 <div class="card"><div class="n">{bytes}</div><div class="l">Transferred</div></div>
</div>
{conflict_block}
{converted_block}
<table><thead><tr>
 <th>File</th><th>Direction</th><th>Action</th><th>Size</th><th>Modified</th>
</tr></thead><tbody>
{rows}
</tbody></table>
</body></html>
"""


def _rows(result: RunResult) -> str:
    conflict_set = set(result.conflicts)
    out = []
    for it in sorted(result.items, key=lambda x: (x["path"], x["direction"])):
        action = it["action"]
        is_conflict = it["path"] in conflict_set
        tag = f'<span class="tag {action}">{action}</span>'
        if is_conflict:
            tag += ' <span class="tag conflict">conflict&rarr;newer-wins</span>'
        size = _human_bytes(it["size"]) if it.get("size") is not None else ""
        out.append(
            "<tr><td>{p}</td><td>{d}</td><td>{t}</td><td>{s}</td><td>{m}</td></tr>".format(
                p=html.escape(it["path"]), d=html.escape(it["direction"]),
                t=tag, s=size, m=html.escape(it.get("modtime", "")),
            )
        )
    if not out:
        out.append('<tr><td colspan="5"><em>No changed or new files in this window.</em></td></tr>')
    return "\n".join(out)


def write_reports(result: RunResult, reports_dir: Path) -> Tuple[Path, Path, Path]:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = reports_dir / f"{result.pair}_{stamp}"
    html_path = base.with_suffix(".html")
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")

    t = result.totals
    dry_banner = (
        '<div class="dry"><b>DRY RUN</b> &mdash; no files were transferred. '
        'Re-run without <code>--dry-run</code> to apply.</div>'
        if result.dry_run else ""
    )
    err_banner = ""
    if result.errors:
        items = "".join(f"<li>{html.escape(e)}</li>" for e in result.errors)
        err_banner = f'<div class="errbox"><b>{len(result.errors)} error(s):</b><ul>{items}</ul></div>'

    conflict_block = ""
    if result.conflicts:
        items = "".join(f"<li><code>{html.escape(c)}</code></li>" for c in result.conflicts)
        conflict_block = (
            '<div class="dry"><b>Conflicts (edited on both sides in this window):</b>'
            f'<ul>{items}</ul>Resolved by newest-modified-wins.</div>'
        )

    converted_block = ""
    if result.converted:
        verb = "Would convert" if result.dry_run else "Converted"
        items = "".join(
            f'<li><code>{html.escape(c["path"])}</code> &rarr; '
            f'<code>{html.escape(c["out"])}</code></li>' for c in result.converted)
        converted_block = (
            f'<div class="dry"><b>{verb} Google docs to Office on the Drive side '
            f'({len(result.converted)}; {result.conversion_uptodate} already up to date):</b>'
            f'<ul>{items}</ul></div>'
        )

    html_doc = _HTML.format(
        pair=html.escape(result.pair), mode=html.escape(result.mode),
        scope=html.escape(result.scope), since_spec=html.escape(result.since_spec),
        window=html.escape(result.window), started=result.started,
        finished=result.finished, duration=result.duration_s,
        dry_banner=dry_banner, err_banner=err_banner, conflict_block=conflict_block,
        converted_block=converted_block,
        created=t["created"], updated=t["updated"], skipped=t["skipped_identical"],
        conflicts=t["conflicts"], errors=t["errors"], bytes=_human_bytes(t["bytes"]),
        converted=len(result.converted),
        rows=_rows(result),
    )
    html_path.write_text(html_doc, encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "direction", "action", "conflict", "size_bytes", "modified"])
        conflict_set = set(result.conflicts)
        for it in result.items:
            w.writerow([it["path"], it["direction"], it["action"],
                        "yes" if it["path"] in conflict_set else "",
                        it.get("size") or "", it.get("modtime", "")])

    payload = asdict(result)
    payload["totals"] = result.totals
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return html_path, csv_path, json_path


_DEDUPE_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>tidysync dedupe - {remote}</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1b1b1b;}}
 h1{{font-size:20px;margin-bottom:4px;}} .sub{{color:#666;margin-bottom:16px;}}
 .cards{{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0;}}
 .card{{background:#f4f6f8;border:1px solid #e0e4e8;border-radius:8px;padding:10px 16px;min-width:96px;}}
 .card .n{{font-size:22px;font-weight:600;}} .card .l{{font-size:12px;color:#666;text-transform:uppercase;}}
 .card.err .n{{color:#c0392b;}} .card.go .n{{color:#1e7a32;}}
 .grp{{border:1px solid #e6e6e6;border-radius:8px;margin:10px 0;padding:8px 12px;}}
 .grp h3{{font-size:13px;margin:2px 0 8px;color:#444;font-weight:600;}}
 table{{border-collapse:collapse;width:100%;font-size:13px;}}
 th,td{{text-align:left;padding:5px 10px;border-bottom:1px solid #f0f0f0;}}
 .keep{{background:#e3f4e4;color:#1e7a32;}} .quar{{background:#fdeccf;color:#9a5b00;}}
 .tag{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;}}
 .banner{{background:#fff3cd;border:1px solid #ffe69c;padding:8px 12px;border-radius:6px;margin:8px 0;}}
 .errbox{{background:#fdecea;border:1px solid #f5c6c2;padding:8px 12px;border-radius:6px;margin:8px 0;}}
 code{{background:#f0f0f0;padding:1px 4px;border-radius:3px;}}
</style></head><body>
<h1>Duplicate report &mdash; {remote}</h1>
<div class="sub">{scope} &middot; content-hash dedupe &middot; keep newest-modified &middot;
 quarantine <code>{quarantine}/</code> &middot; {started} ({duration}s)</div>
{banner}
{err_banner}
<div class="cards">
 <div class="card"><div class="n">{scanned}</div><div class="l">Files scanned</div></div>
 <div class="card"><div class="n">{groups}</div><div class="l">Dup groups</div></div>
 <div class="card go"><div class="n">{dups}</div><div class="l">Duplicates</div></div>
 <div class="card go"><div class="n">{reclaim}</div><div class="l">Reclaimable</div></div>
 <div class="card"><div class="n">{nohash}</div><div class="l">No hash (skipped)</div></div>
 <div class="card err"><div class="n">{errors}</div><div class="l">Errors</div></div>
</div>
{groups_html}
</body></html>
"""


def write_dedupe_report(result: DedupeResult, reports_dir: Path) -> Tuple[Path, Path, Path]:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_remote = result.remote.replace(":", "").replace("/", "_") or "remote"
    base = reports_dir / f"dedupe_{safe_remote}_{stamp}"
    html_path, csv_path, json_path = (
        base.with_suffix(".html"), base.with_suffix(".csv"), base.with_suffix(".json"))

    t = result.totals
    banner = (
        '<div class="banner"><b>Applied</b> &mdash; duplicates were moved to the quarantine '
        'folder. Review them there, then delete what you don\'t want.</div>'
        if result.apply else
        '<div class="banner"><b>Report only</b> &mdash; nothing was moved. '
        'Re-run with <code>--apply</code> to move duplicates to the quarantine folder.</div>'
    )
    err_banner = ""
    if result.errors:
        items = "".join(f"<li>{html.escape(e)}</li>" for e in result.errors)
        err_banner = f'<div class="errbox"><b>{len(result.errors)} error(s):</b><ul>{items}</ul></div>'

    blocks = []
    for i, g in enumerate(sorted(result.groups, key=lambda x: x.kept["_full"]), 1):
        rows = [
            '<tr class="keep"><td><span class="tag keep">KEEP</span></td>'
            f'<td>{html.escape(g.kept["_full"])}</td>'
            f'<td>{_human_bytes(g.kept.get("Size") or 0)}</td>'
            f'<td>{html.escape(g.kept.get("ModTime",""))}</td></tr>'
        ]
        for f in g.quarantined:
            rows.append(
                '<tr class="quar"><td><span class="tag quar">quarantine</span></td>'
                f'<td>{html.escape(f["_full"])}</td>'
                f'<td>{_human_bytes(f.get("Size") or 0)}</td>'
                f'<td>{html.escape(f.get("ModTime",""))}</td></tr>'
            )
        blocks.append(
            f'<div class="grp"><h3>Group {i} &middot; {html.escape(g.hash_type)}:'
            f'{html.escape(g.hash_value[:16])}…</h3>'
            '<table><thead><tr><th></th><th>File</th><th>Size</th><th>Modified</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        )
    groups_html = "\n".join(blocks) or "<p><em>No content duplicates found.</em></p>"

    html_doc = _DEDUPE_HTML.format(
        remote=html.escape(result.remote), scope=html.escape(result.scope_desc),
        quarantine=html.escape(result.quarantine), started=result.started,
        duration=result.duration_s, banner=banner, err_banner=err_banner,
        scanned=t["files_scanned"], groups=t["duplicate_groups"], dups=t["duplicates"],
        reclaim=_human_bytes(t["reclaimable_bytes"]), nohash=t["skipped_no_hash"],
        errors=t["errors"], groups_html=groups_html,
    )
    html_path.write_text(html_doc, encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["group", "status", "file", "size_bytes", "modified", "hash_type", "hash"])
        for i, g in enumerate(result.groups, 1):
            w.writerow([i, "keep", g.kept["_full"], g.kept.get("Size") or "",
                        g.kept.get("ModTime", ""), g.hash_type, g.hash_value])
            for f in g.quarantined:
                w.writerow([i, "quarantine", f["_full"], f.get("Size") or "",
                            f.get("ModTime", ""), g.hash_type, g.hash_value])

    def _clean(f: dict) -> dict:
        return {k: v for k, v in f.items() if not k.startswith("_")} | {"path": f["_full"],
                "moved": f.get("_moved", False)}

    payload = {
        "remote": result.remote, "scope": result.scope_desc,
        "quarantine": result.quarantine, "apply": result.apply,
        "started": result.started, "finished": result.finished,
        "duration_s": result.duration_s, "totals": result.totals,
        "skipped_no_hash": result.skipped_no_hash, "errors": result.errors,
        "groups": [
            {"hash_type": g.hash_type, "hash": g.hash_value,
             "kept": _clean(g.kept), "quarantined": [_clean(f) for f in g.quarantined]}
            for g in result.groups
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return html_path, csv_path, json_path
