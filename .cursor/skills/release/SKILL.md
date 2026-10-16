---
name: release
description: >-
  Cut a Pageboy (pboy) PyPI release with SemVer bump patch, minor, or major:
  version file, CHANGELOG, commit, tag v*, push, watch GitHub Actions Release,
  verify uvx. Use when the user says release patch, release minor, release major,
  or asks to publish/bump a pboy version to PyPI.
---

# Release Pageboy (`pboy`)

## Trigger

User says one of:

- `release patch` — release with patch version bumping (`X.Y.Z` → `X.Y.(Z+1)`)
- `release minor` — release with minor version bumping (`X.Y.Z` → `X.(Y+1).0`)
- `release major` — release with major version bumping (`X.Y.Z` → `(X+1).0.0`)

Optional: `--dry-run` / `no-push` — stop before `git push` / tag push.

## Bump policy (this repo)

| Command | Bump | Rule | Example |
|---------|------|------|---------|
| `release patch` | `patch` | bug fix, docs, CI | `0.1.0` → `0.1.1` |
| `release minor` | `minor` | feature **or** breaking change while on `0.x` | `0.1.0` → `0.2.0` |
| `release major` | `major` | leave Alpha / first stable (`0.x` → `1.0.0`) or post-1.0 breaking | `0.2.0` → `1.0.0` |

On `major` from `0.x`, remind the user to consider bumping the Trove classifier in `pyproject.toml` away from Alpha (only if they intend to leave Alpha).

Source of truth: `src/pboy/__init__.py` → `__version__` (hatch dynamic). Process detail: [`docs/releasing.md`](../../../docs/releasing.md).

## Preconditions (fail fast)

1. Repo root is `pboy`; working tree clean (or only intentional release edits).
2. Fetch remote refs (`git fetch origin`).
3. Ensure `main` has the intended release commits:
   - Check `git log origin/main..origin/dev/0.2.x --oneline` (or latest `dev/M.m.x` train). If the user is releasing work accumulated on `dev/M.m.x` that hasn't been merged into `main` yet, merge or fast-forward `origin/dev/M.m.x` into `main` (or confirm with the user) before cutting the release commit on `main`.
4. Switch to `main` and ensure it is up to date with `origin/main`.
5. Prefer CI green on latest `main` (`gh run list --branch main --limit 5`).
6. GitHub Environment `pypi` exists; PyPI Trusted Publisher is active at
   https://pypi.org/manage/project/pboy/settings/publishing/
   (`fritzprix/pboy` + `release.yml` + environment `pypi`).

## Workflow

Copy and track:

```
Release:
- [ ] Precheck branch (`main` synced with `origin/main` + intended `dev/M.m.x` train if applicable)
- [ ] Bump version (`patch` | `minor` | `major`) + CHANGELOG
- [ ] Fill changelog notes (no placeholder left)
- [ ] Commit on main (`Release X.Y.Z.`)
- [ ] Push main
- [ ] Annotated tag vX.Y.Z + push tag
- [ ] Watch Release workflow
- [ ] Verify PyPI + uvx
```

### 1. Bump

From repo root:

```bash
python .cursor/skills/release/scripts/bump_version.py patch   # or minor|major
# preview only:
python .cursor/skills/release/scripts/bump_version.py patch --dry-run
```

Script updates:

- `src/pboy/__init__.py` `__version__`
- `CHANGELOG.md`: moves `[Unreleased]` notes into `## [X.Y.Z] — YYYY-MM-DD`, resets Unreleased, refreshes compare links

**Then edit CHANGELOG** so the new section is accurate (replace any `(describe this release)` placeholder). Summarize commits since the previous tag:

```bash
git log v<old>..HEAD --oneline
```

### 2. Commit

```text
Release X.Y.Z.
```

Stage only release files (`__init__.py`, `CHANGELOG.md`, and any intentional Alpha classifier edit).

### 3. Tag and push

```bash
git push origin main
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

Tag must match `__version__` exactly (Release workflow enforces this).

### 4. Watch publish

```bash
gh run watch $(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
```

Jobs: `build` → `publish` (OIDC → PyPI) → GitHub Release.

If `build` fails on version parse, fix `.github/workflows/release.yml` quoting (use heredoc, not nested `python -c` quotes). If `publish` fails on Trusted Publisher, check env name is exactly `pypi`.

### 5. Verify

```bash
uvx pboy@X.Y.Z --help
# or
uvx --from pboy==X.Y.Z pboy --help
```

Optional: comment/close packaging issues; confirm https://pypi.org/project/pboy/X.Y.Z/

## Do not

- Force-push tags that already published to PyPI (yank/re-release is a separate decision).
- Skip CHANGELOG polish when Unreleased was empty.
- Publish with a dirty tree that mixes unrelated WIP.
- Use `major` for routine `0.x` breaking changes — use `minor` per repo policy.
