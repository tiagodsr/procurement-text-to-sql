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

## Database

```bash
docker compose up -d --wait db           # PostgreSQL 16 with the synthetic data (about 10 s)
docker compose exec db psql -U app_ro -d procurement   # explore as the read-only role
docker compose down -v                   # stop and delete the data volume (next start reloads it)
```

The first start loads the committed dump (`data/init/01_dump.sql.gz`) and creates the read-only role
`app_ro` (`data/init/02_roles.sql`, local password `app_ro_local`). The base Compose file publishes no
database port. See [docs/data_model.md](docs/data_model.md) for the tables and their pitfalls.

## Contracts, LLM calls and cost

Every LLM and embedding call goes through `assistant.llm`, which writes one trace event (model,
tokens, cost, latency, status) per call to `var/traces/YYYY-MM-DD.jsonl` (git-ignored). Prices come
from `config/pricing.toml`; a model without a price is never called. These demos need no API key and
spend nothing (they use an offline fake client):

```bash
uv run python -m assistant.config                 # settings (key masked) and priced models
uv run python -m assistant.schemas --examples     # one valid answer per status
uv run python -m assistant.schemas FinalAnswer    # JSON schema of a contract
uv run python -m assistant.llm --demo             # three fake calls: cost, latency, p50/p95
uv run python -m assistant.trace var/traces/*.jsonl   # p50/p95 latency and cost from a trace
uv run python -m assistant.pricing gpt-4.1-mini 1200 200 300   # cost of input/cached/output tokens
```

Settings are read from environment variables (see `.env.example`); for local runs that need the key,
use `uv run --env-file .env ...`.

## SQL safety

Generated SQL passes the guard (`assistant.guard`) before it runs. It parses the SQL with sqlglot and
decides on the tree: exactly one SELECT, no writes or DDL anywhere (CTEs included), no `SELECT INTO`,
no locking clauses, only the schema's tables and an allowlist of functions, a depth limit, and an
outer `LIMIT` forced to the maximum. A blocked query names the rule that fired:

```bash
uv run python -m assistant.guard "SELECT name FROM suppliers WHERE status = 'active'"
uv run python -m assistant.guard "SELECT pg_sleep(10)"      # blocked: function_not_allowed
```

The guard is one layer. The database role `app_ro` can only read, independently of it.

## Development

```bash
uv sync                                  # create the environment
uv run pre-commit install                # install the git hooks (once per clone)
uv run pytest -q -m "not integration"    # unit tests, no database, no API key
make db-dev                              # db with a loopback port 127.0.0.1:55432 (compose.dev.yml)
uv run pytest -q -m integration          # integration tests against that db, as app_ro
make dump                                # rebuild the data, the dump and data/fingerprint.json
uv run pre-commit run --all-files        # every check, as CI runs them
```

## Repository layout

```text
src/assistant/   application package
data/            init/ (dump and role script loaded by the db container), fingerprint.json
scripts/         data generator, freeze and one-off parity check
config/          model prices (pricing.toml)
var/             runtime output, git-ignored (traces/*.jsonl)
tests/           unit/ (no DB, no key), integration/ (needs the db), security/
docs/            plan, log, data model, golden-set guide
```
