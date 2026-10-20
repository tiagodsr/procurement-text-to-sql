# Rebuild the committed data dump. Everyday commands are plain `docker compose` and `uv run`.
PG_IMAGE        := postgres:16.15-bookworm
BUILD_CONTAINER := pts-build
BUILD_PORT      := 55433
BUILD_DB        := procurement_build
BUILD_DSN       := postgresql://postgres:build@127.0.0.1:$(BUILD_PORT)/$(BUILD_DB)

.PHONY: dump

# A throwaway container runs the seeded generator, then pg_dump and gzip inside the container, so
# the bytes do not depend on the host's tools. A fixed --restrict-key keeps the dump identical.
dump:  ## regenerate data/init/01_dump.sql.gz
	-@docker rm -f $(BUILD_CONTAINER) >/dev/null 2>&1
	docker run -d --name $(BUILD_CONTAINER) -e POSTGRES_PASSWORD=build -e POSTGRES_DB=$(BUILD_DB) \
		-p 127.0.0.1:$(BUILD_PORT):5432 $(PG_IMAGE) >/dev/null
	@until docker exec $(BUILD_CONTAINER) pg_isready -q -h 127.0.0.1 -U postgres -d $(BUILD_DB); do sleep 1; done
	uv run python scripts/build_db.py --dsn $(BUILD_DSN)
	docker exec $(BUILD_CONTAINER) sh -c 'pg_dump -U postgres -d $(BUILD_DB) --no-owner \
		--no-privileges --restrict-key=procurementv1 | gzip -n -9' > data/init/01_dump.sql.gz
	docker rm -f $(BUILD_CONTAINER)
