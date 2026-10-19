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

## Step 1. PostgreSQL, data, Compose (started 2026-10-17 16:05, time box 4 h, ends 20:05)

**Generator port (`scripts/build_db.py`).** Same seeded generator, now writing PostgreSQL 16.
Input: a DSN (`--dsn` or `BUILD_DATABASE_URL`) whose database name is on an allowlist
(`procurement_build`), and the seed (42). Output: the tables, indexes and two views in `public`.
Only the schema (`DATE`, `NUMERIC`, `BOOLEAN`, `BIGINT`), the connection and the inserts (`%s`,
`bool()`) change; every random draw stays in place, so the rows must equal the SQLite build. The
allowlist exists because the script drops its tables before writing.

**Data fingerprint (`src/assistant/fingerprint.py`).** Computes a SHA-256 per table and one overall
fingerprint. Input: rows (from PostgreSQL, SQLite or the generator itself); output: row counts and
checksums. Every value is first turned into a canonical string (decimal numbers normalised, booleans
as 1/0, dates as ISO, NULL as `\N`) and the lines of a table are sorted, so the same data gives the
same hash whatever the engine, column type or physical row order. One function serves the parity
check, the frozen-data manifest and the tests, so they cannot disagree.

**Build target (`Makefile`: `build-db`, `dump`).** `make build-db` starts a throwaway container from
the same pinned image as Compose (`postgres:16.15-bookworm`, name `pts-build`, `127.0.0.1:55433`),
waits for it over TCP and runs the generator into `procurement_build`. `make dump` then runs
`pg_dump` and `gzip -n` inside that container (same binaries every time, fixed `--restrict-key`),
writes `data/init/01_dump.sql.gz` and the manifest, and removes the container. The Compose database
is never written by the generator; it only loads the committed dump.

**Parity check (`scripts/parity_check.py`, one-off).** Proves the PostgreSQL build holds the same
data as `.local/sqlite_reference.db`. Inputs: the SQLite file and the build DSN; output: a table of
row counts and SHA-256 per table, exit code 1 on any mismatch. It reads only the columns that exist in
SQLite, so it still applies after `lead_time_days` is added and shows that the new column changed
nothing else. Views are reported for information only (float versus NUMERIC rounding may differ).

**Parity report (2026-10-17 16:40, `make parity`, before `lead_time_days`).** All ten tables match
`.local/sqlite_reference.db` on row counts and SHA-256. Overall fingerprint, identical for the
SQLite reference, the PostgreSQL build and the generator's own insert tuples:
`449092e9e09077df77ee3f27d2641b41bf8304b25f0b4a07070c10e1e387c35d`.

| table               |   rows | sha256 (first 16) | match |
|---------------------|-------:|-------------------|-------|
| catalog_items       |    522 | 1ec84ef633ebe492  | yes   |
| categories          |     16 | 39da78f43961f194  | yes   |
| contracts           |    117 | 5ac097c858ec764b  | yes   |
| exchange_rates      |      7 | 82f452925ba2cede  | yes   |
| meta                |      5 | b1531fe8abb761bd  | yes   |
| plants              |      8 | f9d3c58d39ee7a95  | yes   |
| shopping_cart_items | 17,160 | 7d8631eefe5df8eb  | yes   |
| shopping_carts      |  7,200 | 935842429c629ba8  | yes   |
| supplier_aliases    |     81 | 4b6bc1306a5187b4  | yes   |
| suppliers           |     96 | aafff03a51876777  | yes   |

The views have the same row counts (14,926 and 96) but different checksums, as expected: SQLite
rounds binary floats, PostgreSQL rounds exact `NUMERIC`. 45 of 14,926 `total_value_eur` values
differ by 0.01 (for example line 299: 111.73 in SQLite, 111.74 in PostgreSQL, because 111.735 is
stored as 111.73499... in a float); that adds up to at most 0.15 in `spend_eur` for 10 suppliers, and
one `avg_delay_days` differs by 0.01. PostgreSQL is the correct one. Consequence for Step 4: every
expected value is re-measured on PostgreSQL; the SQLite numbers in the golden-set guide are not
reused as they are.

**`catalog_items.lead_time_days`.** The generator already drew a lead time per catalog item
(`rng.randint(2, 21)`), but only wrote it into the free text of `item_description`. The draw is now
bound to a variable at the same position and also stored as an `INTEGER NOT NULL` column (the last
one), so lead-time questions need no string parsing. The draw order is unchanged, so every other
value stays as it was: the parity check, which reads only the SQLite columns, must still pass.
Parity re-run after adding the column (2026-10-17 17:00): all ten tables still identical on the
SQLite columns (same checksums as above); in PostgreSQL, `item_description` ends with
`lead time <lead_time_days> days` for all 522 items, range 2 to 21.

**Freeze (`scripts/freeze_data.py`, run by `make dump`).** Writes `data/fingerprint.json`, the
manifest of the frozen data. Inputs: the build DSN and the dump file; output: data version (`v1`),
seed, `as_of_date`, overall fingerprint, rows and SHA-256 per table and per view, and the SHA-256 of
`01_dump.sql.gz`. It lives outside `data/init/` so the database entrypoint ignores it. Tests compare
the generator, the committed dump and the loaded database with this one file; Step 4 copies the
fingerprint into `evals/golden_set.json`. No timestamp is written, so re-running `make dump` on
unchanged data leaves the file byte-identical.

**Read-only role (`data/init/02_roles.sql`).** Runs once, after the dump, on the first start of the
`db` container. It creates `app_ro`: login, no superuser or create rights, `SELECT` on every table
and view in `public` and nothing else. PUBLIC loses `CREATE` on the schema and `TEMP`/`CONNECT` on
the database, so the role cannot create even a temporary table. Role defaults:
`default_transaction_read_only = on`, `statement_timeout = 5s`,
`idle_in_transaction_session_timeout = 30s`. The read-only default can be switched off by a session,
which is why the privileges, not the default, are the real barrier; the guard (Step 3) is a separate,
independent layer. The password `app_ro_local` is a documented local default: the base Compose file
never publishes the port and the role can only read.

**Compose (`docker-compose.yml`, `compose.dev.yml`).** One `db` service on the pinned image; the
committed `data/init/` is mounted read-only as `/docker-entrypoint-initdb.d`, so the first start
loads the dump and the role script (no seed service, no generator at runtime). The healthcheck uses
`pg_isready` over TCP, which only succeeds after the init scripts finish. No port is published.
`compose.dev.yml` adds `127.0.0.1:55432` for host-side integration tests and exploration only.

**Frozen data v1 (2026-10-17 18:20).** `make dump` twice gave the same bytes (dump SHA-256
`3341d5af...`, 583 KB). Overall fingerprint `ed9d9527...` (after `lead_time_days`); the generator, the
committed dump and the loaded Compose database all reproduce it (unit and integration tests).
A fresh `docker compose up -d --wait db` loads it and reports healthy in about 10 s.

Findings while testing, for later steps:
- A non-owner's `GRANT` is only a warning in PostgreSQL, not an error, so the role test checks that
  nothing was granted instead of expecting an exception. The security suite (Step 3) must do the same.
- `information_schema.table_constraints` hides constraints from a SELECT-only role; schema
  introspection for `app_ro` (for example `GET /v1/schema`) must use `pg_catalog`.
- Inside the container, `psql -U app_ro` over the Unix socket needs no password (the image trusts
  local socket connections). Over TCP the password is required (tested).
- Golden-set probes re-run on PostgreSQL: every snapshot figure matches except two. The `laptop`
  counts in the old guide were over all lines, not completed lines (124/416/9 on completed lines),
  and the runner-up 2025 spend is 979,605.76 (SQLite float rounding gave .75). `notebook` also
  matches the office item "Notebook A5 lined", a trap worth a case in Step 4.

Step 1 finished at 18:35, 2 h 30 min of the 4 h time box. No fallback to SQLite needed.

## Step 2. Contracts and measurement (started 2026-10-18 12:06, time box 2 h)

**Settings and prices (`src/assistant/config.py`, `config/pricing.toml`).** `load_settings(env)`
turns environment variables into a frozen `Settings` (key as `SecretStr`, models, timeout, trace
folder, pricing path); the environment is a parameter, so tests pass a dict. The database URL must use
the `app_ro` user: one more layer for the read-only rule, next to the role and the guard.
`load_pricing(path)` reads the TOML with `Decimal` numbers (no float error in cost) into a `Pricing`
model: one `ModelPrice` per model (`chat` needs `output`, `embedding` must not have it; a missing
`cached_input` bills cached tokens at the input price, which can only overestimate), and `as_of` is
required once any price is listed. `price(model)` fails loudly for an unpriced model, so no call is
ever made without a known cost. No settings library: a dozen lines of reading `os.environ` is enough.

**Contracts (`src/assistant/schemas.py`).** Every Pydantic model that crosses a module boundary: the
request, the three LLM outputs, tool and executor results, the trace event and the final answer.
Inputs: dicts or JSON; outputs: validated, frozen objects (unknown fields are rejected). The status
rules of PLAN.md section 5 are model validators on `FinalAnswer`, so a pipeline bug that would, say,
return `answered` without SQL fails loudly instead of reaching the user; `out_of_scope` and `refused`
are also checked against the trace (no `execute` event). LLM output models keep their limits in
validators, not in `Field` constraints, because strict structured outputs accept only a small subset
of JSON Schema; a test walks their schemas to prove it.
