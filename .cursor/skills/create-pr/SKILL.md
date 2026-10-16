---
name: create-pr
description: >-
  Create or update a GitHub Pull Request for Pageboy (pboy). If the user specifies
  a target branch (e.g. `create pr main`), use that branch as base; if omitted,
  implicitly use the latest dev/M.m.x branch (e.g. dev/0.2.x) as base. Always bind
  related issues with GitHub auto-close keywords (Closes #N).
---

# Create Pull Request (`pboy`)

## Trigger

Use this skill whenever the user asks to:

- `create pr` / `open pr` / `submit pr` / `PR 만들어줘` / `PR 올려줘`
- `create pr <branch>` (e.g. `create pr main`, `create pr dev/0.2.x`)
- Update an existing PR's base branch or linked closing issues.

## Core Rules (Always Enforce)

1. **Base branch resolution**:
   - **When a branch is specified** (e.g. `create pr main`, `main으로 PR 올려줘`): use the user-specified branch as `--base <branch>`.
   - **When no branch is specified** (e.g. `create pr`, `PR 올려줘`): **implicitly** use the latest `dev/M.m.x` branch (highest minor train among `dev/*` branches, e.g. `dev/0.2.x`) as `--base`. Never fall back to GitHub's default (`main`) implicitly.
2. **Auto-close keywords for all related issues**:
   - Every related issue MUST be bound in the PR body using GitHub's closing keywords (`Closes #N`, `Fixes #N`, or `Resolves #N`).
   - When closing multiple issues, repeat the keyword for **each** issue (e.g. `Closes #18`, `Closes #27` on separate lines or comma-separated `Closes #18, Closes #27` — never `Closes #18, #27`, which GitHub ignores for the second issue).
   - Note: GitHub auto-closes issues immediately when a PR merges into the default branch (`main`), or when the `dev/M.m.x` branch is later merged into `main`. Keeping `Closes #N` in both the commit message and PR body ensures GitHub links the PR to the issue timeline and triggers auto-close reliably.

## Workflow

Copy and track:

```
Create PR:
- [ ] Fetch latest remote refs (`git fetch origin`)
- [ ] Resolve base branch (explicit `<branch>` if given, else latest `dev/M.m.x`) & related issue numbers
- [ ] Verify diff against `origin/<base>` (no unintended commits)
- [ ] Push current head branch to `origin`
- [ ] Create (or update) PR with `--base <base>` and `Closes #N` in body
```

### 1. Resolve Base Branch and Issues

Run the helper script from repo root after fetching remote refs:

```bash
git fetch origin

# Case A: No base branch specified by user -> implicitly selects latest dev/M.m.x
python .cursor/skills/create-pr/scripts/prepare_pr.py

# Case B: User specified a target base branch (e.g. `create pr main`)
python .cursor/skills/create-pr/scripts/prepare_pr.py main

# Optional: pass additional issue numbers explicitly
python .cursor/skills/create-pr/scripts/prepare_pr.py --issue 18
```

The script:
- Uses the positional `<target_base>` (or `--base <branch>`) when provided; otherwise finds all `dev/M.m.x` (and `dev/M.m.p`) branches across `origin/dev/*` and local `dev/*` and selects the highest minor train (e.g. `dev/0.2.x`).
- Extracts issue numbers from the current branch name (e.g. `feat/27-...` → `#27`, `docs/18-...` → `#18`) and commit messages between `origin/<base>..HEAD`.
- Prints the resolved `base` and formatted `Closes #N` lines.

If no issue number is detected, check `gh issue list` or ask the user which issue(s) this PR resolves before creating the PR.

### 2. Verify Commits & Diff Against Base

Confirm only the intended commits are included between `origin/<base>` and `HEAD`:

```bash
git log origin/<base>..HEAD --oneline
git diff origin/<base>..HEAD --stat
```

### 3. Push Head Branch

Ensure the current branch is pushed to `origin`:

```bash
git push -u origin HEAD
```

### 4. Create (or Update) the PR

Create the PR with `gh pr create`, passing `--base <base>` and including the `Closes #N` block in the body:

```bash
gh pr create \
  --base "<base>" \
  --title "<concise title>" \
  --body "$(cat <<'EOF'
## Summary
- <bullet points describing changes>

Closes #<issue_1>

## Test plan
- [x] `uv run pytest -q`
- [x] `uv run ruff check src tests`
- [x] `uv run mypy src`
EOF
)"
```

#### Updating an existing PR

If a PR already exists for the branch (e.g. opened against the wrong base or missing closing keywords):

```bash
gh pr edit <pr_number> --base "<base>" --body "<updated body with Closes #N>"
```

## Do Not

- Do **not** omit `--base` on `gh pr create`.
- Do **not** write `Closes #12, #34` without repeating the keyword — always write `Closes #12` and `Closes #34` individually.
- Do **not** guess issue numbers; verify with `prepare_pr.py` or `gh issue view <N>`.
