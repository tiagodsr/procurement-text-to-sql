# Glossary: business rules for every query

These rules always apply. They are part of the SQL instructions for every question.

1. **"Today" is `meta.as_of_date` (2026-10-03), not the real current date.** Resolve "last year",
   "this quarter" and "currently" against it. Purchase history runs from `meta.data_from`
   (2023-01-02) to `meta.data_to` (2026-09-30), so 2026 is a partial year.
2. **Only carts with `status = 'completed'` count as purchases or spend.** Cancelled, rejected and
   in-approval carts still have lines. `v_completed_lines` applies the filter for you.
3. **Amounts are in several currencies.** `unit_price` and `total_value` are in the row's
   `currency`, which is always the supplier's currency. Never sum them across currencies. Convert
   with `exchange_rates.rate_to_eur` or use the `_eur` columns of the views.
4. **Item descriptions are free text.** They are in English, German or French, often in upper case,
   sometimes with a pack suffix (`PK 10`, `BULK`, `2 PACK`...), and about 5% have a typo (two
   swapped letters). Match them with case-insensitive `LIKE` on several keywords and language
   variants (laptop / notebook / ordinateur portable).
5. **About 4% of cart lines have `category_id IS NULL`.** A category filter misses them. When
   completeness matters, also search `item_description`.
6. **Sister suppliers share a brand.** Six brands exist as two legal entities in different
   countries (for example `Brabon Packaging GmbH` (DE) and `Brabon America Inc.` (US)). The brand
   alias (`Brabon`) maps to both suppliers. A question that names only the brand is ambiguous:
   ask which one, or report both.
7. **Supplier status matters.** `inactive` and `blocked` suppliers still have past purchases.
   Blocked suppliers are always `risk_rating = 'high'`, and any contract that ran past the block date
   is `terminated`. When recommending suppliers, prefer `status = 'active'`.
8. **Contract status is as of `meta.as_of_date`.** A cart line can reference a contract that was
   valid at purchase time and is now `expired`. Only `published` contracts are currently valid.
   `draft` contracts start in the future.
