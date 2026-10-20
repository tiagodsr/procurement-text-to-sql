# Developer commands. The professor's path is only `docker compose up`; these rebuild artefacts.
PG_IMAGE        := postgres:16.15-bookworm
BUILD_CONTAINER := pts-build
BUILD_PORT      := 55433
BUILD_DB        := procurement_build
BUILD_DSN       := postgresql://postgres:build@127.0.0.1:$(BUILD_PORT)/$(BUILD_DB)
DEV_COMPOSE     := docker compose -f docker-compose.yml -f compose.dev.yml

.PHONY: build-db build-db-stop dump test test-integration db-dev

build-db:  ## throwaway PostgreSQL container with freshly generated data
	-@docker rm -f $(BUILD_CONTAINER) >/dev/null 2>&1
	docker run -d --name $(BUILD_CONTAINER) -e POSTGRES_PASSWORD=build -e POSTGRES_DB=$(BUILD_DB) \
		-p 127.0.0.1:$(BUILD_PORT):5432 $(PG_IMAGE) >/dev/null
	@until docker exec $(BUILD_CONTAINER) pg_isready -q -h 127.0.0.1 -U postgres -d $(BUILD_DB); do sleep 1; done
	uv run python scripts/build_db.py --dsn $(BUILD_DSN)

build-db-stop:
	-docker rm -f $(BUILD_CONTAINER)

# pg_dump and gzip run inside the container, so the bytes do not depend on the host's tools.
# A fixed --restrict-key keeps the dump byte-identical between runs (the default key is random).
dump: build-db  ## regenerate data/init/01_dump.sql.gz
	docker exec $(BUILD_CONTAINER) sh -c 'pg_dump -U postgres -d $(BUILD_DB) --no-owner \
		--no-privileges --restrict-key=procurementv1 | gzip -n -9' > data/init/01_dump.sql.gz
	$(MAKE) build-db-stop

test:  ## unit tests (no database, no API key)
	uv run pytest -q -m "not integration"

db-dev:  ## compose db with the dev-only loopback port 127.0.0.1:55432
	$(DEV_COMPOSE) up -d --wait db

test-integration: db-dev  ## integration tests against the compose db, as app_ro
	uv run pytest -q -m integration
