# Glossary: business rules for every query

These rules always apply. They are part of the SQL instructions for every question.

1. **"Today" is `meta.as_of_date` (2026-10-03), not the real current date.** Resolve relative periods
   against it, by calendar: "this year" is 2026 to date, "last year" is the calendar year 2025,
   "last quarter" is 2026-Q3; only "the last 12 months" (or 30 days...) counts back from today.
   Purchase history runs from `meta.data_from` (2023-01-02) to `meta.data_to` (2026-09-30), so
   2026 is a partial year. Purchase dates are `completed_at` unless the question is about requests.
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
6. **Sister suppliers share a brand.** The six brands below each exist as two legal entities in
   different countries, and the brand alias in `supplier_aliases` maps to both suppliers. A
   question that names only the brand (`Velria`, `VELRIA`...) is ambiguous, whatever else it asks:
   the user must say which entity they mean, or both, before any query runs. Never pick one entity
   or add both up without being told. It is not ambiguous when it names one entity (its legal name
   with or without the suffix, such as `Velria Tools`), gives a country that picks one entity, or
   says "both" or "all". A name match such as `ILIKE 'velria%'` finds both entities. Every other
   brand belongs to one supplier.

   | Brand   | Legal entities (country)                                  |
   |---------|-----------------------------------------------------------|
   | Brabon  | Brabon Packaging GmbH (DE); Brabon America Inc. (US)      |
   | Mernova | Mernova Benelux B.V. (NL); Mernova Safety SAS (FR)        |
   | Nornova | Nornova Office Sp. z o.o. (PL); Nornova America Inc. (US) |
   | Velria  | Velria Tools SAS (FR); Velria America Inc. (US)           |
   | Venlon  | Venlon Chemicals B.V. (NL); Venlon UK Ltd (GB)            |
   | Zengen  | Zengen Office GmbH (DE); Zengen China Co., Ltd (CN)       |

7. **Supplier status matters.** `inactive` and `blocked` suppliers still have past purchases.
   Blocked suppliers are always `risk_rating = 'high'`, and any contract that ran past the block date
   is `terminated`. When recommending suppliers, prefer `status = 'active'`.
8. **Contract status is as of `meta.as_of_date`.** A cart line can reference a contract that was
   valid at purchase time and is now `expired`. Only `published` contracts are currently valid.
   `draft` contracts start in the future.
