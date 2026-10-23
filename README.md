# Ikaros Snowflake schema smoke test

Copy `run/check_snowflake_tables.py` into the Ikaros `run/` directory.

This script expects the earlier database-backends overlay to be installed (`database/snowflake_storage.py`, `database/repositories/factory.py`, and `config/database.yaml`).

1. Set `database.driver: snowflake` in `config/database.yaml`.
2. Install `python -m pip install -r requirements-snowflake.txt`.
3. Set environment variables `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD` (or configure your preferred authenticator in the connector).
4. From repository root run `python -m run.check_snowflake_tables`.

The repository constructor issues `CREATE SCHEMA IF NOT EXISTS` and eight `CREATE TABLE IF NOT EXISTS` commands in the configured database/schema, then the script checks table names. Running it twice should be harmless; it will not truncate or drop tables and does not insert data. It *does* create persistent schema objects if missing. Do not use production schema names until you approve those names and access policies.

The Snowflake role needs USAGE on database, CREATE SCHEMA on database if the schema does not exist, USAGE on schema and CREATE TABLE on schema. If a role has ownership or equivalent grants these may already be covered. The warehouse is configured for the connection; a CREATE TABLE DDL check does not need a costly bulk ingest.

This is an integration smoke test, not a local mock; the assistant cannot execute it against your Snowflake instance without an authorized connection.
