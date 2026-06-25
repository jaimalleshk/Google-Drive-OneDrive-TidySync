# Google Drive ⇄ OneDrive TidySync

> Private, free, two-way **delta sync** + **content-hash duplicate detection** between Google
> Drive and OneDrive — driven by a single menu-based command (`tidysync`), built on rclone.

A small, opinionated CLI that syncs **deltas** (newly created + recently modified files
and folders) between Google Drive and OneDrive — one-way in either direction, or two-way,
on demand or on a schedule — and writes a report of exactly what was synced.

It is an orchestration layer on top of [**rclone**](https://rclone.org/), the
battle-tested open-source engine that talks directly to Google Drive and Microsoft Graph
using **your own** OAuth credentials. Files move **cloud ↔ cloud directly** — they do not
pass through any third-party server. (Hosted alternatives like MultCloud / cloudHQ do the
same job but are metered/paid and route your data through their infrastructure.)

## ✨ What makes TidySync unique

Most tools do *one* of these. TidySync combines them — privately and for free:

1. **Content-hash duplicate detection with quarantine-for-review — the headline feature.**
   Finds files with **identical content under any name, in any folder**, keeps the newest, and
   moves the rest to a `_duplicates/` folder for you to review and delete. rclone's own `dedupe`
   only catches same-*name* files on Google Drive; the hosted tools (MultCloud / cloudHQ) don't do
   content dedupe at all. (See [Duplicate detection](#duplicate-detection-dedupe).)
2. **Safe-by-default cross-cloud delta sync — no rclone flags to learn.** Only what changed since
   the last run, **newest-wins**, **never deletes**, and it refuses an accidental full-drive copy.
   The safe path is the default path.
3. **Per-run reports for both sync and dedupe** (HTML + CSV + JSON) — exactly what changed, what's
   duplicated, and how much space you can reclaim. Neither raw rclone nor RcloneView produce these.
4. **One menu-driven command + an interactive config wizard** — approachable for non-technical
   users, yet fully scriptable and schedulable for power users.
5. **Private and free.** Files move **cloud ↔ cloud directly** through *your own* OAuth tokens —
   nothing passes through a third-party server, and there is no metering or subscription.

> TidySync is an *opinionated layer on top of [rclone](https://rclone.org/)*: the sync engine is
> rclone's; the duplicate detection, reporting, safety defaults and UX are what TidySync adds.

> ### ⚠️ Status — early / not yet validated against live accounts
> The logic is covered by **offline tests** (sync, dedupe, and the config wizard, with rclone
> stubbed), but it has **not yet been run end-to-end against real Google Drive / OneDrive
> accounts**. Treat it as **alpha**: try it on non-critical folders and use `--dry-run` first.
> **Screenshots and a step-by-step user guide will be added once live testing is complete**
> (private details blurred). Feedback and test reports are very welcome.

## How the delta model works

This tool deliberately does **not** use `rclone bisync`. Instead each direction runs:

```
rclone copy SRC DST --max-age <window> --update
```

- `--max-age <window>` — only consider files modified/created **since** your chosen point
  (a date, a duration, or the remembered last-sync time). No full-drive scan.
- `--update` — never overwrite a newer file with an older one ⇒ **most-recently-modified wins**.
- Identical files (same size/hash/modtime) are skipped automatically ⇒ no needless copies.
- **Two-way** = the same copy run in both directions.
- **Deletions are never propagated** — by design this tool only handles created + modified files.

A file changed on *both* sides within the window is flagged in the report as a
**conflict (resolved newest-wins)** so nothing happens silently.

## Setup

```bash
# 1. Install rclone and this tool
winget install Rclone.Rclone
pip install -e .          # from this folder; gives you the `tidysync` command

# 2. Authorise your two clouds (opens a browser; uses YOUR Google/Microsoft accounts)
rclone config             # create a Google Drive remote (e.g. "gdrive") and a OneDrive remote ("onedrive")

# 3. Create and edit your config
tidysync init             # writes config.yaml template
#   ...edit config.yaml to map remotes and define pairs...

# 4. Verify connectivity
tidysync check
```

> Tip: for best Google Drive performance, create your own Google API client ID during
> `rclone config` (rclone documents this).

## Usage

### One entry point — the menu

There is a **single command**, `tidysync`. Run it with no arguments (or double-click
`start.bat` on Windows) to open an interactive menu — you never run individual `.py` files:

```
========== tidysync : Drive <-> OneDrive ==========
  1) Run a sync pair
  2) Run all pairs
  3) Find duplicates in a cloud (dedupe)
  4) Configure / edit a sync pair
  5) Schedule a pair   6) Unschedule a pair
  7) Status            8) Check remotes
  9) Create/repair config (init)
  0) Quit
===================================================
```

### Interactive confirmation & config completion

When **you** run a sync from a terminal, the tool reads the config, shows you the pair
(source, target, mode, scope, folders, delta start), and asks you to confirm. If anything is
**missing or invalid**, it prompts you for it and **writes your answers back to `config.yaml`** —
so you can either hand-edit the YAML or let the wizard build it. You can also run the wizard
directly: `tidysync configure`.

When the **scheduler** runs it (no terminal, and it passes `--yes`), it skips all prompts and
runs straight from the config.

### Command-line (for scripts and the scheduler)

```bash
# On-demand
tidysync run projects --since 2026-06-01     # first run needs an explicit start point
tidysync run projects                        # later runs resume from last-sync automatically
tidysync run projects --dry-run              # report only, transfer nothing
tidysync run projects --yes                  # skip the confirmation prompt
tidysync run-all                             # every pair

# Status & reports
tidysync status                              # last-sync time + latest report per pair
# reports are written to ./reports/<pair>_<timestamp>.{html,csv,json}

# Scheduling (Windows Task Scheduler)
tidysync schedule projects --every 30m       # every 30 minutes
tidysync schedule projects --daily 02:00     # daily at 02:00
tidysync unschedule projects
```

On Linux/macOS, schedule with cron instead, e.g.:
`*/30 * * * * tidysync --config /path/config.yaml run projects`

## Screenshots & user guide

_Coming soon._ Once TidySync has been validated against live Google Drive / OneDrive accounts,
this section will include annotated screenshots of the menu, a sync report, and a dedupe report,
plus a step-by-step walkthrough — with any private information (account names, file paths)
blurred. Want to help? Run it against your own accounts and open an issue with feedback.

## Configuration

See [`config.example.yaml`](config.example.yaml). Per pair:

| key      | values                                             |
|----------|----------------------------------------------------|
| `mode`   | `left-to-right`, `right-to-left`, `two-way`        |
| `scope`  | `whole-drive`, or `folders` + a `folders:` list    |
| `delta.since` | `last-sync`, a date (`2026-06-01`), or a duration (`720h`) |
| `filters`| optional rclone filter rules (e.g. `- *.tmp`)      |
| `dry_run`| `true`/`false`                                     |

## Duplicate detection (`dedupe`)

Finds **content-duplicate files within a single cloud** and quarantines the extras for you to
review and delete. This is the feature that sets the tool apart from plain rclone: rclone's
built-in `dedupe` only handles same-*name* files on Google Drive, whereas this finds files with
**identical content under any name, in any folder**.

Design decisions baked in:

- **By content hash only**, never by filename — the same filename in different folders can hold
  different content, so name-matching would be unsafe.
- **Per cloud only** — hashes can't be compared across providers (Google Drive uses MD5, OneDrive
  SHA1/quickXorHash), and a copy existing on *both* clouds is expected (that's the sync), not a duplicate.
- **Files only**, never folders.
- **Keeps the newest-modified** copy in each group; moves the rest to a quarantine folder.
- **Report-only by default.** Nothing is moved without `--apply`, and nothing is ever auto-deleted.

```bash
tidysync dedupe gdrive                       # report only: list duplicate groups on Google Drive
tidysync dedupe gdrive --folder "Projects"   # limit scan to a folder (repeatable)
tidysync dedupe gdrive --apply               # move older duplicates into _duplicates/ for review
tidysync dedupe onedrive --apply             # run per cloud
```

Quarantined files keep their original relative path under `_duplicates/`, so you can see where each
came from. Review that folder, then delete what you don't want. The sync engine **always excludes
`_duplicates/`**, so quarantined files are never propagated to the other cloud.

> Caveat: dedupe is per-cloud. If a duplicate path *also* exists on the other cloud, a later
> two-way sync could copy it back. Recommended flow: run dedupe, review, **delete** from quarantine,
> and dedupe each cloud as needed.

## Reports

Every run writes three files to `reports/`:

- **`.html`** — summary cards (created / updated / skipped / conflicts / errors / bytes) and a
  per-file table with direction, action, size and modified time.
- **`.csv`** — the same rows for spreadsheets.
- **`.json`** — full machine-readable run record.

Dedupe runs write a parallel `dedupe_<remote>_<timestamp>.{html,csv,json}` showing each duplicate
group with the kept vs quarantined copies, hashes, sizes and reclaimable space.

## Safety notes

- The first run of a `last-sync` pair refuses to proceed without an explicit `--since`,
  to prevent an accidental whole-drive copy.
- `--dry-run` is supported end-to-end.
- Use `filters` to exclude temp/lock files (`~$*`, `*.tmp`, etc.).
- Two-way sync compares between runs, not in real time — avoid editing the same file on both
  sides simultaneously; shorter schedules reduce the conflict window.

## License

MIT.
