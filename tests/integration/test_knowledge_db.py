"""The knowledge files agree with the database: the sister brands of glossary rule 6."""

import pytest

from tests.unit.test_prompts import sister_brands

pytestmark = pytest.mark.integration


def test_the_glossary_brand_list_matches_the_aliases(ro):
    rows = ro.execute(
        """
        SELECT a.alias, s.name
        FROM supplier_aliases AS a JOIN suppliers AS s USING (supplier_id)
        WHERE a.alias IN (
            SELECT alias FROM supplier_aliases GROUP BY alias HAVING COUNT(*) > 1
        )
        """
    ).fetchall()
    in_db: dict[str, set[str]] = {}
    for alias, name in rows:
        in_db.setdefault(alias, set()).add(name)
    assert sister_brands() == in_db
