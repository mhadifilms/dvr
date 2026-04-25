# Contributing to dvr

Thanks for your interest! `dvr` wraps a notoriously inconsistent API. Edge cases are everywhere, and contributions filling them in are exactly what this project needs.

## Setup

```bash
git clone https://github.com/mhadifilms/dvr
cd dvr
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

DaVinci Resolve must be installed for integration tests. Unit tests run without it.

## Running checks

```bash
ruff check dvr tests       # lint
ruff format dvr tests      # format
mypy dvr                   # type check
pytest                     # unit tests
pytest -m integration      # integration tests (requires running Resolve)
```

CI runs all four on macOS and Linux against Python 3.10–3.13. PRs must pass.

## Architecture

- **Library is primary, CLI is a thin wrapper.** All logic lives in `dvr/`. The CLI in `dvr/cli/` is one library call per command.
- **Every public method has a structured error.** When the underlying API returns `None` or `False`, raise a subclass of `dvr.errors.DvrError` with `cause`, `fix`, and `state` populated.
- **`inspect()` is the standard read.** Each domain class exposes `.inspect() -> dict` returning a structured snapshot. Don't make callers chain getters.
- **Idempotency.** `ensure()` methods are get-or-create. They never raise "already exists".
- **No silent `Any` returns.** Wrapper modules (those that interact with raw Resolve handles) are exempt from `warn_return_any`. Everywhere else, types must be precise.

## Adding a new domain

1. Add a module `dvr/<domain>.py` with the wrapper class(es).
2. Expose a namespace class on the appropriate parent (e.g. `Resolve`, `Project`, `Timeline`).
3. Export public names in `dvr/__init__.py`.
4. Add a CLI sub-app under `dvr/cli/commands/<domain>.py` and register it in `dvr/cli/main.py`.
5. Write tests. Pure unit tests where possible; mark integration tests with `@pytest.mark.integration`.
6. Update `CHANGELOG.md` under `[Unreleased]`.

## Commit style

Follow [Conventional Commits](https://www.conventionalcommits.org/) where natural:

```
feat(timeline): add scene cut detection
fix(render): decode "no current timeline" error
docs: explain LAN-IP workaround on macOS
```

Not every commit needs a prefix. The goal is searchable history, not religious adherence.

## Releasing

Releases are tag-driven. Push a `v*.*.*` tag and the `release.yml` workflow:

1. Builds the wheel and sdist.
2. Publishes to PyPI via trusted publishing.
3. Creates a GitHub release with auto-generated notes.

Versioning follows [SemVer](https://semver.org/). Until 1.0, breaking changes can land in minor versions but must be called out in `CHANGELOG.md`.

## Trademark note

This project is not affiliated with Blackmagic Design. When contributing copy, code comments, or examples, do not imply official endorsement. Use lowercase `dvr` for the project name and reserve "DaVinci Resolve" for references to the application itself.
