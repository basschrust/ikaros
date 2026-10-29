-- OPTIONAL: enable only after the procedure has been tested and monitored.
-- Schedule 19:30 Warsaw time daily (Snowflake's IANA timezone handling).
-- Serverless task: Snowflake bills compute for task executions.
CREATE OR REPLACE TASK DAEDALUS.MARKET_DATA.TASK_REFRESH_GPW_STOCK_METRICS
    SCHEDULE = 'USING CRON 30 19 * * * Europe/Warsaw'
    USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'XSMALL'
    AS
    CALL DAEDALUS.MARKET_DATA.REFRESH_GPW_STOCK_METRICS(
        DATEADD(day, -10, CURRENT_DATE())::DATE,
        CURRENT_DATE()
    );

-- Initially suspended after creation. Run manually then, if satisfied:
-- ALTER TASK DAEDALUS.MARKET_DATA.TASK_REFRESH_GPW_STOCK_METRICS RESUME;
-- To stop: ALTER TASK DAEDALUS.MARKET_DATA.TASK_REFRESH_GPW_STOCK_METRICS SUSPEND;
