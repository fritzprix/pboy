#!/usr/bin/env python3
"""Bump Pageboy SemVer in ``src/pboy/__init__.py`` and scaffold ``CHANGELOG.md``.

Usage:
    python .cursor/skills/release/scripts/bump_version.py patch|minor|major
    python .cursor/skills/release/scripts/bump_version.py patch --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
INIT_PATH = REPO_ROOT / "src" / "pboy" / "__init__.py"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_semver(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.match(version)
    if not match:
        raise SystemExit(f"unsupported version (need X.Y.Z): {version!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump(version: str, kind: str) -> str:
    major, minor, patch = parse_semver(version)
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "major":
        return f"{major + 1}.0.0"
    raise SystemExit(f"unknown bump kind: {kind}")


def read_version(text: str) -> str:
    match = VERSION_RE.search(text)
    if not match:
        raise SystemExit(f"could not find __version__ in {INIT_PATH}")
    return match.group(1)


def write_version(text: str, new_version: str) -> str:
    updated, n = VERSION_RE.subn(f'__version__ = "{new_version}"', text, count=1)
    if n != 1:
        raise SystemExit("failed to rewrite __version__")
    return updated


def update_changelog(text: str, old_version: str, new_version: str, today: str) -> str:
    """Move [Unreleased] body into a new version section; refresh compare links."""
    unreleased_header = "## [Unreleased]"
    if unreleased_header not in text:
        raise SystemExit("CHANGELOG.md missing ## [Unreleased]")

    # Split off link footer (starts at first [Unreleased]: URL line).
    link_start = text.find("\n[Unreleased]:")
    if link_start < 0:
        link_start = text.find("[Unreleased]:")
        body = text[:link_start].rstrip() + "\n\n" if link_start >= 0 else text.rstrip() + "\n\n"
    else:
        body = text[:link_start].rstrip() + "\n\n"

    # Extract notes under Unreleased until next ## [
    after = body.split(unreleased_header, 1)[1]
    next_section = re.search(r"\n## \[", after)
    if next_section:
        notes = after[: next_section.start()].strip("\n")
        rest = after[next_section.start() :]
    else:
        notes = after.strip("\n")
        rest = ""

    notes_block = notes.strip()
    if not notes_block:
        notes_block = "### Changed\n\n- (describe this release)"

    new_body = (
        f"{body.split(unreleased_header, 1)[0]}{unreleased_header}\n\n"
        f"## [{new_version}] — {today}\n\n"
        f"{notes_block}\n"
        f"{rest}"
    )
    if not new_body.endswith("\n"):
        new_body += "\n"

    footer = (
        f"[Unreleased]: https://github.com/fritzprix/pboy/compare/v{new_version}...HEAD\n"
        f"[{new_version}]: https://github.com/fritzprix/pboy/compare/v{old_version}...v{new_version}\n"
    )
    # Preserve older version links if present.
    old_footer = text[link_start:].lstrip("\n") if link_start >= 0 else ""
    preserved: list[str] = []
    for line in old_footer.splitlines():
        if line.startswith("[Unreleased]:"):
            continue
        if line.startswith(f"[{new_version}]:"):
            continue
        if line.strip():
            preserved.append(line)
    if preserved:
        footer += "\n".join(preserved) + "\n"

    return new_body.rstrip() + "\n\n" + footer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("patch", "minor", "major"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Changelog date (YYYY-MM-DD), default today",
    )
    args = parser.parse_args()

    init_text = INIT_PATH.read_text(encoding="utf-8")
    old = read_version(init_text)
    new = bump(old, args.kind)

    if args.kind == "major" and parse_semver(old)[0] == 0:
        print(
            "note: major bump leaves 0.x Alpha -> "
            f"{new}. Confirm leaving Alpha / Trove classifier if intentional.",
            file=sys.stderr,
        )

    print(f"{old} -> {new} ({args.kind})")

    new_init = write_version(init_text, new)
    new_changelog = update_changelog(
        CHANGELOG_PATH.read_text(encoding="utf-8"),
        old_version=old,
        new_version=new,
        today=args.date,
    )

    if args.dry_run:
        for line in new_init.splitlines():
            if "__version__" in line:
                print(f"init: {line}")
        marker = f"## [{new}]"
        lines = new_changelog.splitlines()
        try:
            start = next(i for i, line in enumerate(lines) if line.startswith(marker))
        except StopIteration as exc:
            raise SystemExit("new changelog section missing") from exc
        print("changelog section:")
        for line in lines[start : start + 12]:
            print(line.encode("ascii", "replace").decode("ascii"))
        return

    INIT_PATH.write_text(new_init, encoding="utf-8", newline="\n")
    CHANGELOG_PATH.write_text(new_changelog, encoding="utf-8", newline="\n")
    print(f"updated {INIT_PATH.relative_to(REPO_ROOT)}")
    print(f"updated {CHANGELOG_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
