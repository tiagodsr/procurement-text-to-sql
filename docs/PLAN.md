# Plan: Procurement Text-to-SQL Assistant

Deadline 2026-10-21 23:59. No new features after 18:00 on 2026-10-21.
Status: Steps 0, 1 and 2 done. Tick the boxes as steps complete. Notes go in `docs/LOG.md`.

## 1. Product

- **User:** procurement staff who do not write SQL.
- **Problem:** purchasing data (suppliers, contracts, catalog, purchase carts, spend in several
  currencies, delivery times) is locked behind SQL and dashboards.
- **What it does:** answers business questions in plain language. An agent writes safe read-only
  SQL, executes it, and explains the result with visible evidence, confidence, cost and latency.
- **Missing information:** it never guesses. It asks for clarification, says there is no data and
  why, declines out-of-scope questions, and refuses unsafe requests.

## 2. Course requirements and where they are met

| Slide requirement | Where |
|---|---|
| At least two LLM steps (classify, then generate) | Intake (LLM 1), SQL generation (LLM 2), explanation (LLM 3) |
| Structured output validated against a schema | Pydantic: `IntakeResult`, `GeneratedSQL`, `FinalAnswer` |
| Defined behaviour when information is missing | Statuses in section 5, each with test cases |
| Set of test cases | Golden set (32 cases) and security attacks |
| Idea 04: fix failing queries from the error message | Repair loop in both variants |
| Idea 04: ask for clarification instead of guessing | Ambiguity cases (two-entity brands, vague criteria, missing period) |
| Idea 04: evaluate against expected results | Result comparison, not SQL text comparison |
| Idea 04: semantic search over table descriptions and example queries | ChromaDB over `knowledge/`, recall@k |
| Idea 04: execute in read-only mode | Guard, READ ONLY transaction, `app_ro` role |
| RAG with visible evidence and cited sources | Retrieved docs (id, score), SQL and rows shown with every answer |
| FastAPI and Streamlit (portfolio option) | API and UI over HTTP |

Beyond the minimum (and aligned with grading): layered SQL safety with attack tests, three variants
compared on one golden set, dev and holdout split, cost and latency tracking, Docker Compose.

## 3. Decisions

| Topic | Decision |
|---|---|
| Name | `procurement-text-to-sql`. Everything in English |
| Database | PostgreSQL 16. Generator adapted only in schema, connection and inserts (same random draws). Time box 4h for the port; fallback is SQLite from the old repo tag `sqlite-final` |
| Data on the professor's side | Committed dump in `data/init/`, loaded by the `db` container. No seed service. The DB port is not published; `compose.dev.yml` adds `127.0.0.1:55432` for host-side tests only |
| Search index | Committed vectors (`index/embeddings.json`, `index_meta.json`) loaded into embedded ChromaDB at startup. No embedding call needed to start |
| Embeddings | `text-embedding-3-small`. The query embedding is computed by `llm.py` so cost and latency are traced |
| Models | OpenAI. Base `gpt-4.1-mini` (as in the course); one stronger OpenAI model for comparison. Prices copied from the OpenAI pricing page into `config/pricing.toml` with a date |
| Variants | Baseline (one prompt, full schema), A (fixed workflow), B (agent with tools) |
| Agent | OpenAI Agents SDK, validated in a 2h spike (tools, `max_turns`, usage from the run result, structured `output_type`). Fallback: manual tool-calling loop as in course session 7 |
| Tools | `search_schema`, `resolve_entity` (plain SQL on names and aliases), `run_sql` |
| Roles / external search | Not used. The ranking criterion comes from the question |
| Language | All repository content in English, including test questions |

## 4. Architecture and request lifecycle

```text
UI (Streamlit) -HTTP-> API (FastAPI) POST /v1/query {question, variant}
  intake (LLM 1): out of scope / unsafe / ambiguous -> direct answer, no SQL
  variant A: code orchestrates      |  variant B: agent orchestrates (max_turns, timeout, budget)
     tools: search_schema (ChromaDB) - resolve_entity - run_sql
     run_sql: guard -> EXPLAIN -> READ ONLY transaction as app_ro; an error goes back to the model
  verify: sanity checks, simple confidence
  explain (LLM 3) -> FinalAnswer
Every step writes one trace event (JSONL): step, model, tokens, cost, latency, sql, status.
```

| # | Step | Module | Output |
|---|---|---|---|
| 0 | Request | `api.py` | `QueryRequest` (400 if invalid) |
| 1 | Intake | `intake.py` | `IntakeResult` |
| 2 | Retrieve schema context | `knowledge.py` | `SchemaContext` (docs with id and score) |
| 3 | Resolve entities | `tools.py` | candidates; two candidates means clarification |
| 4 | Generate SQL | `pipeline.py` / `agent.py` | `GeneratedSQL` |
| 5 | Guard | `guard.py` | ok or `GuardViolation(rule)` |
| 6 | Execute | `executor.py` | `SqlResult` (rows, typed error, db ms); error goes back to 4 (max 2); zero rows get a diagnostic |
| 7 | Verify | `verify.py` | confidence with breakdown |
| 8 | Explain | `pipeline.py` | plain-language answer citing rows |
| 9 | Assemble | `schemas.py` | `FinalAnswer` with evidence and usage |

## 5. Statuses and final-answer rules

`answered`, `needs_clarification`, `no_data`, `out_of_scope`, `refused`, `error`.

- `answered` needs the executed SQL and a non-empty row preview or aggregate.
- `no_data` needs a diagnostic of which filter removed the rows.
- `needs_clarification` needs a question and at least two options when the cause is entity ambiguity.
- `out_of_scope` and `refused` mean no SQL was executed.
- `error` is used when a limit is hit (steps, retries, time, budget). It is never shown as an answer.

## 6. Repository layout

```text
procurement-text-to-sql/
  README.md  CLAUDE.md  .env.example  docker-compose.yml  Dockerfile  Makefile  pyproject.toml  uv.lock
  knowledge/   schema/*.md (one file per table and view)   glossary.md   examples.yaml
  index/       embeddings.json   index_meta.json
  data/init/   01_dump.sql.gz   02_roles.sql
  src/assistant/   config pricing llm fake_llm trace schemas guard executor db knowledge tools intake
                   pipeline agent verify api
  ui/streamlit_app.py
  scripts/     build_db.py   preflight.py
  evals/       golden_set.json   compare.py   run.py
  config/pricing.toml
  tests/       unit/  integration/  security/
  docs/        PLAN.md  LOG.md  architecture.md  data_model.md  evaluation.md  results/  demo/
```

## 7. Knowledge and index

`knowledge/schema/<table>.md`: front matter (`type`, `table`, `related`), then sections `Purpose`,
`Columns`, `Pitfalls`. Each `##` section becomes one document. `glossary.md`: one section per business
rule. `examples.yaml`: one document per example (`id`, `question`, `tags`, `sql`).
Metadata: `doc_type`, `table`, `source`. A test checks that every table and column is described and
that no example question appears in the golden set.
`make index` embeds all documents in batches and writes `index/embeddings.json` and `index_meta.json`
(model, dimension, fingerprint of `knowledge/`, date, cost). At startup the API loads these vectors
into ChromaDB; a fingerprint mismatch stops it with the rebuild command.
Evaluate retrieval with recall@k (k = 1, 3, 5): tables parsed from `expected_sql` against retrieved
tables. Choose k from the numbers.

## 8. Evaluation

- **Golden set:** 32 cases (22 development, 10 holdout), stratified. Fields: `id`, `split`, `category`,
  `critical`, `question`, `expected_status`, `expected_sql`, `expected_snapshot`, `comparison`,
  `traps`, `naive_sql`, `verified` (method, data version, date).
- **Mix:** simple 5, aggregation with dates 5, joins 4, traps 7, criteria in the question 3, ambiguous 3,
  no data 3, unsafe or out of scope 2. Questions are written as a person would ask them, in English,
  including a few colloquial or very short ones.
- **Rules:** SQL first, verified a second way; trap cases keep a `naive_sql` with a different result;
  no ties in top-N, no `LIMIT` without `ORDER BY`, no dependence on the real date; expected values are
  computed on PostgreSQL; the comparison function is one unit-tested function (multiset by default,
  ordered for rankings, numeric tolerance 0.01, ignore column names and extra columns).
- **Metrics:** execution accuracy, status accuracy, clarification correctness, unsafe queries executed
  (target 0), recall@k, citation validity, cost per question (mean, p95), latency (p50, p95), LLM calls
  and tool calls per question. Three repetitions per case; report mean and share passing all three.
- **Variants and models:** baseline, A, B, each with the base model and one stronger model.
- **Discipline:** develop on the development split; run holdout once at the end; write one failure
  diagnosis, one targeted improvement and the before and after.
- **Security tests:** about 15 attacks (DROP, stacked statements, comment tricks, `pg_sleep`,
  `COPY ... TO PROGRAM`, system catalogs, `SET ROLE`, `FOR UPDATE`, CTE wrapping an INSERT, deep
  nesting, exploding cross join), each naming the rule that must fire, plus the same attacks sent
  straight to the database role to prove it refuses by itself.

## 9. Steps

### Step 0. Foundation (1h)
- [x] Repo, `.gitignore` (`.env`, `.local/`, `CLAUDE.local.md`, caches), `pyproject.toml`, uv, Ruff,
  pre-commit, CI skeleton, `.env.example`, `.gitattributes` (`eol=lf`), folders, README skeleton.
- [x] `config/pricing.toml` skeleton, `docs/LOG.md`.
DoD: `uv sync` works, hooks run, first commit on `dev`.

### Step 1. PostgreSQL, data, Compose (limit 4h)
- [x] Adapt `scripts/build_db.py` to PostgreSQL: types `DATE`, `NUMERIC(14,2)`, `NUMERIC(10,4)`,
  `NUMERIC(4,1)`, `BOOLEAN` (convert with `bool()`), `BIGINT` for cart ids; `%s` placeholders; no
  `PRAGMA`; target-database allowlist; random-draw code unchanged.
- [x] One-off parity check against `.local/sqlite_reference.db`: row counts and per-table checksums.
- [x] Then add `lead_time_days` to `catalog_items` (the value is already drawn; capture it without
  changing draw order). Freeze the data and record the fingerprint.
- [x] `make dump` writes `data/init/01_dump.sql.gz` and `02_roles.sql` (role `app_ro`: SELECT only,
  `default_transaction_read_only = on`, `statement_timeout`; local password documented).
- [x] `docker-compose.yml`: `db` (pinned image, healthcheck, DB port not published to the host).
  `compose.dev.yml` publishes it on `127.0.0.1:55432` for integration tests only.
- [x] Tests: schema and integrity, determinism across `PYTHONHASHSEED`, dump fingerprint, `app_ro` can
  read and cannot write.
DoD: `docker compose up -d db` loads the data; parity report identical; tests green.

### Step 2. Contracts and measurement (2h)
- [x] `config.py`, `schemas.py` (all Pydantic models, statuses, cross-field validators), `trace.py`,
  `llm.py` (`parse()` and `embed()` with tokens, latency, cost; cost in `pricing.py`). Unit tests with
  a fake client (`fake_llm.py`).
- [ ] Real prices for `gpt-4.1-mini` and `text-embedding-3-small` in `config/pricing.toml` (needed
  before the first real call in Step 5; the stronger model by Step 6).
DoD: a fake call yields correct cost, latency and p50/p95.

### Step 3. Safe SQL (3h)
- [ ] `guard.py` (sqlglot, dialect postgres): one statement, SELECT root, table and function
  allowlists, forced LIMIT, depth limit, structured `GuardViolation`. No regex for security.
- [ ] `executor.py`: READ ONLY transaction, `SET LOCAL statement_timeout`, EXPLAIN cost check, row cap,
  typed errors, rollback. Diagnostic for zero rows (drop one filter at a time).
- [ ] Security suite from section 8.
DoD: every attack blocked by the intended rule.

### Step 4. Golden set and comparison (4h)
- [ ] `docs/data_model.md` updated; profile of the data; glossary decisions written down.
- [ ] 32 cases, verified twice; `compare.py` with tests; fingerprint check; holdout marked and frozen.
DoD: tests green; holdout never inspected until Step 10.

### Step 5. Knowledge index (3h)
- [ ] `knowledge/` files, `make index`, `knowledge.py`, `search_schema`, recall@k, choose k.
DoD: recall@k reported; startup loads the committed vectors without an API call.

### Step 6. Baseline, intake and variant A, runner (4h)  -> minimum deliverable
- [ ] `intake.py`, `tools.py`, `pipeline.py`, baseline prompt, `evals/run.py` (JSON and Markdown report,
  cost estimate and confirmation, `--max-cases`, `--repeat`).
- [ ] First results table and one written failure diagnosis.
DoD: baseline and A run on the development split with accuracy, cost and latency. This is the gate for
the course minimum.

### Step 7. Agent B (3h)
- [ ] 2h spike with the OpenAI Agents SDK (fallback: manual loop). `agent.py` with the same tools,
  `max_turns`, timeout, budget; usage converted to trace events. A versus B table, one improvement.
DoD: A versus B comparison with a written analysis.

### Step 8. Verification and evidence (2h)
- [ ] `verify.py`: sanity checks (currency mixing, dates outside the data window, NULL-heavy joins,
  truncation), simple confidence with breakdown; citations to rows validated in code.
DoD: confidence and retrieved documents visible in the answer; unit tests per check.

### Step 9. API, UI, Docker (4h)
- [ ] FastAPI: `POST /v1/query`, `GET /v1/schema`, `GET /v1/history`, `GET /health` (independent of the
  LLM; reports `llm_configured`). 400 for invalid input, 502 for upstream failure.
- [ ] Streamlit: variant selector, six clickable example questions (answered, clarification, no data,
  refused, out of scope, repair), answer, SQL, rows, evidence, confidence, step panel with tokens,
  cost and latency. Clear banner if the API key is missing.
- [ ] Compose: `db`, `api`, `ui`; key only to `api`; healthchecks; `scripts/preflight.py`; smoke test.
DoD: from a clean clone, three commands bring the system up and the smoke test passes.

### Step 10. Close-out
- [ ] Holdout once for each variant. README (results first, quick start for bash and PowerShell, what
  to try, common problems, architecture, variants, evaluation, decisions, limitations). `docs/results/`
  and `docs/demo/` (screenshots, short GIF). Clean-clone test. Tag `v1.0`.
DoD: delivered before 18:00 cut-off with margin.

## 10. Schedule

| Day | Steps |
|---|---|
| Sun 2026-10-18 | 0, 1, 2, 3 |
| Mon 2026-10-19 | 4, 5, 6 (minimum deliverable) |
| Tue 2026-10-20 | 7, 8, 9 |
| Wed 2026-10-21 | 10; features stop at 18:00 |

If variant A is not stable by Monday night, Tuesday finishes A and B becomes v1.1.
Cut order: second model, verification beyond sanity checks, Streamlit polish (the API docs page is the
demo), variant B.
Never cut: Docker starting, golden set with comparison, guard and read-only role, clarification and
no-data handling, cost and latency table, README with results, tests.

## 11. Out of scope
Roles and personas, web search, external supplier lists, user feedback loop, authentication, cloud
deployment, multi-agent orchestration, long-term memory, multi-query validation and back-translation
(stretch only).
