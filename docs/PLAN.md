# Plan: Procurement Text-to-SQL Assistant

Deadline 2026-10-21 23:59. No new features after 18:00 on 2026-10-21.
Status: Steps 0 to 3 done. Steps 3 to 10 re-planned on 2026-10-19 (one day behind the first
schedule). Tick the boxes as steps complete. Notes go in `docs/LOG.md`.

## 1. Product

- **User:** procurement staff who do not write SQL.
- **Problem:** purchasing data (suppliers, contracts, catalog, purchase carts, spend in several
  currencies, delivery times) is locked behind SQL and dashboards.
- **What it does:** answers business questions in plain language. A fixed workflow of LLM steps
  writes safe read-only SQL, executes it, and explains the result with visible evidence, confidence,
  cost and latency.
- **Missing information:** it never guesses. It asks for clarification, says there is no data and
  why, declines out-of-scope questions, and refuses unsafe requests.

## 2. Course requirements and where they are met

| Slide requirement | Where |
|---|---|
| At least two LLM steps (classify, then generate) | Intake (LLM 1), SQL generation (LLM 2), explanation (LLM 3) |
| Structured output validated against a schema | Pydantic: `IntakeResult`, `GeneratedSQL`, `ExplanationDraft`, `FinalAnswer` |
| Defined behaviour when information is missing | Statuses in section 5, each with test cases |
| Set of test cases | Golden set (24 cases), security attacks, offline tests of every status |
| Idea 04: fix failing queries from the error message | Repair loop (max 2) in variant A |
| Idea 04: ask for clarification instead of guessing | Ambiguity cases (two-entity brands, vague criteria, missing period) |
| Idea 04: evaluate against expected results | Result comparison, not SQL text comparison |
| Idea 04: semantic search over table descriptions and example queries | Mandatory step in variant A: cosine top-k over committed vectors, recall@k |
| Idea 04: execute in read-only mode | Guard, READ ONLY transaction, `app_ro` role |
| RAG with visible evidence and cited sources | Retrieved docs (id, score), SQL and cited rows shown with every answer |
| FastAPI and Streamlit (portfolio option) | API, and one Streamlit page over HTTP, started by Docker Compose |

Beyond the minimum (and aligned with grading): layered SQL safety with attack tests, baseline versus
variant A on one golden set, dev and holdout split, cost and latency tracking, Docker Compose.

## 3. Decisions

| Topic | Decision |
|---|---|
| Name | `procurement-text-to-sql`. Everything in English |
| Database | PostgreSQL 16, data frozen as v1 (committed dump in `data/init/`, loaded by the `db` container). The DB port is not published; `compose.dev.yml` adds `127.0.0.1:55432` for host-side tests only |
| Semantic search | Mandatory step of variant A. The question is embedded (`llm.embed`, traced) and ranked by plain-Python cosine similarity against committed vectors (`index/embeddings.json`) of the table docs and example queries, as in the course's `rag_course_support.py`. About 35 documents, so no vector database. No embedding call is needed to start |
| Embeddings | `text-embedding-3-small` |
| Models | OpenAI `gpt-4.1-mini` (as in the course). A stronger model is optional (one dev run). Prices in `config/pricing.toml` with a date |
| Variants | Baseline (one prompt, full schema) and A (fixed workflow). B (agent with tools, manual loop as in course session 7, no Agents SDK) is optional and gated (Step 9) |
| Tools | `search_schema`, `resolve_entity` (plain SQL on names and aliases), `run_sql` |
| Demo surface | Docker Compose starts `db`, `api` (FastAPI) and `ui` (one simple Streamlit page over HTTP). The CLI `ask` and the API docs page are extra surfaces |
| Golden set | 24 cases (16 development, 8 holdout) |
| API budget | Up to 5 USD for development and evaluation without asking again; the runner prints an estimate first |
| Roles / external search | Not used. The ranking criterion comes from the question |
| Language | All repository content in English, including test questions |

## 4. Architecture and request lifecycle

```text
UI (Streamlit) -HTTP-> API (FastAPI) POST /v1/query {question, variant, clarification}
  intake (LLM 1): sql | clarify | out_of_scope | unsafe -> decision layer in code
  resolve_entity: a brand with two legal entities -> clarification (deterministic)
  semantic search: embed question -> top-k table docs + top-3 example queries (evidence)
  generate SQL (LLM 2); glossary always in the trusted instructions
  guard -> EXPLAIN -> READ ONLY transaction as app_ro; an error goes to repair (LLM, max 2)
  zero rows -> diagnostic (drop one filter at a time) -> no_data
  verify: sanity checks, simple confidence
  explain (LLM 3) -> FinalAnswer with cited rows
Baseline: one prompt with the full schema -> SQL -> guard -> execute.
Every step writes one trace event (JSONL): step, model, tokens, cost, latency, sql, status.
```

| # | Step | Module | Output |
|---|---|---|---|
| 0 | Request | `api.py`, `cli.py` | `QueryRequest` (400/422 if invalid) |
| 1 | Intake | `intake.py` | `IntakeResult`, then a route |
| 2 | Resolve entities | `tools.py` | candidates; two candidates means clarification |
| 3 | Retrieve schema context | `knowledge.py` | `SchemaContext` (docs with id and score) |
| 4 | Generate SQL | `pipeline.py` | `GeneratedSQL` |
| 5 | Guard | `guard.py` | ok or `GuardViolation(rule)` |
| 6 | Execute | `executor.py` | `SqlResult` or `SqlError`; error goes to repair (max 2) |
| 7 | Zero rows | `diagnose.py` | `NoDataDiagnostic` |
| 8 | Verify | `verify.py` | confidence with breakdown |
| 9 | Explain | `pipeline.py` | plain-language answer citing rows |
| 10 | Assemble | `schemas.py` | `FinalAnswer` with evidence and usage |

Untrusted text (the question, the clarification, database errors, rows) goes only in the `input` of
an LLM call, wrapped in tags. `instructions` is built from code and `knowledge/` only.

## 5. Statuses and final-answer rules

`answered`, `needs_clarification`, `no_data`, `out_of_scope`, `refused`, `error`.

- `answered` needs the executed SQL and a non-empty row preview or aggregate.
- `no_data` needs a diagnostic of which filter removed the rows.
- `needs_clarification` needs a question and at least two options when the cause is entity ambiguity.
- `out_of_scope` and `refused` mean no SQL was executed.
- `error` is used when a limit is hit (steps, retries, time, budget) or an LLM output is invalid. It
  is never shown as an answer.

## 6. Repository layout

```text
procurement-text-to-sql/
  README.md  CLAUDE.md  .env.example  docker-compose.yml  compose.dev.yml  Dockerfile  Makefile
  knowledge/   tables/*.md (one file per table and view)   glossary.md   examples.yaml
  index/       embeddings.json   index_meta.json
  prompts/     intake.md  generate_sql.md  repair_sql.md  explain.md  baseline.md
  data/init/   01_dump.sql.gz   02_roles.sql
  src/assistant/   config pricing llm fake_llm trace schemas fingerprint guard executor diagnose
                   knowledge tools intake pipeline baseline verify cli api (agent: optional)
  ui/streamlit_app.py
  scripts/     build_db.py   freeze_data.py   parity_check.py   smoke.py
  evals/       golden_set.json   compare.py   verify_golden.py   run.py   report.py
  config/pricing.toml
  tests/       unit/  integration/  security/
  docs/        PLAN.md  LOG.md  architecture.md  data_model.md  golden_set_guide.md  evaluation.md  results/
```

## 7. Knowledge and index

`knowledge/tables/<table>.md`: one document per table and view, with purpose, columns and pitfalls.
`knowledge/glossary.md`: the business rules; always included in the SQL-generation instructions,
because mandatory rules must not depend on retrieval. `knowledge/examples.yaml`: one document per
example (`id`, `question`, `tags`, `sql`); every example SQL passes the guard, and no example question
appears in the golden set (tests).
`make index` embeds the documents in batches and writes `index/embeddings.json` and
`index_meta.json` (model, dimension, fingerprint of `knowledge/`, cost). At startup the vectors are
loaded; a fingerprint mismatch stops the app with the rebuild command.
Retrieval is evaluated with recall@k (k = 1, 3, 5) on the development questions: tables parsed from
`expected_sql` against retrieved tables. k is chosen from the numbers.

## 8. Evaluation

- **Golden set:** 24 cases (16 development, 8 holdout), stratified. Fields: `id`, `split`,
  `category`, `critical`, `question`, `expected_status`, `expected_sql`, `check_sql`,
  `expected_snapshot`, `comparison`, `traps`, `naive_sql`, `must_mention`, `verified`.
- **Mix:** simple 3, aggregation with dates 3, joins 3, traps 6, criteria in the question 2,
  ambiguous 3, no data 2 (one true zero, one false zero), unsafe or out of scope 2. Questions are
  written as a person would ask them, in English, including a few short or colloquial ones.
- **Rules:** SQL first, verified a second way (`check_sql` on the base tables); trap cases keep a
  `naive_sql` with a different result; no ties in top-N, no `LIMIT` without `ORDER BY`, no dependence
  on the real date; the comparison function is one unit-tested function (multiset by default,
  ordered for rankings, numeric tolerance 0.01, ignore column names and extra columns).
- **Metrics:** execution accuracy, status accuracy, clarification correctness, unsafe queries executed
  (target 0), recall@k, citation validity, cost per question (mean, p95), latency (p50, p95), LLM calls
  per question. Final runs use three repetitions when the budget allows; report the mean and the share
  passing all three.
- **Comparison:** baseline versus A on the base model, with a quality gate (no unsafe query executed,
  no critical case regressing), in the style of course session 5.
- **Discipline:** develop on the development split; run holdout once at the end; write one failure
  diagnosis, one targeted improvement and the before and after.
- **Security tests:** about 15 attacks (DROP, stacked statements, comment tricks, `pg_sleep`,
  `COPY ... TO PROGRAM`, system catalogs, `SET ROLE`, `FOR UPDATE`, CTE wrapping an INSERT, deep
  nesting, exploding cross join), each naming the rule that must fire, plus the same attacks sent
  straight to the database role to prove it refuses by itself. An offline test where the fake LLM
  returns a destructive query proves it is never executed.

## 9. Steps

### Step 0. Foundation (1h)
- [x] Repo, `.gitignore`, `pyproject.toml`, uv, Ruff, pre-commit, CI skeleton, `.env.example`,
  `.gitattributes`, folders, README skeleton, `config/pricing.toml` skeleton, `docs/LOG.md`.
DoD: `uv sync` works, hooks run, first commit on `dev`.

### Step 1. PostgreSQL, data, Compose (limit 4h)
- [x] Generator on PostgreSQL, parity check against SQLite, `lead_time_days`, frozen data v1,
  `make dump`, `app_ro` role, Compose `db`, tests.
DoD: `docker compose up -d db` loads the data; parity report identical; tests green.

### Step 2. Contracts and measurement (2h)
- [x] `config.py`, `schemas.py`, `trace.py`, `llm.py`, `pricing.py`, `fake_llm.py`, unit tests.
DoD: a fake call yields correct cost, latency and p50/p95.

### Step 3. Safe SQL (Mon night to Tue 10:00)
- [x] Real prices for `gpt-4.1-mini` and `text-embedding-3-small` in `config/pricing.toml`.
- [x] `guard.py` (sqlglot, dialect postgres): one statement, SELECT only (writes, DDL, `COPY`, `SET`
  anywhere, CTEs included), no `SELECT INTO`, no locking clauses, table and function allowlists,
  depth limit, forced outer `LIMIT`, structured `GuardViolation`. No regex for security.
- [x] `executor.py`: READ ONLY transaction, `SET LOCAL statement_timeout`, EXPLAIN cost check, row
  preview, typed errors, rollback. `diagnose.py`: zero-row diagnostic (drop one filter at a time).
- [x] Security suite from section 8 (guard unit tests and role integration tests):
  `tests/security/test_guard_attacks.py` and `tests/integration/test_role_attacks.py`.
DoD: every attack blocked by the intended rule; the role refuses them without the guard.

### Step 4. Golden set and comparison (Tue 10:00 to 12:30)
- [ ] 24 cases verified twice (`evals/verify_golden.py`); `evals/compare.py` with tests; fingerprint
  check; holdout marked and frozen; glossary decisions written down.
DoD: tests green; holdout never inspected until Step 10.

### Step 5. Knowledge and semantic search (Tue 13:30 to 15:00)
- [ ] `knowledge/` files, `make index`, `knowledge.py` (cosine top-k, loader with fingerprint check),
  recall@k on the development questions, choose k.
DoD: recall@k reported; startup loads the committed vectors without an API call.

### Step 6. Intake, variant A, baseline, CLI (Tue 15:00 to 19:00)
- [ ] `prompts/`, `intake.py` (LLM plus a deterministic decision layer), `tools.py`, `pipeline.py`,
  `baseline.py`, `cli.py` (`uv run ask "..."`).
- [ ] Offline tests of every status with the fake client and a fake executor, plus the injection test.
DoD: every status works offline in the tests and `uv run ask` answers a real question.

### Step 7. FastAPI and Docker (Tue 19:00 to 20:30)
- [ ] `api.py`: `GET /health` (independent of the LLM; reports `llm_configured`), `POST /v1/query`,
  `GET /v1/examples`. 400 invalid input, 422 schema, 502 upstream failure, 503 database down.
- [ ] `Dockerfile` (uv), `.dockerignore`, Compose `api` (key only to `api`, healthcheck),
  `scripts/smoke.py`.
DoD: from a clean clone, `docker compose up --build -d --wait` brings `db` and `api` up healthy and
the smoke test passes.

### Step 7b. Runner and first results (Tue 20:30 to 23:00) -> minimum deliverable
- [ ] `evals/run.py` (JSON and Markdown report, cost estimate and confirmation, `--max-cases`,
  `--repeat`), `evals/report.py` (baseline versus A, quality gate).
- [ ] First development results and one written failure diagnosis; README rewritten results-first.
DoD: baseline and A run on the development split with accuracy, cost and latency; 0 unsafe queries
executed; pull request to `main`.

### Step 8. Streamlit UI (Wed 08:30 to 10:30)
- [ ] One page over HTTP: question box, example questions, answer with status, clarification
  options as buttons, SQL, rows, evidence, diagnostic, steps with cost and latency, banner when the
  key is missing or the API is down. Compose `ui`.
DoD: from a clean clone, the page answers a question end to end and walks through a clarification.

### Step 8b. Verification and one improvement (Wed 10:30 to 12:00)
- [ ] `verify.py`: sanity checks (raw-currency sums, carts without the completed filter, truncation),
  invalid citations dropped, simple confidence with breakdown. One targeted improvement, before and after.
DoD: confidence visible in the answer; unit tests per check.

### Step 9. Variant B, optional (Wed 12:00 to 15:00, hard stop)
- [ ] Only if Steps 3 to 8b are done by 12:00: manual tool loop (course session 7) with the same tools,
  allowlist, structured tool errors, `max_steps`, budget; A versus B on development.
DoD: A versus B comparison, or B documented as future work in the README.

### Step 10. Close-out (Wed 15:00 to 18:00)
- [ ] Final development runs, holdout once per variant. README (how to run first, results, what to
  try, safety, architecture, evaluation, decisions, limitations, future work). `docs/results/`,
  `docs/architecture.md`, `docs/evaluation.md`. Clean-clone test. Tag `v1.0`.
DoD: delivered before the 18:00 cut-off with margin.

## 10. Schedule

| When | Steps | Checkpoint |
|---|---|---|
| Mon 2026-10-19 night | 3 (prices, guard, attack tests) | |
| Tue 2026-10-20 morning | 3 (executor, diagnose, role tests), 4 | Pull request at Step 3 DoD |
| Tue afternoon | 5, 6 | |
| Tue night | 7, 7b | Pull request: minimum deliverable (`docker compose up` serves the API) |
| Wed 2026-10-21 morning | 8, 8b | Pull request |
| Wed 12:00 to 15:00 | 9 (gated) | |
| Wed 15:00 to 18:00 | 10 | Pull request and `v1.0` |

If Tuesday runs late, Step 7b's results move to Wednesday 08:30 and the UI starts after them.
FastAPI and Docker are never pushed past Tuesday.
Cut order: variant B, second model, verification beyond two checks, three repetitions (fall back to
one), UI extras (variant selector, step panel).
Never cut: `docker compose up` starting `db`, `api` and a basic Streamlit page; guard and read-only
role; golden set with the baseline versus A comparison; clarification, no-data and refusal handling;
cost and latency table; README with results; tests.

## 11. Out of scope
Roles and personas, web search, external supplier lists, user feedback loop, authentication, cloud
deployment, multi-agent orchestration, long-term memory, a vector database, multi-query validation
and back-translation.
