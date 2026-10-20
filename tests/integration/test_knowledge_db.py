"""The knowledge files agree with the database: the sister brands of glossary rule 6, and every
example query passes the guard and returns rows."""

import re

import pytest

from assistant.executor import execute
from assistant.guard import GuardedSQL, check
from assistant.retrieve import sections
from assistant.schemas import SqlResult
from tests.unit.test_prompts import sister_brands

pytestmark = pytest.mark.integration

EXAMPLES = {
    heading: re.search(r"```sql\n(.+?)```", text, re.DOTALL)[1]
    for heading, text in sections("examples.md")
}


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


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_every_example_query_passes_the_guard_and_returns_rows(name, database_url):
    guarded = check(EXAMPLES[name])
    assert isinstance(guarded, GuardedSQL), guarded
    result = execute(guarded, database_url=database_url)
    assert isinstance(result, SqlResult), result
    assert not result.empty
