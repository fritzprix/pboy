#!/usr/bin/env python3
"""Prepare PR parameters for Pageboy (`pboy`): resolve base branch and closing issues.

Usage:
    # Implicit base: automatically selects the latest dev/M.m.x branch
    python .cursor/skills/create-pr/scripts/prepare_pr.py

    # Explicit base branch specified by user (e.g., `create pr main` or `create pr dev/0.1.x`)
    python .cursor/skills/create-pr/scripts/prepare_pr.py main
    python .cursor/skills/create-pr/scripts/prepare_pr.py --base main
    python .cursor/skills/create-pr/scripts/prepare_pr.py --issue 18 --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

# Matches dev/0.2.x (preferred minor train) or dev/0.2.0 (numeric patch fallback)
DEV_BRANCH_RE = re.compile(r"^(?:origin/)?dev/(\d+)\.(\d+)\.(x|\d+)$")
BRANCH_ISSUE_RE = re.compile(r"(?:^|/|-|#)(\d+)(?=-|$)")
COMMIT_ISSUE_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|#)\s*#?(\d+)\b",
    re.IGNORECASE,
)


def _git(*args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def find_latest_dev_branch() -> str:
    """Return the highest ``dev/M.m.x`` (or ``dev/M.m.p``) branch across origin and local refs."""
    refs_out = _git(
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/remotes/origin/dev/",
        "refs/heads/dev/",
    )
    # Sort key: (major, minor, is_x_wildcard, patch_int) so dev/0.2.x ranks above dev/0.2.0
    candidates: dict[tuple[int, int, int, int], str] = {}
    for line in refs_out.splitlines():
        ref = line.strip()
        m = DEV_BRANCH_RE.match(ref)
        if not m:
            continue
        major = int(m.group(1))
        minor = int(m.group(2))
        patch_tok = m.group(3)
        is_x = 1 if patch_tok == "x" else 0
        patch_num = -1 if patch_tok == "x" else int(patch_tok)
        clean_name = f"dev/{major}.{minor}.{patch_tok}"
        candidates[(major, minor, is_x, patch_num)] = clean_name

    if not candidates:
        raise SystemExit(
            "No dev/M.m.x branch found in local or origin refs. "
            "Run `git fetch origin` or create a `dev/M.m.x` branch first."
        )
    latest_key = max(candidates)
    return candidates[latest_key]


def normalize_base_branch(raw: str) -> str:
    """Strip leading ``origin/`` if the caller passed a remote-tracking name."""
    cleaned = raw.strip()
    if cleaned.startswith("origin/"):
        cleaned = cleaned[len("origin/") :]
    return cleaned


def current_branch() -> str:
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise SystemExit("Detached HEAD: checkout a feature branch before creating a PR.")
    return branch


def infer_issues(head: str, base: str, explicit_issues: list[int]) -> list[int]:
    """Collect unique issue numbers from explicit flags, branch name, and commits since base."""
    seen: list[int] = []

    def _add(num: int) -> None:
        if num > 0 and num not in seen:
            seen.append(num)

    for n in explicit_issues:
        _add(n)

    # 1. Branch name (e.g. docs/18-operator-guide, feat/27-hook-debug)
    for m in BRANCH_ISSUE_RE.finditer(head):
        _add(int(m.group(1)))

    # 2. Commit messages in base..HEAD
    base_ref = f"origin/{base}"
    try:
        _git("rev-parse", "--verify", base_ref)
    except subprocess.CalledProcessError:
        base_ref = base

    try:
        log_out = _git("log", f"{base_ref}..HEAD", "--format=%s%n%b")
        for m in COMMIT_ISSUE_RE.finditer(log_out):
            _add(int(m.group(1)))
    except subprocess.CalledProcessError:
        pass

    return seen


def format_closing_lines(issues: list[int]) -> str:
    """Format GitHub auto-close keywords (each issue must have its own keyword)."""
    return "\n".join(f"Closes #{num}" for num in issues)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target_base",
        nargs="?",
        help="Optional target base branch (e.g. `main` or `dev/0.1.x`). "
        "If omitted, defaults to the latest `dev/M.m.x` branch.",
    )
    parser.add_argument(
        "--base",
        help="Explicit base branch (overrides positional target_base; defaults to highest dev/M.m.x)",
    )
    parser.add_argument(
        "--issue",
        "-i",
        type=int,
        action="append",
        default=[],
        help="Issue number(s) to auto-close (repeatable; also inferred from branch/commits)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output",
    )
    args = parser.parse_args()

    head = current_branch()
    explicit_base = args.base or args.target_base
    base = normalize_base_branch(explicit_base) if explicit_base else find_latest_dev_branch()
    if head == base:
        raise SystemExit(f"Current branch {head!r} cannot be the PR head against itself ({base!r}).")

    issues = infer_issues(head, base, args.issue)
    closing = format_closing_lines(issues)

    payload = {
        "head": head,
        "base": base,
        "base_source": "explicit" if explicit_base else "latest_dev",
        "issues": issues,
        "closing_keywords": closing,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return

    print(f"head:    {head}")
    print(f"base:    {base} ({payload['base_source']})")
    print(f"issues:  {', '.join(f'#{i}' for i in issues) if issues else '(none detected — pass --issue N)'}")
    if closing:
        print("closing:")
        for line in closing.splitlines():
            print(f"  {line}")
    else:
        print(
            "warning: no issue numbers detected from branch/commits. "
            "Confirm with the user or pass --issue <N> so GitHub auto-close triggers.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
