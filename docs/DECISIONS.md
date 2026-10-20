# Design decisions

Each decision and the reason for it.

## Data

- **PostgreSQL 16 with a committed dump.** `scripts/build_db.py` generates the synthetic data (seed
  42); `make dump` runs it in a throwaway container and writes `data/init/01_dump.sql.gz` with the
  same `pg_dump` and `gzip` every time. The `db` container loads the dump on its first start, so no
  generator runs at startup and every clone has the same rows.
- **PostgreSQL rounds exactly.** Amounts are `NUMERIC`, so EUR conversions have no float artefacts.
- **`catalog_items.lead_time_days`** stores the lead time that was only inside the item description,
  so lead-time questions need no string parsing.

## Safety: three independent layers

1. **Guard** (`guard.py`). The SQL is parsed once with sqlglot (dialect postgres) and every rule looks
   at the tree, never at the text, so comments and quoting cannot change a decision. Exactly one
   statement, SELECT or UNION only, no writes or DDL anywhere (CTEs included), no `SELECT INTO`, no
   locking clauses, a depth limit, and allowlists (not denylists) of tables and functions. The outer
   `LIMIT` is forced. The SQL that runs is regenerated from the checked tree and must parse back to
   the same tree, so what runs is exactly what was checked. A parse failure is a violation, never an
   exception. `current_date` and `now()` are not allowed: "today" is `meta.as_of_date`.
2. **Executor** (`executor.py`). One `READ ONLY` transaction per query with `SET LOCAL
   statement_timeout` (3 s), `EXPLAIN` first (plans above 1,000,000 cost units never run; normal
   questions cost under 2,000, an exploding three-way cross join about 7.6e10), then always
   `ROLLBACK`. Statements are prepared, so PostgreSQL itself rejects stacked statements. Errors are
   typed from the SQLSTATE for the repair step. A database outage raises `DatabaseUnavailable`
   instead of an error, because repairing SQL cannot fix an outage.
3. **Role** (`data/init/02_roles.sql`). `app_ro` has `SELECT` on the tables and views and nothing
   else: no temporary tables, no `CREATE`, no role switching. The read-only and timeout defaults can
   be changed by a session, so the privileges are the real barrier. The settings also refuse any
   database URL whose user is not `app_ro`.

The tests send every guard attack straight to the role as well. What the role lets through (a
session changing its own timeout, reading `information_schema`, valid but deeply nested SQL) is
listed in `tests/integration/test_role_attacks.py`: the role protects the data, the guard protects
the session and the shape of the query.

Left to other layers because the guard cannot decide them from the tree: exploding joins and costly
functions over large inputs (EXPLAIN and the timeout), and privileges (the role).

## Zero rows

`diagnose.py` drops one top-level `WHERE`/`HAVING` condition at a time, sends each variant through
the guard and the executor again, and reports which filter removed every row. The summary is written
by code, not by an LLM, so `no_data` always says why. At most 8 filters are checked; filters inside
subqueries are not split.

## Workflow

- **Fixed steps, no agent.** `workflow.py` runs intake, SQL, guard, execute, repair and explain in a
  fixed order, and every exit (clarification, no data, out of scope, refused, error) is code.
- **Intake decides before any SQL.** It routes to `sql`, `clarify`, `out_of_scope` or `unsafe`.
  It asks only about sister brands and vague judgements; any other missing detail gets a default
  that the SQL step states as an assumption.
- **Sister brands are a list, not a tool.** Glossary rule 6 lists the six brands with two legal
  entities, so intake sees them in its instructions and no entity-resolution step is needed. An
  integration test checks the list against `supplier_aliases`.
- **One SQL prompt for generation and repair.** A repair is the same instructions with the failed SQL
  and the error added to the input, so the two cannot drift apart. At most two repairs.
- **A guard block that means "write" is refused at once.** `write_or_ddl`, `not_select`,
  `select_into` and `locking_clause` end as `refused` with nothing executed; other blocks (a clock
  function, an unknown table) go to repair like a database error.
- **Empty means no rows or one row of nulls.** `SUM` over no rows returns one null row, so a total
  for 2030 is `no_data` with its diagnostic, not an answer of "null". `no_data` has no explain call:
  the diagnostic summary is the answer.
- **Refusals.** A model refusal before any rows were read ends as `refused`; after that (in explain)
  it is an `error`. `refused` and `out_of_scope` never return rows; a failed query before a refusal
  is allowed in the trace.
- **Citations are checked.** An explanation that cites a row the result does not have is an
  `invalid_output` error, never shown.
- **Supplier matching never joins aliases into lines.** Some suppliers have two aliases, so a join
  counts their lines twice; the prompt and `tables.md` ask for `supplier_id IN (...)`.

## LLM calls

- **One door.** Every call goes through `llm.py`, which records model, tokens, latency and cost as a
  trace event, failures included. An unpriced model fails before the call.
- **Strict structured outputs.** Each LLM step returns a Pydantic model. The call uses
  `responses.create` with the strict schema from the SDK helper that `responses.parse` uses, and
  validates the text with our own model: with `responses.parse`, a failing validator raises inside
  the SDK and the usage of a paid call is lost. The helper is private, so `openai` is pinned below the
  next major version and a test builds the format for every LLM output. Limits live in validators,
  because strict mode accepts only a subset of JSON Schema.
- **Trusted and untrusted text.** `instructions` is built only from code and `knowledge/`. The
  question, the clarification, database errors and rows go only in `input`.
- **`store=False`.** Questions and answers are not kept on OpenAI's side.
- **Exact cost.** Prices are read as `Decimal` from `config/pricing.toml`; cost is uncached input,
  cached input and output tokens times their prices, per 1M tokens.

## Retrieval

About 25 documents (table sections and example queries), so the vectors are committed in a JSON file
and ranked with plain-Python cosine similarity, as in the course. No vector database.

## Tracing

One JSON line per step, appended to `var/traces/` (git-ignored). Latency percentiles use linear
interpolation between closest ranks (the NumPy default), written out because the standard library
refuses fewer than two values. A request's cost is the sum of its steps; its latency is the wall
clock of the whole request.

## PostgreSQL findings

- A non-owner's `GRANT` is only a warning, so the tests check that nothing was granted.
- `information_schema.table_constraints` hides constraints from a SELECT-only role; schema
  introspection as `app_ro` must use `pg_catalog`.
- In sqlglot 30, `AND` and `OR` are function nodes; the guard skips them when checking functions.
  sqlglot is pinned below the next major version because the guard depends on its tree shape.
