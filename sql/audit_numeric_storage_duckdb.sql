-- Read-only. Run while connected to ikaros.duckdb in DBeaver.
-- Close DBeaver before running the migration script.
SELECT
  table_schema, table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND upper(data_type) IN (
    'FLOAT', 'DOUBLE', 'REAL', 'FLOAT4', 'FLOAT8', 'DOUBLE PRECISION'
  )
ORDER BY table_name, ordinal_position;

-- Inspect all persisted numeric storage types in one result:
SELECT
  table_schema, table_name, column_name, data_type,
  numeric_precision, numeric_scale
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND (
    upper(data_type) IN ('FLOAT', 'DOUBLE', 'REAL', 'FLOAT4', 'FLOAT8', 'DOUBLE PRECISION')
    OR upper(data_type) LIKE 'DECIMAL%'
    OR upper(data_type) LIKE 'NUMERIC%'
    OR upper(data_type) IN ('BIGINT', 'INTEGER', 'HUGEINT', 'SMALLINT')
  )
ORDER BY table_name, ordinal_position;
