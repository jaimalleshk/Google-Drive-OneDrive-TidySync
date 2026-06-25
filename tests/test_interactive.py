"""Offline test of the interactive confirm/complete wizard (scripted input)."""

import builtins
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tidysync import interactive
from tidysync.config import load_raw, save_raw


def feed(inputs):
    it = iter(inputs)

    def fake(prompt=""):
        try:
            return next(it)
        except StopIteration:
            return ""
    return fake


def test_fill_missing():
    interactive.available_remotes = lambda: ["gdrive:", "onedrive:"]
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "config.yaml"
        save_raw(cfg, {
            "remotes": {"gdrive": "gdrive:", "onedrive": "onedrive:"},
            "pairs": [{"name": "projects", "left": "gdrive", "right": "onedrive",
                       "mode": "two-way", "scope": "folders",   # folders MISSING
                       "delta": {"since": "last-sync"}}],
        })
        # left=#1, right=#2, mode=default, scope=default, folders, since=default, proceed=y
        builtins.input = feed(["1", "2", "", "", "Projects/Active, Shared/Docs", "", "y"])
        ok = interactive.review_and_complete(cfg, "projects")
        assert ok, "should proceed"
        folders = load_raw(cfg)["pairs"][0].get("folders")
        assert folders == ["Projects/Active", "Shared/Docs"], folders
        print("fill-missing ok -> folders saved:", folders)


def test_confirm_only_no_change():
    interactive.available_remotes = lambda: ["gdrive:", "onedrive:"]
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "config.yaml"
        save_raw(cfg, {
            "remotes": {"gdrive": "gdrive:", "onedrive": "onedrive:"},
            "pairs": [{"name": "p", "left": "gdrive", "right": "onedrive",
                       "mode": "two-way", "scope": "whole-drive",
                       "delta": {"since": "24h"}}],
        })
        before = load_raw(cfg)
        builtins.input = feed(["y"])           # just confirm
        ok = interactive.review_and_complete(cfg, "p")
        assert ok
        assert load_raw(cfg) == before, "confirmed-unchanged config must not be rewritten"
        print("confirm-only ok -> no rewrite")


def test_create_new_pair_when_missing():
    interactive.available_remotes = lambda: ["gdrive:", "onedrive:"]
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "config.yaml"
        save_raw(cfg, {"remotes": {}, "pairs": []})
        # create? y ; left #1 ; right #2 ; mode default ; scope=whole-drive(#2) ; since default ; proceed y
        builtins.input = feed(["y", "1", "2", "", "2", "", "y"])
        ok = interactive.review_and_complete(cfg, "newpair")
        assert ok
        raw = load_raw(cfg)
        names = [p["name"] for p in raw["pairs"]]
        assert "newpair" in names, names
        pair = raw["pairs"][0]
        assert pair["scope"] == "whole-drive", pair
        assert raw["remotes"], "remotes should have been recorded"
        print("create-new ok -> pair:", pair["name"], "remotes:", raw["remotes"])


if __name__ == "__main__":
    test_fill_missing()
    test_confirm_only_no_change()
    test_create_new_pair_when_missing()
    print("\nALL INTERACTIVE CHECKS PASSED")
