# Tables and views

PostgreSQL 16, database `procurement`, schema `public`. All data is synthetic. Each `##` section
below describes one table or view. Dates are `DATE`, amounts `NUMERIC(14,2)`, exchange rates
`NUMERIC(10,4)`, discounts `NUMERIC(4,1)`, flags `BOOLEAN`, cart ids `BIGINT`.

## meta
Answers: what "today" is, the first and last purchase dates of the data.
5 rows of `key`, `value`. Holds `as_of_date` (the "today" of the data, 2026-10-03), `data_from`
(2023-01-02), `data_to` (2026-09-30), `seed` and `note`.

## exchange_rates
Answers: converting an amount in another currency to EUR, exchange rates.
7 rows. `currency` (PK), `rate_to_eur`. The currencies are EUR, USD, GBP, CHF, PLN, CZK and CNY,
with one fixed rate each (no history). EUR amount = amount × `rate_to_eur`.

## plants
Answers: the buyer's sites, plants, factories or locations, and their country or city.
8 rows. `plant_id`, `name` (`Site 01` to `Site 08`), `country_code`, `city`. The buyer's own sites;
carts reference them.

## categories
Answers: product categories and category groups, rolling spend up to a group.
16 rows. `category_id`, `name`, `code`, `parent_id`. A two-level tree:
- Four groups with `parent_id IS NULL`: Indirect Materials (IND), Technical Materials (TEC), IT (ITX)
  and Services (SRV).
- Twelve leaf categories underneath. Indirect Materials: `Personal Protective Equipment`,
  `Office Supplies`, `Packaging`, `Facility & Cleaning`. Technical Materials: `Hand Tools`,
  `Lab & Test Equipment`, `Fasteners & Fittings`, `Lubricants & Chemicals`. IT: `IT Hardware`,
  `Security & Authentication`, `Software & Licences`. Services: `Logistics & Services`.

Contracts, catalog items and cart lines always reference a leaf. To roll up to a group, join through
`parent_id`.

## suppliers
Answers: how many suppliers, where they are, their status (active, blocked), risk,
preferred flag, payment terms and billing currency.
96 rows.
- `supplier_id` (PK).
- `name`: full legal name including the suffix (`GmbH`, `Sp. z o.o.`, `Co., Ltd`...).
- `country_code` (ISO-2) and `city` (nullable: a few suppliers have no city).
- `currency`: billing currency. Chinese suppliers bill in CNY or USD.
- `status`: `active`, `inactive` or `blocked`.
- `risk_rating`: `low`, `medium` or `high`.
- `is_preferred`: boolean. Only active, low-risk suppliers are preferred.
- `payment_terms_days`: 30, 45, 60 or 90.
- `onboarded_at`: date. Can predate the data window (from 2015).

## supplier_aliases
Answers: finding a supplier by brand or short name.
81 rows. `alias_id`, `supplier_id`, `alias`. Each alias is either a short brand (`Velria`) or the
name without its legal suffix (`Dovtor Supply`). Not every supplier has an alias, and some have two.
An alias can map to more than one supplier (sister entities: see the glossary). Because a supplier
can have two aliases, never join this table into purchase lines: pick the suppliers with
`supplier_id IN (SELECT supplier_id FROM supplier_aliases WHERE alias ILIKE ...)`, or the lines of a
supplier with two matching aliases are counted twice.

## contracts
Answers: which suppliers have a contract (valid, expired, terminated, draft) for a
category, contract end dates, contract discounts.
117 rows.
- `contract_id`: text, for example `CTR-100001`.
- `supplier_id`, `category_id`: one contract covers one supplier and one leaf category.
- `status`: `published`, `draft`, `expired` or `terminated`, as of `meta.as_of_date`.
- `start_date`, `end_date`: from 2021 to 2029, outside the purchase window.
- `discount_pct`: 0 to 15, already applied to contract prices.

## catalog_items
Answers: what we can buy today, current catalog prices, quoted lead times, SKUs.
522 rows: the current offer per supplier.
- `catalog_item_id`, `supplier_id` and `category_id`.
- `contract_id`: nullable; the supplier's latest non-draft contract for the category, which may be
  expired or terminated.
- `sku`: a brand prefix plus a number, for example `BRA-46749`.
- `item_name`: in English, German or French. `item_description` has the format
  `"<name> | SKU <sku> | lead time <n> days"`.
- `unit_price`: current price in `currency`.
- `active`: boolean. Only items from active suppliers can be active.
- `created_at`: the date the item was added.
- `lead_time_days`: the supplier's quoted delivery time for the item, 2 to 21 days. A catalog
  promise, not a measured delivery time (those are `promised_days` and `actual_days` on cart lines).

## shopping_carts
Answers: purchase requests (carts) per site, department and status, how many were
completed, cancelled, rejected or are waiting for approval.
7,200 rows: one purchase request each.
- `cart_id`: a large integer starting at 2000000001.
- `description`: short free text, sometimes with a site or quarter, sometimes lower case.
- `plant_id` and `requester_department` (IT, Production, Logistics, Quality, HR, Facilities, R&D,
  Finance).
- `status`: `completed`, `cancelled`, `rejected` or `in_approval`.
- `created_at`: always a weekday. `in_approval` carts are from the last 10 days before
  `meta.as_of_date`.
- `completed_at`: set only for completed carts. It can fall after `data_to`, up to `as_of_date`.

## shopping_cart_items
Answers: line details of every cart, whatever its status; for spend use `v_completed_lines`.
17,160 rows: one line per item.
- `line_id` and `cart_id`.
- `supplier_id`: each line has its own supplier; about 1,300 carts mix suppliers.
- `category_id`: nullable (about 4% of lines). `contract_id`: nullable, the contract valid at
  purchase time if one was used.
- `item_description`: free text (see the glossary).
- `quantity`, `unit_price`, `total_value` (= `unit_price × quantity`, rounded to cents) and
  `currency`.
- `promised_days` and `actual_days`: delivery lead times in days. NULL unless the cart is completed
  and was created on or before 2026-09-15. Delay = `actual_days - promised_days`.
- `created_at`: equals the cart's `created_at`.

Prices rise about 3.5% per year, so compare unit prices within the same period.

## v_completed_lines
Answers: how much we spent or bought, per year, month, site, department, supplier,
category or item; delivery delays. The default for spend.
View: every line of a completed cart, already converted to EUR. The default source for spend and
volume questions. Columns: `line_id`, `cart_id`, `cart_created_at`, `completed_at`, `plant_id`,
`requester_department`, `supplier_id`, `category_id`, `contract_id`, `item_description`,
`quantity`, `unit_price`, `total_value`, `currency`, `unit_price_eur`, `total_value_eur`,
`promised_days`, `actual_days`.

## v_supplier_summary
Answers: all-time totals per supplier: spend, number of carts, last purchase,
average delay.
View: one row per supplier (all 96, LEFT JOIN): `supplier_id`, `name`, `country_code`, `status`,
`risk_rating`, `is_preferred`, `completed_carts`, `completed_lines`, `spend_eur`, `last_cart_date`
and `avg_delay_days`. Completed carts only, over all time; for a date range, query
`v_completed_lines` instead.
