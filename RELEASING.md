# Releasing dynet-cli

Releases go to [PyPI](https://pypi.org/project/dynet-cli/) from GitHub
Actions, using PyPI trusted publishing: no API token is stored anywhere.
Publishing a GitHub release whose tag is `vX.Y.Z` does everything else,
including attaching a single-file `dynet.pyz` to the release.

## Choosing the version

dynet-cli follows [semantic versioning](https://semver.org/). Its "API" is
what scripts and people rely on: command names, options, exit codes, the JSON
output, and the capture file format. Before 1.0:

- **Patch** (`0.1.0` → `0.1.1`): bug fixes, new options or commands, and
  describing more message types.
- **Minor** (`0.1.0` → `0.2.0`): renaming or removing a command or option,
  changing exit codes, or changing the JSON output or capture file format in
  a way that breaks existing readers.

Capture files people have already sent in must stay readable by
`dynet decode`.

## If it needs a new aiodynet

dynet-cli installs aiodynet from PyPI. If this release needs features from an
aiodynet release:

1. Release aiodynet first (see its `RELEASING.md`), and wait until
   <https://pypi.org/project/aiodynet/> shows the new version.
2. Raise the requirement in `pyproject.toml` (for example
   `"aiodynet>=0.2.0"`), then update the lockfile:

   ```sh
   uv lock --upgrade-package aiodynet
   uv sync && uv run pytest
   ```

## Steps

1. **Start from a green `main`.** Check that the latest CI run on `main`
   passed:

   ```sh
   git switch main && git pull
   gh run list --branch main --limit 1
   ```

2. **Bump the version.** This updates both `pyproject.toml` and `uv.lock`:

   ```sh
   uv version --bump patch      # or: minor, major, or an exact version
   ```

3. **Update `CHANGELOG.md`.** Add a section at the top in this form:

   ```markdown
   ## 0.1.1 (2026-12-01)

   - What changed, written for people using the tool.
   ```

4. **Commit and push**, then wait for CI to pass on that commit:

   ```sh
   git commit -am "Release 0.1.1"
   git push
   gh run watch "$(gh run list --branch main --limit 1 --json databaseId -q '.[0].databaseId')"
   ```

5. **Publish the GitHub release.** The tag must be `v` plus the exact
   version in `pyproject.toml`:

   ```sh
   gh release create v0.1.1 --target main --title "dynet-cli 0.1.1" \
     --notes "Short summary; see CHANGELOG.md."
   ```

   Or use *Releases → Draft a new release* on GitHub, create the tag
   `v0.1.1` on `main`, and click *Publish release*. Saving a draft doesn't
   start anything; only publishing does.

6. **Watch the release workflow** until it succeeds:

   ```sh
   gh run watch "$(gh run list --workflow release.yml --limit 1 --json databaseId -q '.[0].databaseId')"
   ```

7. **Check the result.** Install it the way the capture guide tells people
   to, from a clean environment:

   ```sh
   uv tool run --isolated --no-cache --from dynet-cli==0.1.1 dynet --version
   curl -sLO https://github.com/sdunster/dynet-cli/releases/latest/download/dynet.pyz
   python3 dynet.pyz --version
   ```

   The GitHub release should have the wheel, sdist and `dynet.pyz` attached.

## What the release workflow does

[`.github/workflows/release.yml`](.github/workflows/release.yml) runs when a
release is *published*. In order, it:

1. fails unless the tag is exactly `v` plus the version in `pyproject.toml`;
2. installs from the lockfile (`uv sync --locked`) and runs the tests;
3. builds the sdist and wheel, and checks them with `twine check --strict`;
4. publishes to PyPI with trusted publishing (`uv publish --trusted-publishing always`);
5. builds `dynet.pyz` with `scripts/build-pyz`, which takes aiodynet from PyPI;
6. attaches the sdist, wheel and `dynet.pyz` to the GitHub release.

The job runs in the `pypi` GitHub environment, which only accepts tags
matching `v*`. A workflow run from a branch can't publish.

The capture guide links to
`releases/latest/download/dynet.pyz`, so every release must have a
`dynet.pyz` attached, or that link breaks.

## When something goes wrong

- **The workflow fails before "publish"** (wrong tag, a failing test, a bad
  build). Nothing reached PyPI. Fix the problem on `main`, then delete the
  release and its tag and create them again:

  ```sh
  gh release delete v0.1.1 --cleanup-tag --yes
  ```

- **Publishing fails with a trusted-publishing error.** The PyPI side of the
  setup is missing or wrong; see [Trusted publisher](#trusted-publisher)
  below. Fix it on PyPI, then re-run the failed job
  (`gh run rerun <run-id> --failed`).

- **PyPI has the release but building or attaching `dynet.pyz` failed.**
  Don't re-run the job, because PyPI rejects a second upload of the same
  version. Build locally from the tag and attach the files by hand:

  ```sh
  git switch --detach v0.1.1
  rm -rf dist && uv build && mkdir -p release-files && mv dist/* release-files/
  scripts/build-pyz && mv dist/dynet.pyz release-files/
  gh release upload v0.1.1 release-files/* --clobber
  git switch main && rm -rf release-files
  ```

- **A broken version reached PyPI.** A version number can never be uploaded
  twice, even after deleting it. Release a fixed patch version. If the broken
  one is harmful, also *yank* it on PyPI (project → *Manage* → *Releases* →
  *Options* → *Yank*), which hides it from new installs without breaking
  existing pins.

## Trusted publisher

PyPI accepts uploads from this repository through a trusted publisher. If it
ever needs recreating, it's under *dynet-cli → Manage → Publishing* on PyPI,
with these settings:

| Field | Value |
|---|---|
| Owner | `sdunster` |
| Repository name | `dynet-cli` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

The GitHub side is the `pypi` environment in *Settings → Environments*. Its
deployment rule allows only tags matching `v*`.
