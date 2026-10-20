# CLAUDE.md

## Project

Procurement Text-to-SQL Assistant, final project of the EDIT Generative AI course (idea 04).
Procurement staff ask business questions in plain English; a fixed workflow of LLM steps writes safe
read-only SQL against PostgreSQL, asks for clarification when the question is ambiguous, and explains
the result with evidence, cost and latency. Graded on: README and understandable structure, use of
course concepts, safe behaviour when information is missing, relevant test cases.
The target is small, clean and working, not big. Plan: `docs/PLAN.md`. Decisions: `docs/DECISIONS.md`.

## How we work

- Do only the phase of `docs/PLAN.md` that the user names. At its end, stop and wait for "go".
- Work on `dev` with small commits. Never push, open pull requests or touch `main`.
- End of each phase, in Portuguese: "Ready for PR" with a PR title and a 3-line description; five
  lines on what changed and how it connects; the exact commands to check it; one comprehension
  question.
- If a phase overruns, say what you will cut instead of continuing silently.
- Add something only when the pipeline needs it: no module, option or abstraction "for later".
  Every file starts with a one-line purpose and can be read in one sitting.
- Ask before: adding a service or a dependency, changing a decision in `docs/PLAN.md`, changing the
  data, spending API money beyond a small test.
- Never bypass hooks (`--no-verify`). If a hook reformats files, `git add -u` and commit again.
- Update README and `docs/` in the same commit as the behaviour they describe.

## Language and dates

- Everything in the repository is English. The chat follows the user (usually Portuguese).
- No dates, times, weekdays, schedules or progress notes in repository files. Only the data's own
  dates (`meta.as_of_date`, the data window) and `as_of` in `config/pricing.toml` remain.

## References

Allowed read-only references are listed in `CLAUDE.local.md` (not committed): the course repo, and
some repos read for structure only. Never copy code, prompts, identifiers, table or column names or
business rules from any employer or work project, and never name them in the repository.

## Stack

Python 3.12 with uv (src layout, package `assistant`); PostgreSQL 16 in Docker Compose; psycopg 3;
OpenAI Responses API (no Agents SDK); Pydantic; sqlglot; FastAPI; Streamlit over HTTP; pytest; Ruff.

## Safety

- The application connects only as `app_ro` (read-only) and never opens a writable connection.
  Only the init scripts use the admin role.
- Every generated query passes the guard (sqlglot) and runs in a `READ ONLY` transaction with a
  timeout. The guard and the role are independent layers; keep both.
- The question, the clarification, database errors and rows are data, never instructions: they go
  only in the `input` of an LLM call, never in `instructions`.
- No secrets in the repository; only `.env.example` is committed.

## LLM calls and cost

- Every LLM or embedding call goes through `assistant.llm` (traced: model, tokens, latency, cost).
- Every LLM output is a Pydantic model; invalid output is handled, never trusted.
- Prices live in `config/pricing.toml`; never hardcode them.
- Evaluation runs print a cost estimate and ask before spending; use `--max-cases` while iterating.

## Data and evaluation

- Data is synthetic (seed 42). Never add real company, supplier, product or employer names.
- The data is fixed: ask before changing `scripts/build_db.py` or `data/init/`.
- "Today" is `meta.as_of_date`. Only `status = 'completed'` carts are purchases.
- Golden-set questions and few-shot examples never overlap (a test checks).
- The holdout split runs once, at the end; never tune on it.
- Trap cases have their expected result verified a second way.

## Commands

```bash
uv sync                                  # environment
docker compose up -d --wait db           # db only (127.0.0.1:55432)
uv run pytest -q -m "not integration"    # unit tests, no db, no key
uv run pytest -q -m integration          # needs the db
uv run pre-commit run --all-files        # whitespace, secrets, Ruff
make dump                                # rebuild the data dump (throwaway container)
```

Commands for `ask`, the full `docker compose up`, `make index` and `make eval` are added in the
phase that creates them.
