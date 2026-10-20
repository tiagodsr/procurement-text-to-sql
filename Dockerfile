# One image for the api and the ui: they share the package and its dependencies.
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first, so editing the code does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev
# What the package reads at run time (REPO_ROOT is /app).
COPY config ./config
COPY knowledge ./knowledge
COPY prompts ./prompts
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "assistant.api:app", "--host", "0.0.0.0", "--port", "8000"]
