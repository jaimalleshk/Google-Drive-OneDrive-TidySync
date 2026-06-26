# Launch post (draft)

Ready-to-adapt copy for sharing TidySync. Pick the variant for your channel.
Replace the alpha caveat once live testing is complete.

---

## Show HN / Hacker News (title + body)

**Title:** Show HN: TidySync – private, free Google Drive ⇄ OneDrive sync + dedupe (built on rclone)

I kept two clouds (Google Drive + OneDrive) and got tired of two problems: keeping them
in sync without a paid service that routes my files through *their* servers, and the pile of
duplicate files I'd accumulated across folders.

So I built **TidySync** — a small CLI on top of rclone that does both, privately:

- **Two-way delta sync** that never deletes (newest-wins) and refuses an accidental full-drive copy.
- **Content-hash duplicate detection** — finds identical files under *any* name/folder, keeps the
  newest, and moves the rest to a `_duplicates/` folder for review (it never auto-deletes).
- **Google Workspace → Office conversion**, so native Google Docs land on OneDrive as editable
  `.docx/.xlsx/.pptx` instead of useless link stubs.
- One menu-driven command, per-run HTML/CSV/JSON reports, scheduling, and a standalone `.exe`.

Files move cloud ↔ cloud directly through your own OAuth — nothing passes through a third-party
server, and it's free and open source (MIT).

It's early/alpha and I'd love feedback, especially real-world test reports.
Repo: https://github.com/jaimalleshk/Google-Drive-OneDrive-TidySync

---

## Reddit (r/selfhosted, r/DataHoarder)

**Title:** I built a free, private Google Drive ⇄ OneDrive sync + content-dedupe tool (open source)

Hosted cloud-to-cloud tools work, but they're metered/paid and route your files through their
servers. I wanted something that runs locally, uses my own OAuth, and also cleans up the duplicate
mess across my folders — so I made **TidySync** (MIT, built on rclone):

- Two-way delta sync, **never deletes**, newest-wins, dry-run first.
- **Content-hash** duplicate detection (not filename) → quarantine-for-review, never auto-delete.
- Converts native Google Docs/Sheets/Slides to Office files so they're usable on OneDrive.
- Menu-driven, per-run reports, scheduling, optional standalone exe.

Direct cloud ↔ cloud via your own credentials — nothing through a third party.

It's alpha and feedback/test reports are very welcome. Repo in comments / here:
https://github.com/jaimalleshk/Google-Drive-OneDrive-TidySync

---

## dev.to / blog (outline)

**Title:** Why I built a private Google Drive ⇄ OneDrive sync (and what rclone taught me)

1. **The problem** — two clouds, paid sync services that touch your data, duplicate sprawl.
2. **Why not just rclone?** — it's the engine, but the safe path is buried in flags and footguns.
3. **The design decisions** — never-delete (`copy --update`), timestamp-windowed delta, content-hash
   dedupe with quarantine, Google-doc conversion, safe-by-default + dry-run.
4. **The honest part** — it's a layer on rclone; the dedupe + reporting + UX are the contribution.
   Alpha, validated logic, still proving against live accounts.
5. **What I learned** — OAuth quotas, mtime preservation vs. ping-pong, why two-way sync is scary,
   recovering from my own bug.
6. **Try it / contribute** — link, issues, discussions.

> Tip: a 10-second terminal GIF (record with asciinema or ScreenToGif) of the menu + a sync at the
> top of the post massively boosts engagement.
