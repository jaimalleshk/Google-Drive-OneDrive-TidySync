# Contributing to TidySync

Thanks for your interest! TidySync is a small, opinionated layer over
[rclone](https://rclone.org/). Bug reports, ideas, real-world test reports, and PRs are all welcome.

## Ground rules
- Be kind and constructive.
- It's **pre-release** — test changes against non-critical data, with `--dry-run` first.
- **Never commit personal data.** `config.yaml`, `reports/`, and `state/` are gitignored — keep it that way.

## Dev setup
```bash
git clone https://github.com/jaimalleshk/Google-Drive-OneDrive-TidySync
cd Google-Drive-OneDrive-TidySync
pip install -e .
```

## Run the tests
Tests are fully **offline** — rclone is stubbed, so no cloud account is needed:
```bash
python tests/test_smoke.py
python tests/test_dedupe.py
python tests/test_interactive.py
python tests/test_gdocs.py
```
CI runs these on every push and PR (Python 3.9 / 3.11 / 3.12).

## Build the standalone exe
See the README "Build a standalone executable" section (`build_exe.bat`).

## Submitting changes
1. Branch from `main`.
2. Keep changes focused and match the existing style (small modules under `src/tidysync/`).
3. Add or adjust a test when you change behaviour.
4. Open a PR using the template — say *what* changed and *why*.

## Architecture (quick map)
- `cli.py` — subcommands + menu wiring
- `interactive.py` — the menu and config wizard
- `syncjob.py` — orchestrates a run (scan → convert → copy → report)
- `rclone.py` — thin rclone wrapper (the only module that shells out)
- `dedupe.py` / `gdocs.py` — content dedupe / Google-doc → Office conversion
- `report.py` — HTML/CSV/JSON reports
- `config.py`, `state.py`, `schedule.py`, `progress.py` — supporting pieces

## Questions
Start a [Discussion](https://github.com/jaimalleshk/Google-Drive-OneDrive-TidySync/discussions) — no email needed.
