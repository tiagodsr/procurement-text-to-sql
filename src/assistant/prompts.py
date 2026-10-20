"""The `instructions` of each LLM step: prompts/*.md filled with the knowledge files. Only trusted
text comes in here; the question, the clarification, database errors and rows never do."""

from __future__ import annotations

from functools import cache

from assistant.config import REPO_ROOT

PROMPTS_DIR = REPO_ROOT / "prompts"
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"


@cache
def knowledge(name: str) -> str:
    return (KNOWLEDGE_DIR / name).read_text(encoding="utf-8").strip()


def render(name: str, **parts: str) -> str:
    """prompts/<name>.md with each {{part}} replaced; a placeholder left unfilled is an error."""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in parts.items():
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text:
        raise ValueError(f"prompts/{name}.md has a placeholder that was not filled")
    return text


def intake() -> str:
    return render("intake", glossary=knowledge("glossary.md"))


def sql() -> str:
    return render("sql", glossary=knowledge("glossary.md"), tables=knowledge("tables.md"))


def explain() -> str:
    return render("explain", glossary=knowledge("glossary.md"))
