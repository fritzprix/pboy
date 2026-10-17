"""Prune staged inbound/outbound files under ``data_dir/files/``.

Default retention is forever: the operator deletes manually or runs
``pboy files prune``. See docs/operator.md and issue #30.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

# Subdirs created by Telegram bot inbound, CLI outbound staging, and App API media.
FILE_RETENTION_SUBDIRS: tuple[str, ...] = ("telegram", "outbound", "telegram_app")

_OLDER_THAN_RE = re.compile(r"^(\d+)([smhdw])?$", re.IGNORECASE)
_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 86400 * 7,
}


def parse_older_than(raw: str) -> timedelta:
    """Parse a retention age like ``7d``, ``24h``, ``30m``, or bare ``7`` (days)."""
    text = raw.strip().lower()
    if not text:
        raise ValueError("older-than value is empty")
    match = _OLDER_THAN_RE.fullmatch(text)
    if match is None:
        raise ValueError(
            f"Invalid older-than {raw!r}; use e.g. 7d, 24h, 30m, 2w (or bare days: 7)"
        )
    amount = int(match.group(1))
    unit = (match.group(2) or "d").lower()
    seconds = amount * _UNIT_SECONDS[unit]
    if seconds < 1:
        raise ValueError("older-than must be at least 1 second")
    return timedelta(seconds=seconds)


@dataclass(frozen=True)
class PruneResult:
    """Summary of a prune pass."""

    removed_files: int
    removed_bytes: int
    removed_dirs: int
    dry_run: bool


def prune_files_tree(
    files_root: Path,
    *,
    older_than: timedelta,
    dry_run: bool = False,
    subdirs: tuple[str, ...] = FILE_RETENTION_SUBDIRS,
    now: float | None = None,
) -> PruneResult:
    """Delete files under ``files_root/<subdir>/`` older than ``older_than`` (mtime).

    Only paths that resolve inside ``files_root`` are touched (symlinks are an
    exception: the link path under the tree may be unlinked by the link's own
    mtime; targets outside the tree are never followed for deletion).

    Empty directories that were already empty are removed only when their mtime
    is at or before the cutoff (avoids racing a daemon ``mkdir`` before the
    download write). Directories emptied by this pass are always removed.
    """
    root = files_root.expanduser().resolve()
    if not root.is_dir():
        return PruneResult(0, 0, 0, dry_run)

    cutoff = (now if now is not None else time.time()) - older_than.total_seconds()
    removed_files = 0
    removed_bytes = 0
    removed_dirs = 0
    # Paths removed (or counted as removed in dry-run) so parent emptiness
    # accounts for children that would disappear this pass.
    removed_paths: set[Path] = set()
    # Parents of removed files/dirs — still remove even if mtime became "now".
    emptied_parents: set[Path] = set()

    for name in subdirs:
        base = (root / name).resolve()
        try:
            base.relative_to(root)
        except ValueError:
            continue
        if not base.is_dir():
            continue

        # Deepest-first so parents see children already marked removed.
        for path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_symlink():
                # Link lives under base via rglob; do not require the target
                # to resolve inside files_root (we never delete the target).
                try:
                    mtime = path.lstat().st_mtime
                except OSError:
                    continue
                if mtime > cutoff:
                    continue
                if not dry_run:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        continue
                removed_files += 1
                removed_paths.add(path)
                emptied_parents.add(path.parent)
                continue

            try:
                resolved = path.resolve(strict=False)
                resolved.relative_to(root)
            except (ValueError, OSError):
                continue

            if path.is_file():
                try:
                    st = path.stat()
                except OSError:
                    continue
                if st.st_mtime > cutoff:
                    continue
                removed_files += 1
                removed_bytes += max(st.st_size, 0)
                if not dry_run:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        continue
                removed_paths.add(path)
                emptied_parents.add(path.parent)
                continue

            if path.is_dir():
                try:
                    children = list(path.iterdir())
                except OSError:
                    continue
                remaining = [child for child in children if child not in removed_paths]
                if remaining:
                    continue
                try:
                    if path.resolve() == base:
                        continue
                except OSError:
                    continue
                # Already-empty dirs: only drop when older than cutoff (mkdir race).
                # Dirs we just emptied: drop regardless of mtime bump from unlinks.
                if path not in emptied_parents:
                    try:
                        if path.stat().st_mtime > cutoff:
                            continue
                    except OSError:
                        continue
                if not dry_run:
                    try:
                        path.rmdir()
                    except OSError:
                        continue
                removed_dirs += 1
                removed_paths.add(path)
                emptied_parents.add(path.parent)

    return PruneResult(
        removed_files=removed_files,
        removed_bytes=removed_bytes,
        removed_dirs=removed_dirs,
        dry_run=dry_run,
    )


__all__ = [
    "FILE_RETENTION_SUBDIRS",
    "PruneResult",
    "parse_older_than",
    "prune_files_tree",
]
