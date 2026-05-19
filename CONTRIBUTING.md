# Contributing

## Development environment

```bash
git clone --recurse-submodules git@github.com:dagstack/config-python.git
cd config-python
uv sync --group dev
uv run pre-commit install
```

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

## Workflow

- **Base branch**: `main`.
- **Feature branches**: `feature/<phase|topic>-<short-desc>` (e.g., `feature/phase-b-interpolation`).
- **Pull requests** target `main`; one PR = one logical change. For Phase A-D — separate PRs per phase.
- **Commit style**: conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`) or phase-tagged (`phase-b:`).

## Identity

- `user.name = "Evgenii Demchenko"`
- `user.email = "demchenkoev@gmail.com"` (dagstack/* — personal identity, not corporate).

Set this locally (not globally):

```bash
git config user.name "Evgenii Demchenko"
git config user.email "demchenkoev@gmail.com"
```

## Pre-PR checks

```bash
make lint
make typecheck
make test
```

Pre-commit hooks (`uv run pre-commit install`) run ruff + mypy automatically on every commit; CI re-runs everything on push/PR.

## Spec submodule updates

`spec/` is a submodule pointing at `dagstack/config-spec`. To update to the latest main:

```bash
cd spec
git fetch origin
git checkout main
git pull
cd ..
git add spec
git commit -m "chore(spec): bump config-spec submodule"
```

A submodule bump is its own commit (don't mix with implementation changes).

## Testing guidelines

- **Unit tests** — one test function per behavior branch. Mock file I/O via the pytest `tmp_path` fixture.
- **Conformance tests** (`pytest -m conformance`) — run against `spec/conformance/` golden fixtures. A divergence is either a bug in the binding or a spec update (discuss in the PR).
- **Coverage target**: 90%+ for the Phase 1 release. CI validates via `--cov`.

## Versioning

`src/dagstack/config/_version.py::__version__` is the single source of truth. A release = git tag `v<version>` → the publish workflow ships to Nexus gx-pypi.

Pre-1.0: `0.N.M` semver + `.devN` / `.rcN` suffixes for pre-releases.
