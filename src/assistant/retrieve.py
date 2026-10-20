"""Semantic search over the knowledge: one document per table section (tables.md) and per example
query (examples.md), vectors committed in knowledge/index.json, plain cosine similarity.

    make index    # re-embed every document after editing tables.md or examples.md (API key)
    make recall   # recall@3 of the table docs on the development questions (API key)
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from assistant.config import REPO_ROOT, load_pricing, load_settings
from assistant.llm import embed, make_client
from assistant.prompts import KNOWLEDGE_DIR, knowledge
from assistant.schemas import RetrievedDoc
from assistant.trace import Tracer

INDEX_PATH = KNOWLEDGE_DIR / "index.json"
CASES_PATH = REPO_ROOT / "evals" / "cases.json"
TOP_TABLES = 5  # table docs given to the SQL step
TOP_EXAMPLES = 3  # example queries given to the SQL step
RECALL_AT = 3

DocType = Literal["table", "example"]


@dataclass(frozen=True)
class Doc:
    id: str
    doc_type: DocType
    source: str
    text: str


class IndexOutdated(RuntimeError):
    """knowledge/index.json does not match the documents or the model: run `make index`."""


def sections(name: str) -> list[tuple[str, str]]:
    """(heading, text from the heading on) for each `## ` section of a knowledge file."""
    parts = re.split(r"^## ", knowledge(name), flags=re.MULTILINE)[1:]
    return [(part.split("\n", 1)[0].strip(), f"## {part.strip()}") for part in parts]


def load_docs() -> list[Doc]:
    return [
        Doc(f"{doc_type}:{heading}", doc_type, f"knowledge/{name}#{heading}", text)
        for doc_type, name in (("table", "tables.md"), ("example", "examples.md"))
        for heading, text in sections(name)
    ]


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if not norms:
        raise ValueError("cosine similarity is undefined for a zero vector")
    return sum(a * b for a, b in zip(left, right, strict=True)) / norms


@dataclass(frozen=True)
class Index:
    model: str
    docs: list[Doc]
    vectors: list[list[float]]

    def search(self, vector: Sequence[float], doc_type: DocType, k: int) -> list[RetrievedDoc]:
        """The k documents of this type closest to the vector, best first."""
        scored = [
            RetrievedDoc(
                id=doc.id,
                doc_type=doc.doc_type,
                score=round(cosine(vector, v), 4),
                source=doc.source,
            )
            for doc, v in zip(self.docs, self.vectors, strict=True)
            if doc.doc_type == doc_type
        ]
        return sorted(scored, key=lambda d: d.score, reverse=True)[:k]

    def text(self, doc_id: str) -> str:
        return next(doc.text for doc in self.docs if doc.id == doc_id)


def load_index(model: str, path: Path = INDEX_PATH) -> Index:
    """The committed vectors, checked to be one per document and from `model`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    docs, vectors = load_docs(), data["vectors"]
    if sorted(vectors) != sorted(doc.id for doc in docs):
        raise IndexOutdated(f"{path.name} does not hold one vector per document: run `make index`")
    if data["model"] != model:
        raise IndexOutdated(f"{path.name} was built with {data['model']!r}, not {model!r}")
    return Index(model=model, docs=docs, vectors=[vectors[doc.id] for doc in docs])


def write_index(index: Index, path: Path = INDEX_PATH) -> None:
    """One document per line, so a rebuild shows which documents changed."""
    rows = [
        f"  {json.dumps(doc.id)}: {json.dumps([round(x, 6) for x in vector])}"
        for doc, vector in zip(index.docs, index.vectors, strict=True)
    ]
    body = ",\n".join(rows)
    path.write_text(f'{{"model": {json.dumps(index.model)}, "vectors": {{\n{body}\n}}}}\n', "utf-8")


def recall(index: Index, cases: list[dict], vectors: list[list[float]], k: int) -> list[float]:
    """Share of each case's expected table docs found among its k closest table docs."""
    found = []
    for case, vector in zip(cases, vectors, strict=True):
        top = {doc.id for doc in index.search(vector, "table", k)}
        found.append(len(top & set(case["expected_docs"])) / len(case["expected_docs"]))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["build", "recall"])
    command = parser.parse_args().command
    settings = load_settings()
    client, pricing = make_client(settings), load_pricing(settings.pricing_path)
    tracer = Tracer(settings.trace_dir)
    options = {"model": settings.embedding_model, "pricing": pricing, "tracer": tracer}
    if command == "build":
        docs = load_docs()
        vectors = embed(client, [doc.text for doc in docs], **options)
        write_index(Index(settings.embedding_model, docs, vectors))
        print(f"{len(docs)} documents embedded into {INDEX_PATH.relative_to(REPO_ROOT)}")
    else:
        index = load_index(settings.embedding_model)
        cases = [c for c in json.loads(CASES_PATH.read_text("utf-8")) if c["expected_docs"]]
        vectors = embed(client, [c["question"] for c in cases], **options)
        for k in sorted({RECALL_AT, TOP_TABLES}):
            scores = recall(index, cases, vectors, k)
            misses = [c["id"] for c, s in zip(cases, scores, strict=True) if s < 1]
            print(f"recall@{k}: {sum(scores) / len(scores):.2f} over {len(cases)} questions;"
                  f" incomplete: {', '.join(misses) or 'none'}")  # fmt: skip
    print(f"cost: ${sum(e.cost_usd for e in tracer.events):.6f}")


if __name__ == "__main__":
    main()
