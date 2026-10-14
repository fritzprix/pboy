# Releasing Pageboy

## Prerequisites

1. **PyPI Trusted Publisher** (OIDC, no long-lived token in GitHub secrets):
   - Project: [pypi.org/manage/project/pboy](https://pypi.org/project/pboy/) (claim the name on first publish if needed)
   - Publisher: GitHub
   - Owner: `fritzprix`, repository: `pboy`
   - Workflow: `release.yml`
   - Environment: `pypi` (matches this repo’s GitHub Environment name)
2. GitHub repo **Environment** named `pypi` (optional protection rules; required by the workflow `environment:` key).
3. Working tree clean on `main`; CI green.

## Version bump

1. Set `__version__` in [`src/pboy/__init__.py`](../src/pboy/__init__.py) (single source of truth; hatch reads it).
2. Move `[Unreleased]` notes into a new section in [`CHANGELOG.md`](../CHANGELOG.md).
3. Update compare links at the bottom of the changelog.
4. Commit on `main` (e.g. `Release 0.1.1`).

## Tag and publish

```bash
git tag -a v0.1.1 -m "v0.1.1"
git push origin main
git push origin v0.1.1
```

Pushing a `v*` tag runs [`.github/workflows/release.yml`](../.github/workflows/release.yml):

1. Build sdist + wheel
2. Upload artifacts to the GitHub Release (via softprops/action-gh-release)
3. Publish to PyPI via Trusted Publishing

## Verify

```bash
uvx pboy@0.1.1 --help
# or latest:
uvx pboy --help
```

## TestPyPI (optional dry run)

Configure a second Trusted Publisher for TestPyPI with the same workflow, or temporarily point `pypa/gh-action-pypi-publish` at TestPyPI. Prefer a one-off manual `twine upload --repository testpypi` from a local `uv build` if you only need a dry run.

## Leaving Alpha

Bump the Trove classifier in `pyproject.toml` from `Development Status :: 3 - Alpha` only when intentionally leaving Alpha — not required for the first PyPI release.
