# Example queries

Questions with a query that answers them correctly under the glossary rules. Each `##` section is one
example; semantic search gives the SQL step the closest ones. No example repeats a question of the
evaluation cases (a test checks), and every example runs on the database (another test checks).

## top_suppliers_by_spend_in_a_year
Question: Which ten suppliers did we spend the most with in 2024?

```sql
SELECT s.name, ROUND(SUM(l.total_value_eur), 2) AS spend_eur
FROM v_completed_lines AS l
JOIN suppliers AS s ON s.supplier_id = l.supplier_id
WHERE l.completed_at >= DATE '2024-01-01' AND l.completed_at < DATE '2025-01-01'
GROUP BY s.name
ORDER BY spend_eur DESC
LIMIT 10
```

## spend_per_category_group
Question: How is our spend split across the four category groups?

```sql
SELECT g.name AS category_group, ROUND(SUM(l.total_value_eur), 2) AS spend_eur
FROM v_completed_lines AS l
JOIN categories AS c ON c.category_id = l.category_id
JOIN categories AS g ON g.category_id = c.parent_id
GROUP BY g.name
ORDER BY spend_eur DESC
```

## spend_with_one_brand
Question: What did we spend with Dovtor each year?

```sql
SELECT EXTRACT(YEAR FROM l.completed_at)::int AS year, ROUND(SUM(l.total_value_eur), 2) AS spend_eur
FROM v_completed_lines AS l
WHERE l.supplier_id IN (SELECT supplier_id FROM supplier_aliases WHERE alias ILIKE 'dovtor%')
GROUP BY year
ORDER BY year
```

## late_delivery_rate_by_supplier
Question: Which suppliers deliver late most often?

```sql
SELECT s.name,
       COUNT(*) AS delivered_lines,
       ROUND((100.0 * COUNT(*) FILTER (WHERE l.actual_days > l.promised_days) / COUNT(*))::numeric, 1)
         AS late_pct,
       ROUND(AVG(l.actual_days - l.promised_days)::numeric, 1) AS avg_delay_days
FROM v_completed_lines AS l
JOIN suppliers AS s ON s.supplier_id = l.supplier_id
WHERE l.actual_days IS NOT NULL
GROUP BY s.name
HAVING COUNT(*) >= 20
ORDER BY late_pct DESC
LIMIT 10
```

## contracts_expiring_soon
Question: Which valid contracts end in the next six months?

```sql
SELECT c.contract_id, s.name AS supplier, cat.name AS category, c.end_date
FROM contracts AS c
JOIN suppliers AS s ON s.supplier_id = c.supplier_id
JOIN categories AS cat ON cat.category_id = c.category_id
WHERE c.status = 'published'
  AND c.end_date < (SELECT value::date FROM meta WHERE key = 'as_of_date') + INTERVAL '6 months'
ORDER BY c.end_date
```

## fastest_catalog_items_in_a_category
Question: Which active catalog items for hand tools have the shortest lead time?

```sql
SELECT ci.item_name, s.name AS supplier, ci.lead_time_days, ci.unit_price, ci.currency
FROM catalog_items AS ci
JOIN suppliers AS s ON s.supplier_id = ci.supplier_id
JOIN categories AS c ON c.category_id = ci.category_id
WHERE ci.active AND c.name = 'Hand Tools'
ORDER BY ci.lead_time_days, ci.unit_price
LIMIT 10
```

## carts_by_status_per_plant
Question: How many carts did each site create in 2025, by status?

```sql
SELECT p.name AS plant, p.city, sc.status, COUNT(*) AS carts
FROM shopping_carts AS sc
JOIN plants AS p ON p.plant_id = sc.plant_id
WHERE sc.created_at >= DATE '2025-01-01' AND sc.created_at < DATE '2026-01-01'
GROUP BY p.name, p.city, sc.status
ORDER BY p.name, sc.status
```

## item_keywords_in_three_languages
Question: How many safety shoes did we buy in 2025?

```sql
SELECT SUM(l.quantity) AS pairs, ROUND(SUM(l.total_value_eur), 2) AS spend_eur
FROM v_completed_lines AS l
WHERE l.completed_at >= DATE '2025-01-01' AND l.completed_at < DATE '2026-01-01'
  AND (l.item_description ILIKE '%safety shoe%'
       OR l.item_description ILIKE '%sicherheitsschuh%'
       OR l.item_description ILIKE '%chaussures de securite%')
```

## spend_by_original_currency
Question: How much did we spend in each billing currency, in the original currency and in EUR?

```sql
SELECT l.currency, ROUND(SUM(l.total_value), 2) AS spend_original,
       ROUND(SUM(l.total_value_eur), 2) AS spend_eur
FROM v_completed_lines AS l
GROUP BY l.currency
ORDER BY spend_eur DESC
```

## risky_suppliers_with_spend
Question: Which blocked or high-risk suppliers did we still buy from?

```sql
SELECT name, status, risk_rating, spend_eur, last_cart_date
FROM v_supplier_summary
WHERE (status = 'blocked' OR risk_rating = 'high') AND completed_carts > 0
ORDER BY spend_eur DESC
```

## monthly_spend_trend
Question: What was our spend per month in 2025?

```sql
SELECT TO_CHAR(DATE_TRUNC('month', l.completed_at), 'YYYY-MM') AS month,
       ROUND(SUM(l.total_value_eur), 2) AS spend_eur
FROM v_completed_lines AS l
WHERE l.completed_at >= DATE '2025-01-01' AND l.completed_at < DATE '2026-01-01'
GROUP BY month
ORDER BY month
```

## share_of_spend_under_contract
Question: What share of our spend went through a contract, per category?

```sql
SELECT COALESCE(c.name, '(no category)') AS category,
       ROUND(SUM(l.total_value_eur), 2) AS spend_eur,
       ROUND((100 * SUM(l.total_value_eur) FILTER (WHERE l.contract_id IS NOT NULL)
              / SUM(l.total_value_eur))::numeric, 1) AS contract_share_pct
FROM v_completed_lines AS l
LEFT JOIN categories AS c ON c.category_id = l.category_id
GROUP BY c.name
ORDER BY spend_eur DESC
```
