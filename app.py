"""Entry point for the standalone PyInstaller build (produces tidysync.exe)."""

from tidysync.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
