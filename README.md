# Procurement Text-to-SQL Assistant

Ask business questions about suppliers and purchasing in plain English. A fixed workflow of LLM
steps writes safe, read-only SQL against PostgreSQL, asks for clarification when the question is
ambiguous, and explains the result with evidence, cost and latency.

Final project of the EDIT Generative AI course (idea 04, "Text-to-SQL Assistant"). All data is
fully synthetic. The plan is in [docs/PLAN.md](docs/PLAN.md) and the design decisions are in
[docs/DECISIONS.md](docs/DECISIONS.md).

## Run it

```bash
cp .env.example .env                  # then set OPENAI_API_KEY
docker compose up --build --wait      # db, api and ui
```

Open the page at <http://localhost:8501> and the API docs at <http://localhost:8000/docs>.
`docker compose down` stops everything (add `-v` to delete the database volume). The ports are
published on `127.0.0.1` only, because the API spends the key's money and has no login. Without a
key everything still starts: the page shows a warning and the API answers `503` to a question.
Inside compose the api reaches the database as `db:5432`; only `OPENAI_API_KEY`, `LLM_MODEL`,
`EMBEDDING_MODEL` and `LLM_TIMEOUT_S` are taken from `.env`.

## Ask in the terminal

```bash
docker compose up -d --wait db
cp .env.example .env                     # then set OPENAI_API_KEY
uv run --env-file .env ask "Which five suppliers had the highest spend in 2025?"
uv run --env-file .env ask "How much did we spend with Velria last year?"   # asks which Velria
uv run --env-file .env ask "How much did we spend with Velria last year?" --clarification "Both"
```

The answer comes with its status, the assumptions the SQL made, the evidence (the documents the
search found, with their scores), the SQL that ran, the rows (cited rows marked) and one trace line
per step with model, tokens, cost and latency. `--json` prints the full answer. A question costs
about 0.002 USD with `gpt-4.1-mini` and takes 1 to 5 seconds.

## API

Compose runs this; to run it on the host (the db must be up):

```bash
uv run --env-file .env uvicorn assistant.api:app      # http://127.0.0.1:8000, interactive docs at /docs
curl -s localhost:8000/health
curl -s -X POST localhost:8000/v1/query -H 'content-type: application/json' \
  -d '{"question": "Which five suppliers had the highest spend in 2025?"}'
```

`POST /v1/query` takes `question` and, to reply to a clarification, `clarification` (the option
chosen). The response is the same answer as the terminal one, as JSON: `status`, `answer`, `sql`,
`result` (columns and rows), `cited_rows`, `evidence` (documents with scores), `assumptions`,
`caveats`, and `usage` with the `cost_usd`, the `latency_ms` and every step of the trace. Every
workflow outcome is a `200` with its status (a failed service is status `error`, with the cost
spent so far). A missing `OPENAI_API_KEY` is `503` and an invalid request is `422`.

`GET /health` always answers while the API is up and says what a question needs:
`{"status": "ok", "llm_configured": true, "database": true}`.

## The page

Compose runs this too; on the host, with the API up:

```bash
uv run streamlit run src/assistant/ui.py              # http://localhost:8501
```

One page over HTTP (`API_URL`, default `http://127.0.0.1:8000`). It shows what the API returns: the
status, the answer, cost and latency, the SQL, the rows (cited ones marked), the evidence and the
trace. A clarification is answered with one button per option. Rows are not shown when there are
none (`no_data` shows which filter removed them instead). A warning appears, and Ask is disabled,
when the API is not reachable, has no `OPENAI_API_KEY` or cannot reach the database.

## How a question is answered

```text
question -> intake LLM: sql | clarify | out_of_scope | unsafe
         -> semantic search: the 5 closest table docs and 3 closest example queries
         -> SQL LLM (glossary and the docs found, in the instructions)
         -> guard -> read-only execute
         -> guard block or SQL error: the same SQL LLM repairs it, with the error (max 2)
         -> empty result: which filter removed the rows (code, no LLM) -> no_data
         -> explain LLM -> answer citing rows
```

| Status | When |
|---|---|
| `answered` | the query returned rows and the explanation cites them |
| `needs_clarification` | the question names a sister brand without the entity, or a vague judgement ("best supplier") |
| `no_data` | the query returned nothing; the answer names the filter that removed every row |
| `out_of_scope` | not about the procurement data |
| `refused` | asks to change data, read system information or override the rules; or the SQL tried to write |
| `error` | the SQL still failed after two repairs, an LLM output was unusable, or a service was down |

## Semantic search

The documents are the 12 sections of [knowledge/tables.md](knowledge/tables.md) and the 12 example
queries of [knowledge/examples.md](knowledge/examples.md). Their vectors
(`text-embedding-3-small`) are committed in `knowledge/index.json`, so a question costs one
embedding call and a plain-Python cosine ranking, with no vector database. Tables and examples are
ranked separately, so examples never push the tables out. The glossary is not searched: its rules
apply to every query, so it is always in the instructions.

```bash
make index    # re-embed after editing tables.md or examples.md (< 0.001 USD)
make recall   # recall of the table docs on the development questions in evals/cases.json
```

On the 8 development questions that reach SQL, recall@3 is 0.85 and recall@5 (what the SQL step
gets) is 0.96.

The prompts are in [prompts/](prompts/). The question, the clarification, database errors and rows
only ever go in the `input` of a call; the `instructions` are built from the prompts and
`knowledge/` alone.

## Evaluation

```bash
make eval                                                           # dev split, both variants
uv run --env-file .env python -m assistant.evaluate --max-cases 2    # a cheap smoke run
uv run --env-file .env python -m assistant.evaluate --split holdout  # once, at the end
```

[evals/cases.json](evals/cases.json) holds 16 questions (12 development, 4 holdout) across simple,
join, trap, ambiguous, no data and unsafe. Each runs through the workflow and through a baseline
(one prompt with the whole schema and no glossary, the same guard and read-only execution). The
report compares status accuracy, execution accuracy (the rows of a reference query), unsafe
questions that reached the database, writes blocked by the guard, cost and latency p50/p95, and is
written to `evals/report-<split>.md`. The runner prints a cost estimate and asks before spending.

## Requirements

- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for you)
- Docker with Docker Compose

## Database

```bash
docker compose up -d --wait db                          # PostgreSQL 16 with the synthetic data
docker compose exec db psql -U app_ro -d procurement    # explore as the read-only role
docker compose down -v                                  # stop and delete the data volume
```

The first start loads the committed dump (`data/init/01_dump.sql.gz`) and creates the read-only
role `app_ro` (`data/init/02_roles.sql`, local password `app_ro_local`). The database is published
on `127.0.0.1:55432` only. The tables are described in [knowledge/tables.md](knowledge/tables.md)
and the business rules in [knowledge/glossary.md](knowledge/glossary.md).

## SQL safety

Generated SQL goes through three independent layers before any row comes back:

1. **Guard** (`assistant.guard`): parses the SQL with sqlglot and decides on the tree. It allows
   exactly one SELECT with no writes or DDL anywhere (CTEs included), no `SELECT INTO` and no
   locking clauses. Only the schema's tables and an allowlist of functions are allowed, nesting
   depth is limited, and an outer `LIMIT` is forced. A blocked query names the rule that fired.
2. **Executor** (`assistant.executor`): runs the guarded SQL as `app_ro` in a `READ ONLY`
   transaction with a 3 s timeout. It runs `EXPLAIN` first and refuses plans that would cost more
   than 1,000,000 units, and it always rolls back. Errors come back typed, for the repair step.
3. **Database role** (`app_ro`): can only read. Every guard attack is also sent straight to the
   role in `tests/integration/test_role_attacks.py`, and the role refuses every one that needs a
   privilege.

When a query returns no rows, `assistant.diagnose` drops one filter at a time to find the one that
removed every row. Try the layers (the last three need the database):

```bash
uv run python -m assistant.guard "SELECT pg_sleep(10)"      # blocked: function_not_allowed
uv run python -m assistant.executor "SELECT name, status FROM suppliers ORDER BY name LIMIT 5"
uv run python -m assistant.executor "SELECT COUNT(*) FROM shopping_cart_items AS a CROSS JOIN shopping_cart_items AS b"   # cost_limit
uv run python -m assistant.diagnose "SELECT cart_id FROM shopping_carts WHERE status = 'completed' AND created_at >= '2030-01-01'"
```

## Development

```bash
uv sync                                  # create the environment
uv run pre-commit install                # install the git hooks (once per clone)
uv run pytest -q -m "not integration"    # unit, API, page and security tests: no database, no API key
uv run pytest -q -m integration          # executor, diagnostic and role tests (needs the db)
uv run pre-commit run --all-files        # whitespace, secrets, Ruff
make dump                                # regenerate the data dump in a throwaway container
```

Settings come from environment variables (see `.env.example`); use `uv run --env-file .env ...`
when a command needs the API key.

## Map of the repo

```text
src/assistant/
  cli.py           `ask`: one question in the terminal, with SQL, rows and trace
  api.py           FastAPI: `GET /health` and `POST /v1/query`
  ui.py            the Streamlit page, which asks the API over HTTP
  workflow.py      the fixed workflow and its exits for every status, and the baseline
  retrieve.py      semantic search: documents, committed vectors, cosine, recall
  evaluate.py      the golden set through the workflow and the baseline, with a report
  prompts.py       builds each step's instructions from prompts/ and knowledge/
  config.py        settings from the environment, model prices and the cost of a call
  schemas.py       Pydantic contracts and the rules of each answer status
  llm.py           the only door to OpenAI: structured calls and embeddings, traced
  trace.py         per-request trace events, usage totals, p50/p95 latency and cost
  guard.py         SQL guard on the parsed tree: one SELECT, allowlists, forced LIMIT
  executor.py      read-only transaction as app_ro, timeout, EXPLAIN cost check
  diagnose.py      zero rows: which filter removed them
prompts/           intake.md, sql.md (generate and repair), explain.md, baseline.md
knowledge/
  tables.md        one section per table and view
  glossary.md      business rules that every query must follow, with the sister brands
  examples.md      example questions with a correct query, for the SQL step
  index.json       the committed vectors of tables.md and examples.md (`make index`)
evals/cases.json   the golden set: 12 development and 4 holdout questions, with the docs each
                   needs and, for answers, the reference SQL and its rows
config/pricing.toml  model prices (USD per 1M tokens) with their as_of date
data/init/         the synthetic data dump and the read-only role, loaded on the db's first start
scripts/build_db.py  the seeded generator that produced the dump
tests/
  unit/            no database, no API key
  security/        guard attacks, each naming the rule that must block it
  integration/     executor, diagnostic, role attacks and the glossary against the db
  fake_llm.py      offline stand-in for the OpenAI client
docs/
  PLAN.md          goal, flow and phases
  DECISIONS.md     design decisions and why
Dockerfile       one image for the api and the ui (uv, locked dependencies)
docker-compose.yml   db (127.0.0.1:55432), api (:8000) and ui (:8501)
Makefile         `make dump`, `make index`, `make recall`, `make eval`
.github/workflows/ci.yml  Ruff and unit tests on every push
var/             runtime output, git-ignored (traces/*.jsonl)
```
