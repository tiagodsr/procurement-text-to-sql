"""Spec for assistant.prompts and the knowledge files the prompts are built from."""

import re

import pytest

from assistant import prompts

BRAND_ROW = re.compile(r"^\s*\|\s*([A-Z][a-z]+)\s*\|\s*(.+?)\s*\|\s*$")


def sister_brands() -> dict[str, set[str]]:
    """{brand: {legal entity name}} from the table in glossary rule 6."""
    brands = {}
    for line in prompts.knowledge("glossary.md").splitlines():
        if (match := BRAND_ROW.match(line)) and match[1] != "Brand":
            entities = (re.sub(r"\s*\([A-Z]{2}\)$", "", e.strip()) for e in match[2].split(";"))
            brands[match[1]] = set(entities)
    return brands


def sql_prompt() -> str:
    return prompts.sql(tables="## suppliers\n96 rows.", examples="## an_example\nSELECT 1")


@pytest.mark.parametrize("build", [prompts.intake, sql_prompt, prompts.explain])
def test_every_prompt_is_filled_and_carries_the_glossary(build):
    text = build()
    assert "{{" not in text
    assert prompts.knowledge("glossary.md") in text


def test_the_sql_prompt_carries_the_docs_it_is_given():
    text = sql_prompt()
    assert "## suppliers\n96 rows." in text
    assert "## an_example\nSELECT 1" in text


def test_an_unfilled_placeholder_is_an_error():
    with pytest.raises(ValueError, match="placeholder"):
        prompts.render("sql", glossary="rules only")


def test_the_glossary_lists_six_sister_brands_of_two_entities_each():
    brands = sister_brands()
    assert len(brands) == 6
    assert all(len(entities) == 2 for entities in brands.values())
    assert brands["Velria"] == {"Velria Tools SAS", "Velria America Inc."}
