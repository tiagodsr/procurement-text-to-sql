"""Spec for assistant.retrieve: the documents, the committed index, cosine ranking and recall."""

import json
import math

import pytest

from assistant.retrieve import (
    CASES_PATH,
    Index,
    IndexOutdated,
    cosine,
    load_docs,
    load_index,
    recall,
    sections,
    write_index,
)

DOCS = load_docs()


def examples() -> dict[str, str]:
    """{example id: its question} from knowledge/examples.md."""
    return {
        f"example:{heading}": text.split("Question:", 1)[1].split("\n", 1)[0].strip()
        for heading, text in sections("examples.md")
    }


def test_one_doc_per_table_section_and_per_example():
    ids = [doc.id for doc in DOCS]
    assert len(ids) == len(set(ids))
    assert sum(doc.doc_type == "table" for doc in DOCS) == 12
    assert sum(doc.doc_type == "example" for doc in DOCS) == len(examples()) >= 10
    suppliers = next(doc for doc in DOCS if doc.id == "table:suppliers")
    assert suppliers.source == "knowledge/tables.md#suppliers"
    assert suppliers.text.startswith("## suppliers\n")
    assert "## supplier_aliases" not in suppliers.text


def test_the_committed_index_has_one_vector_per_doc():
    index = load_index("text-embedding-3-small")
    assert [doc.id for doc in index.docs] == [doc.id for doc in DOCS]
    assert len({len(vector) for vector in index.vectors}) == 1


def test_an_index_that_does_not_match_the_docs_or_the_model_is_refused(tmp_path):
    path = tmp_path / "index.json"
    write_index(Index("m", DOCS[:-1], [[1.0, 0.0]] * (len(DOCS) - 1)), path)
    with pytest.raises(IndexOutdated, match="make index"):
        load_index("m", path)
    write_index(Index("m", DOCS, [[1.0, 0.0]] * len(DOCS)), path)
    assert load_index("m", path).vectors[0] == [1.0, 0.0]
    with pytest.raises(IndexOutdated, match="'m'"):
        load_index("other-model", path)


def test_cosine():
    assert cosine([1, 0], [2, 0]) == pytest.approx(1)
    assert cosine([1, 0], [0, 3]) == pytest.approx(0)
    assert cosine([1, 1], [1, 0]) == pytest.approx(1 / math.sqrt(2))
    with pytest.raises(ValueError, match="zero vector"):
        cosine([0, 0], [1, 0])


def test_search_ranks_one_doc_type_best_first():
    vectors = [[1.0, float(j)] for j in range(len(DOCS))]
    found = Index("m", DOCS, vectors).search([1.0, 0.0], "table", 3)
    tables = [doc.id for doc in DOCS if doc.doc_type == "table"]
    assert [doc.id for doc in found] == tables[:3]
    assert found[0].score > found[1].score > found[2].score


def test_recall_is_the_share_of_expected_docs_in_the_top_k():
    vectors = [[1.0, float(j)] for j in range(len(DOCS))]
    index = Index("m", DOCS, vectors)
    first, second, last = (d.id for d in (DOCS[0], DOCS[1], DOCS[11]))
    cases = [{"expected_docs": [first, second]}, {"expected_docs": [first, last]}]
    assert recall(index, cases, [[1.0, 0.0]] * 2, k=2) == [1.0, 0.5]


def test_evaluation_questions_and_examples_never_overlap():
    def norm(text: str) -> str:
        return " ".join(text.casefold().split())

    questions = {norm(case["question"]) for case in json.loads(CASES_PATH.read_text("utf-8"))}
    assert questions.isdisjoint(norm(q) for q in examples().values())


def test_expected_docs_name_real_table_docs():
    tables = {doc.id for doc in DOCS if doc.doc_type == "table"}
    for case in json.loads(CASES_PATH.read_text("utf-8")):
        assert set(case["expected_docs"]) <= tables, case["id"]
