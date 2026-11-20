# P0 / P1 implementation status

Basis: `feature/clean_ni`, `3b6d1dce6fc473318fc67f37fdf5ada0e2699006`.

| Target | Status in this kit | Gaps |
|---|---|---|
| Candidate-only daily `run.update_all` | Already in branch | Live execution not verified |
| Prevent accidental legacy price-table writes via repository factory | Implemented; SQL and bulk routes guarded | Direct raw DB connections / separate credentials are not gated |
| Kafka FX/GPW inbound candidate-only | Implemented | Kafka E2E with real broker not verified; corporate actions require candidate schema extension |
| Retire old direct GPW/FX standalone entrypoints | `basic_ingest`, `ingest_fx_rates` fail closed | Other direct-write entrypoints fail closed via guard but aren't re-routed |
| Move `run.ingest_index_prices` SQL to database/ | Implemented | Its old import-to-final mode intentionally blocked |
| Move `run.refresh_fw20_prices` SQL to database/ | Implemented | Its old direct-to-final mode intentionally blocked |
| Move SQL from other 22 audited modules | Query-extraction builder provided | Generated replacements must be reviewed; AST audit reports manual blockers |
| Top-level `sql/` scripts | Copy command included in builder | Original paths remain for compatibility, so `sql/` is not yet empty |
| Backend portability | Existing Snowflake/DuckDB repository factory preserved | Moving SQL does not translate Snowflake-specific SQL to DuckDB |
| CI-enforced no-SQL-outside-database | Architecture auditor included | Strict mode cannot be enabled until remaining modules are migrated |

## P0 remaining safeguards

- Central promotion roles in Snowflake: candidate writer may write **only**
  candidate/reference tables; promoter role may write authoritative tables.
- Migrate/disable all legacy direct-final commands, including older NBP/FX/rates
  and CSV-based index/derivative utilities. Until migrated they fail closed
  when using the repository factory.
- Route any non-factory raw connections and external SQL clients under an
  authorization policy.
- Add broker-backed Kafka tests, end-to-end Snowflake tests and complete
  DuckDB back-end tests before scheduling recurring production writes.

## P1 remaining work

- Execute `tools/build_db_query_refactor.py` against the complete pinned
  checkout and inspect its issue manifest. Resolve flagged manual cases with
  explicit database repository methods.
- Update SQL-file references and then relocate original top-level `sql/` files;
  copying them alone does not satisfy the strict source-tree boundary.
- Replace direct Snowflake type imports outside `database/` with neutral
  protocols/interfaces and add backend-appropriate SQL implementations.
- Make `tools/audit_db_boundary.py --strict` pass, then enforce it under pytest/CI.
