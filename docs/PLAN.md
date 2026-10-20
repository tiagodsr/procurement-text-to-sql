# Plan

## Goal

A small, clean assistant that answers procurement questions over a PostgreSQL database with safe
read-only SQL, asks for clarification instead of guessing, and explains the result with evidence,
cost and latency. Course idea 04 ("Text-to-SQL Assistant"). A reader should understand every step.

## Flow

```text
question -> intake LLM (sql | clarify | out_of_scope | unsafe)
         -> semantic search (table docs + example queries, cosine over committed vectors)
         -> SQL LLM -> guard -> read-only execute
         -> SQL error: repair LLM with the error message (max 2)
         -> zero rows: diagnostic of which filter removed them -> no_data
         -> explain LLM -> answer citing rows
```

Statuses: `answered`, `needs_clarification`, `no_data`, `out_of_scope`, `refused`, `error`.

## Always visible

- Trace per request: each step with model, tokens, cost and latency.
- Evidence in every answer: retrieved docs with score, the SQL, the cited rows.
- Cost and latency in the API response and in the evaluation report.
- `docker compose up` starts db, api and ui; `/docs` works; `uv run ask "..."` works.

## Never cut

Docker + FastAPI + simple Streamlit; guard + read-only role; clarification, no_data and refusal
handling; golden set with the baseline comparison; trace, cost, latency and evidence; README; tests.

## Not built

Answer verification and confidence scores, repeated runs, index fingerprint checks, recall tuning,
a second model, extra Streamlit panels, entity-resolution tools, a vector database.

## Phases

One phase at a time; stop at the end of each.

### Phase 1. Cleanup

The approved cut; docs point only at files that exist; README "map of the repo"; tests green.

### Phase 2. Vertical slice

`uv run ask "<question>"` end to end with a real LLM: intake -> SQL (all table docs and the glossary
in the prompt) -> guard -> execute -> explain. Then repair (max 2), clarification, no_data,
out_of_scope, refused. Prompts in `prompts/*.md`. Offline tests with the fake LLM for every status,
plus a prompt-injection test. Then semantic search: about 25 docs (table sections + example
queries), plain cosine, committed vectors, evidence in the answer, recall@3 on the development
questions.
Done when every status is tested offline and a real question is answered with evidence.

### Phase 3. Demo surface

FastAPI (`GET /health`, `POST /v1/query` returning answer, evidence, trace, cost, latency),
Dockerfile, Compose `api`, one Streamlit page over HTTP, Compose `ui`.
Done when a clean clone runs `docker compose up` and answers a question in the page and in `/docs`.

### Phase 4. Evaluation

16 cases (12 development, 4 holdout run once): simple, join, trap, ambiguous, no data, unsafe.
Baseline (one prompt, full schema) versus the workflow: execution accuracy, status accuracy, unsafe
queries executed (must be 0), cost, latency. Trap cases verified a second way. The runner prints a
cost estimate and asks before spending. Budget 5 USD.

### Phase 5. README

Results first: what it does, run in three commands, example questions, flow diagram, safety,
evaluation results, limitations, future work.

### Phase 6. Optional: agent with tools

Only if Phases 1 to 5 are merged and there is time: a manual tool loop (`search_schema`, `run_sql`)
as in course session 7, compared on the same golden set. Otherwise: README future work.
