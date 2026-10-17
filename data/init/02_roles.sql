-- Read-only application role. Runs once, after 01_dump.sql.gz, on the first start of the db
-- container (docker-entrypoint-initdb.d, as the admin role). The application always connects as
-- app_ro; only these init scripts use the admin role.
--
-- The password is a documented local default: the base compose file never publishes the database
-- port, and the role can only read.

CREATE ROLE app_ro LOGIN PASSWORD 'app_ro_local'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT
    CONNECTION LIMIT 20;

-- Nothing by default for PUBLIC: no connect, no temporary tables, no objects in schema public.
REVOKE ALL ON DATABASE procurement FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Exactly what the application needs: read every table and view.
GRANT CONNECT ON DATABASE procurement TO app_ro;
GRANT USAGE ON SCHEMA public TO app_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_ro;

-- Session defaults. A session can turn the read-only default off, so the privileges above are
-- the real barrier; the SQL guard in the application is a separate, independent layer.
ALTER ROLE app_ro SET default_transaction_read_only = on;
ALTER ROLE app_ro SET statement_timeout = '5s';
ALTER ROLE app_ro SET idle_in_transaction_session_timeout = '30s';
