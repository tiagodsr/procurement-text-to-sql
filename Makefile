# Developer commands. The professor's path is only `docker compose up`; these rebuild artefacts.
PG_IMAGE        := postgres:16.15-bookworm
BUILD_CONTAINER := pts-build
BUILD_PORT      := 55433
BUILD_DB        := procurement_build
BUILD_DSN       := postgresql://postgres:build@127.0.0.1:$(BUILD_PORT)/$(BUILD_DB)
DEV_COMPOSE     := docker compose -f docker-compose.yml -f compose.dev.yml

.PHONY: build-db build-db-stop parity dump test test-integration db-dev

build-db:  ## throwaway PostgreSQL container with freshly generated data
	-@docker rm -f $(BUILD_CONTAINER) >/dev/null 2>&1
	docker run -d --name $(BUILD_CONTAINER) -e POSTGRES_PASSWORD=build -e POSTGRES_DB=$(BUILD_DB) \
		-p 127.0.0.1:$(BUILD_PORT):5432 $(PG_IMAGE) >/dev/null
	@until docker exec $(BUILD_CONTAINER) pg_isready -q -h 127.0.0.1 -U postgres -d $(BUILD_DB); do sleep 1; done
	uv run python scripts/build_db.py --dsn $(BUILD_DSN)

build-db-stop:
	-docker rm -f $(BUILD_CONTAINER)

parity: build-db  ## one-off: compare the PostgreSQL build with .local/sqlite_reference.db
	uv run python scripts/parity_check.py --sqlite .local/sqlite_reference.db --pg $(BUILD_DSN)
