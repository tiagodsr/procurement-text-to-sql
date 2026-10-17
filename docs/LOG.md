# Log

Design notes and progress, newest step last. Each module gets a short entry before it is written:
what it does, its inputs and outputs, and why this design.

## Step 0. Foundation (2026-10-17)

**Tooling.** `pyproject.toml` (uv, src layout, package `assistant`, Python 3.12), Ruff, pytest,
pre-commit and one CI workflow. Inputs: source files; outputs: lint, format and test results, the
same locally (hooks) and in CI. Hooks run through `uv run`, so Ruff and pytest are the versions in
`uv.lock`. Commit hooks stay fast (hygiene, secrets, Ruff); unit tests run on push and in CI, never
the integration tests, which need the database. The generator is excluded from formatting because
it is mostly data tables and its draw logic is frozen.
