# Procurement Text-to-SQL Assistant

Ask business questions about suppliers and purchasing in plain English. An agent writes safe,
read-only SQL against PostgreSQL, asks for clarification when the question is ambiguous, and
explains the result with evidence, cost and latency.

Final project of the EDIT Generative AI course (idea 04, "Text-to-SQL Assistant").
All data is fully synthetic.

**Status:** in development. The plan, step by step, is in [docs/PLAN.md](docs/PLAN.md); design
notes and progress are in [docs/LOG.md](docs/LOG.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for you)
- Docker with Docker Compose

## Development

```bash
uv sync                                  # create the environment
uv run pre-commit install                # install the git hooks (once per clone)
uv run pytest -q -m "not integration"    # unit tests, no database, no API key
uv run pre-commit run --all-files        # every check, as CI runs them
```

## Repository layout

```text
src/assistant/   application package
scripts/         data generator and one-off tools
config/          model prices (pricing.toml)
tests/           unit/ (no DB, no key), integration/ (needs the db), security/
docs/            plan, log, data model, golden-set guide
```
