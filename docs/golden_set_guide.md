# Synthetic data and the golden set

How to know the data exactly, and how to turn it into evaluation cases you can trust. Read this before Step 4 of [PLAN.md](PLAN.md).

## 1. The data is random, but it is not unknown

The generator is seeded, so seed 42 always produces the same rows. That makes the database a **fixed test fixture**, not a moving target. Two things follow:

- You can compute the right answer to any question by running SQL against it. The database is the oracle.
- Because the data was generated with deliberate traps (see `docs/data_model.md`, "Key facts"), you know in advance what a careless query gets wrong.

You do not need to memorise 17,000 rows. You need to (a) freeze the data, (b) profile it, (c) write cases SQL-first, and (d) prove that each trap case actually discriminates. The rules below do that.

## 2. Five rules

1. **Freeze the data.** Give the dataset a version (`DATA_VERSION = "v1"`) and a fingerprint: a SHA-256 of every table in a canonical form (`assistant.fingerprint`; `make dump` writes it to `data/fingerprint.json`). Store the fingerprint in `evals/golden_set.json`. A test rebuilds the data and fails if the fingerprint differs: "the golden set was written for different data, re-verify it". Any change to the generator, even the order of random draws, changes the data.
2. **Profile before you write.** Generate `docs/data_profile.md` with `scripts/profile_db.py` and read it. Pick cases from facts you can see, not from imagination.
3. **Write SQL first, then trust it only after verifying it.** The expected result is whatever your hand-written SQL returns, but you confirm it a second way (rule 5 of the checklist in section 8).
4. **Every trap case must discriminate.** Keep a `naive_sql`, the query a careless system would write, and assert that its result differs from the expected one. If they match, the case tests nothing.
5. **Avoid fragile cases.** No ties in a top-N, no `LIMIT` without `ORDER BY`, no dependence on the real current date, no unrounded floats.

## 3. Know the data

### Profile output (`docs/data_profile.md`)

Row counts per table and view; suppliers by status, risk, country and currency; the category by country matrix (active suppliers offering active items); carts per month and per status (to see the partial year 2026); the currency mix with raw and EUR sums; top suppliers per year with the gap to the next one; duplicate brands and the suppliers behind them; `NULL` rates of nullable columns; description variants for a few keywords in several languages; contract statuses and lines that reference expired or terminated contracts; delivery-time coverage; carts in approval; the fingerprint and data version.

### Probe queries (PostgreSQL, schema `public`)

```sql
-- 1. Brands that map to more than one legal entity
SELECT a.alias, count(DISTINCT a.supplier_id) AS entities,
       string_agg(s.name, ' ; ' ORDER BY s.name) AS names
FROM supplier_aliases a
JOIN suppliers s USING (supplier_id)
GROUP BY a.alias
HAVING count(DISTINCT a.supplier_id) > 1;

-- 2. Leaf category x country with no active supplier offering an active item (true "no suppliers" candidates)
SELECT c.name AS category, s.country_code
FROM categories c
CROSS JOIN (SELECT DISTINCT country_code FROM suppliers) s
WHERE c.parent_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1
    FROM catalog_items i
    JOIN suppliers sup USING (supplier_id)
    WHERE i.category_id = c.category_id
      AND sup.country_code = s.country_code
      AND sup.status = 'active'
      AND i.active)
ORDER BY 1, 2;

-- 3. Spend ranking with the gap to the next supplier (tie check)
WITH spend AS (
  SELECT supplier_id, SUM(total_value_eur) AS spend_eur
  FROM v_completed_lines
  WHERE cart_created_at >= DATE '2025-01-01' AND cart_created_at < DATE '2026-01-01'
  GROUP BY supplier_id)
SELECT supplier_id, spend_eur,
       spend_eur - LEAD(spend_eur) OVER (ORDER BY spend_eur DESC) AS gap_to_next
FROM spend
ORDER BY spend_eur DESC
LIMIT 10;

-- 4. Why summing across currencies is wrong
SELECT currency, count(*) AS lines,
       sum(total_value) AS raw_sum, sum(total_value_eur) AS eur_sum
FROM v_completed_lines
GROUP BY currency
ORDER BY lines DESC;

-- 5. Free-text matching: one keyword versus all language variants
SELECT
  count(*) FILTER (WHERE item_description ILIKE '%laptop%') AS keyword_only,
  count(*) FILTER (WHERE item_description ILIKE ANY (
        ARRAY['%laptop%', '%notebook%', '%ordinateur portable%'])) AS all_variants,
  count(*) FILTER (WHERE category_id IS NULL AND item_description ILIKE ANY (
        ARRAY['%laptop%', '%notebook%', '%ordinateur portable%'])) AS variants_without_category
FROM v_completed_lines;

-- 6. Non-active suppliers that still have purchases
SELECT s.name, s.status, s.risk_rating, max(v.cart_created_at) AS last_purchase
FROM v_completed_lines v
JOIN suppliers s USING (supplier_id)
WHERE s.status <> 'active'
GROUP BY s.name, s.status, s.risk_rating
ORDER BY last_purchase DESC;

-- 7. Purchases made under contracts that are no longer valid
SELECT c.status, count(*) AS lines
FROM v_completed_lines l
JOIN contracts c USING (contract_id)
WHERE c.status IN ('expired', 'terminated')
GROUP BY c.status;
```

To explore interactively: `docker compose exec db psql -U app_ro -d procurement`, then `\dt`, `\dv` and `\d+ v_completed_lines`.

### Snapshot of what the traps look like (seed 42, data v1, measured 2026-10-17 on PostgreSQL)

Re-measured after the PostgreSQL port with the probes above. The base data is identical to the SQLite build; derived EUR amounts can differ by a cent where SQLite rounded a float (see `docs/LOG.md`, parity report).

| Fact | Measured |
|---|---|
| Suppliers | 96 (84 active, 7 inactive, 5 blocked) |
| Carts | 7,200 (6,270 completed, 544 cancelled, 352 rejected, 34 in approval) |
| Brands shared by two legal entities | 6 (Brabon, Mernova, Nornova, Velria, Venlon, Zengen) |
| Cart lines without a category | 673 of 17,160 (3.9%) |
| Total 2025 spend, careless (all statuses, raw mixed currencies) | about 40.5 million |
| Total 2025 spend, completed only but still mixed currencies | about 34.0 million |
| Total 2025 spend, completed and converted to EUR | about 13.45 million |
| Completed lines in CNY, raw sum versus EUR | about 38.5 million versus about 5.0 million |
| Completed lines matching `laptop`, one keyword versus all language variants | 124 versus 416 (9 of those have no category); over all lines, any status: 147 versus 481 (11). `notebook` also matches the office item "Notebook A5 lined" |
| Leaf category x country pairs with no active supplier offering an active item | 101 of 156 |
| Top supplier by 2025 completed EUR spend | Lumlis Fasteners GmbH, 1,017,863.22 (next: 979,605.76, so no tie; SQLite gave 979,605.75) |

Two consequences: "no suppliers" answers are common, so you need both genuine empty results and **false zeros** (a result that is empty only because one filter is too strict); and a system that ignores the completed-only or currency rules is wrong by a factor of three, so those cases discriminate strongly.

## 4. Decide the definitions once

Write them in `docs/glossary.md` (or a section of this file) before writing any case. They are also what the schema index and the system prompt will carry.

| Term | Decision to record |
|---|---|
| "Spend in year Y" | By cart `created_at` or `completed_at` (D-OPEN-2, default `created_at`) |
| "Last year", "this year", "last quarter" | Relative to `meta.as_of_date` (2026-10-03); 2026 is a partial year |
| "Purchase" and "spend" | Completed carts only |
| Amounts | EUR via the `_eur` columns; never summed across currencies |
| "Active supplier" | `status = 'active'` |
| "Supplier for category X" | Has an active catalog item in X (state whether contracts matter) |
| "Delay" | `actual_days - promised_days`, only where both exist |
| Ambiguous brand | Ask, do not guess (D-OPEN-5) |

## 5. Anatomy of a case

```json
{
  "id": "spend-top-supplier-2025",
  "split": "development",
  "category": "trap_completed_and_currency",
  "critical": true,
  "question": "Which supplier did we spend the most with in 2025, and how much in EUR?",
  "expected_status": "answered",
  "expected_sql": "SELECT s.supplier_id, s.name, ROUND(SUM(v.total_value_eur), 2) AS spend_eur FROM v_completed_lines v JOIN suppliers s USING (supplier_id) WHERE v.cart_created_at >= DATE '2025-01-01' AND v.cart_created_at < DATE '2026-01-01' GROUP BY s.supplier_id, s.name ORDER BY spend_eur DESC LIMIT 1",
  "expected_snapshot": {"columns": ["supplier_id", "name", "spend_eur"], "rows": [[44, "Lumlis Fasteners GmbH", 1017863.22]]},
  "comparison": {"ordered": true, "numeric_tolerance": 0.01, "extra_columns_allowed": true},
  "traps": ["only completed carts count", "amounts must be converted to EUR"],
  "naive_sql": "SELECT supplier_id, SUM(total_value) FROM <all lines, any status, raw currency> ...",
  "verified": {"method": "re-computed from the base tables without the views", "data_version": "v1", "date": "YYYY-MM-DD"}
}
```

The snapshot values above were measured on PostgreSQL (data v1).

Fields by case type:

| Type | Required fields | Notes |
|---|---|---|
| Answerable | `expected_sql`, `expected_snapshot`, `comparison` | Add `traps` and `naive_sql` for trap cases |
| Ambiguous | `expected_status: needs_clarification`, `must_mention` (the candidates) | Example: "Is Brabon an active supplier?" must mention both Brabon entities |
| True "no data" | `expected_status: no_data`, `expected_sql`, `expected_diagnostics` | Example: active suppliers offering Facility & Cleaning items in Austria (verified empty by probe 2); diagnostics show that dropping the country filter returns rows |
| False zero | As above, plus the single filter that causes the emptiness | Build it by tightening one filter on a query that has rows |
| Out of scope | `expected_status: out_of_scope`, `max_db_statements: 0` | A question unrelated to procurement |
| Unsafe | `expected_status: refused`, `max_db_statements: 0` | Write or DDL intent, injection text |

## 6. Comparison rules

Execution match should be strict about values and tolerant about presentation.

- Compare rows as a multiset, unless `ordered: true` (use it for rankings and top-N).
- Ignore column names. Match each expected column to a returned column by its values, so extra or reordered columns still pass when `extra_columns_allowed` is true.
- Numbers: absolute tolerance of 0.01 for EUR amounts; round both sides to the precision the question implies.
- Strings: trim and compare case-insensitively, except identifiers.
- Dates: compare as ISO dates.
- A top-N expected result requires an `ORDER BY` and a gap between the last included and the first excluded value.
- An empty expected result is allowed only in `no_data` cases, where the status is the primary check.
- Keep the comparison in one function, unit-tested with passing and failing examples, so the metric itself cannot be the bug.

## 7. How many cases, and the split

32 cases, 22 development and 10 holdout, stratified by category so both splits contain every type (the mix in `docs/PLAN.md`, section 8).

| Category | Cases |
|---|---:|
| Simple lookups (supplier attributes, catalog) | 5 |
| Aggregation with time filters (spend, counts by year or quarter) | 5 |
| Multi-table joins (supplier, category, contract) | 4 |
| Traps (completed-only, currency, `NULL` category, language variants, inactive or blocked, expired contracts, delay) | 7 |
| Criteria in the question (the ranking criterion is stated by the user) | 3 |
| Ambiguity needing clarification (two-entity brands, vague criteria, missing period) | 3 |
| No data: true zero, false zero, unanswerable | 3 |
| Unsafe or out of scope | 2 |

With 32 cases one case is roughly 3.1 percentage points. Report counts ("27 of 32") next to percentages, and per category. With 3 repetitions report the mean pass rate and the share that passed all three.

Do not look at the holdout cases' system outputs until Step 10, and do not edit them after seeing results.

## 8. Verification checklist (per case)

- [ ] The case comes from a fact in the profile or a documented trap, not from a guess.
- [ ] The question is phrased as a person would ask it: no table or column names, varied wording.
- [ ] `expected_sql` is written by hand and passes the SQL guard.
- [ ] The result is plausible, has no ties, and is not trivially empty or single-row unless designed.
- [ ] **Independent confirmation:** the same answer comes from a different formulation, for example the base tables instead of the views, or a short Python recomputation. This also checks the views.
- [ ] For trap cases, `naive_sql` returns a different result.
- [ ] The snapshot is frozen and the data version and date recorded.
- [ ] Holdout cases are marked and left alone.

## 9. Three files, never mixed

| File | Purpose | Rule |
|---|---|---|
| `evals/golden_set.json` | Measure the system | Written before the pipeline; holdout never inspected until Step 10 |
| `knowledge/examples.yaml` | Teach the model patterns in the prompt and the index | At least 10; no question or normalised SQL identical to a golden case. A test enforces this |
| `evals/regression_cases.json` | Bugs found later | Failures worth keeping; never promoted automatically into few-shot or golden |

Few-shot examples will share patterns with the golden set, which is unavoidable. Keep entities, periods and categories different, and note the limitation in the README.

## 10. Using AI to help

Reasonable: brainstorm phrasings and paraphrases, draft SQL for you to verify, translate a subset of questions.

Not acceptable: accept an AI-written expected result without independent confirmation; ask the system under test to produce its own references; tune prompts using holdout failures; paste golden cases into prompts or few-shot examples.

## 11. When the data changes

1. Bump `DATA_VERSION` in `scripts/freeze_data.py` and run `make dump` (rebuilds the data and the fingerprint).
2. Run the golden-set verification: it re-executes every `expected_sql` and diffs the result against `expected_snapshot`.
3. Review every case that changed; update the snapshot only after re-doing the independent confirmation.
4. Commit the data change and the golden-set change together, so no commit has a stale golden set.
