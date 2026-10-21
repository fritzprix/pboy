"""Tests for staged-file retention prune helpers and CLI."""

from __future__ import annotations

import os
import time
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

import pboy.core.config as config_mod
from pboy.cli.main import app
from pboy.core.files_retention import parse_older_than, prune_files_tree


def test_parse_older_than_units() -> None:
    assert parse_older_than("7d") == timedelta(days=7)
    assert parse_older_than("24h") == timedelta(hours=24)
    assert parse_older_than("30m") == timedelta(minutes=30)
    assert parse_older_than("2w") == timedelta(weeks=2)
    assert parse_older_than("7") == timedelta(days=7)
    with pytest.raises(ValueError, match="Invalid older-than"):
        parse_older_than("yesterday")


def test_prune_files_tree_removes_old_only(tmp_path: Path) -> None:
    files = tmp_path / "files"
    old = files / "telegram" / "1" / "old.bin"
    fresh = files / "outbound" / "fresh.bin"
    keep_root_child = files / "other" / "skip.bin"
    old.parent.mkdir(parents=True)
    fresh.parent.mkdir(parents=True)
    keep_root_child.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    fresh.write_bytes(b"new")
    keep_root_child.write_bytes(b"x")

    now = time.time()
    past = now - 10_000
    os.utime(old, (past, past))
    os.utime(fresh, (now, now))
    os.utime(keep_root_child, (past, past))

    dry = prune_files_tree(
        files, older_than=timedelta(seconds=60), dry_run=True, now=now
    )
    assert dry.removed_files == 1
    assert dry.removed_dirs == 1  # telegram/1 would empty after the file goes
    assert old.is_file() and fresh.is_file()

    result = prune_files_tree(
        files, older_than=timedelta(seconds=60), dry_run=False, now=now
    )
    assert result.removed_files == 1
    assert result.removed_dirs == 1
    assert result.removed_bytes == 3
    assert not old.is_file()
    assert fresh.is_file()
    assert keep_root_child.is_file()
    assert not (files / "telegram" / "1").exists()


def test_prune_skips_fresh_empty_dir_and_outside_symlink(tmp_path: Path) -> None:
    files = tmp_path / "files"
    fresh_empty = files / "telegram" / "brand-new"
    old_empty = files / "telegram" / "stale-empty"
    fresh_empty.mkdir(parents=True)
    old_empty.mkdir(parents=True)

    outside = tmp_path / "secret.bin"
    outside.write_bytes(b"keep-me")
    link = files / "outbound" / "leak"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    now = time.time()
    past = now - 10_000
    os.utime(fresh_empty, (now, now))
    os.utime(old_empty, (past, past))
    # follow_symlinks is not supported on Windows
    if os.utime in os.supports_follow_symlinks:
        os.utime(link, (past, past), follow_symlinks=False)
    else:
        # On Windows, utime follows symlinks by default
        os.utime(link, (past, past))

    result = prune_files_tree(
        files, older_than=timedelta(seconds=60), dry_run=False, now=now
    )
    assert fresh_empty.is_dir()
    assert not old_empty.exists()
    assert result.removed_dirs == 1
    # Symlink itself is removed by age; target outside files_root is untouched.
    assert not link.exists()
    assert outside.is_file() and outside.read_bytes() == b"keep-me"


def test_stage_reply_mtime_is_fresh_so_prune_spares_it(tmp_path: Path) -> None:
    from pboy.models.reply_file import stage_reply_file_for_daemon

    root = tmp_path / "files"
    src = tmp_path / "old-report.pdf"
    src.write_bytes(b"pdf")
    past = time.time() - 86_400 * 21
    os.utime(src, (past, past))

    staged = stage_reply_file_for_daemon(src, files_root=root)
    assert staged.stat().st_mtime > past + 86_400 * 7

    now = time.time()
    result = prune_files_tree(
        root, older_than=timedelta(days=7), dry_run=False, now=now
    )
    assert result.removed_files == 0
    assert staged.is_file()


def test_cli_files_prune(cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    files = data_dir / "files" / "telegram" / "9"
    files.mkdir(parents=True)
    target = files / "gone.bin"
    target.write_bytes(b"abc")
    past = time.time() - 86_400 * 10
    os.utime(target, (past, past))

    monkeypatch.setenv("PB_DATA_DIR", str(data_dir.resolve()))
    monkeypatch.setenv("PB_CONFIG_DIR", str((tmp_path / "cfg").resolve()))
    monkeypatch.setenv("PB_SECRET_KEY", "fixed_files_prune_secret_key_32b!")
    monkeypatch.setattr(config_mod, "_settings", None)

    dry = cli_runner.invoke(app, ["files", "prune", "--older-than", "7d", "--dry-run"])
    assert dry.exit_code == 0
    assert "would remove 1 file" in dry.stdout
    assert target.is_file()

    real = cli_runner.invoke(app, ["files", "prune", "--older-than", "7d"])
    assert real.exit_code == 0
    assert "removed 1 file" in real.stdout
    assert not target.is_file()
