-- Rebuild metrics for a requested date range, retaining full prior price history
-- for correct rolling calculations. Rerunnable and upserts revised observations.
-- NULL represents insufficient history, not zero volatility or volume.
CREATE OR REPLACE PROCEDURE DAEDALUS.MARKET_DATA.REFRESH_GPW_STOCK_METRICS(
    P_START_DATE DATE,
    P_END_DATE DATE
)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS OWNER
AS
$$
BEGIN
    IF (P_START_DATE IS NULL OR P_END_DATE IS NULL OR P_START_DATE > P_END_DATE) THEN
        RETURN 'Invalid date range';
    END IF;

    MERGE INTO DAEDALUS.MARKET_DATA.GPW_STOCK_METRICS_DAILY t
    USING (
        WITH canonical AS (
            SELECT p."SYMBOL" AS SYMBOL,
                   TO_DATE(p."DATE") AS TRADE_DATE,
                   p."CLOSE" AS CLOSE_PRICE,
                   p."HIGH" AS HIGH_PRICE,
                   p."LOW" AS LOW_PRICE,
                   p."VOLUME" AS VOLUME
            FROM DAEDALUS.MARKET_DATA.PRICES p
            WHERE p."DATE" IS NOT NULL
              AND TO_DATE(p."DATE") <= :P_END_DATE
              AND EXISTS (
                  SELECT 1 FROM DAEDALUS.MARKET_DATA.SECURITIES s
                  WHERE s."SYMBOL" = p."SYMBOL" AND s."VENUE" = 'GPW'
              )
            -- Protect metrics from legacy duplicate daily observations.
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY p."SYMBOL", TO_DATE(p."DATE")
                ORDER BY p."DATE" DESC
            ) = 1
        ),
        clean AS (
            SELECT * FROM canonical
            WHERE CLOSE_PRICE > 0
        ),
        lagged AS (
            SELECT *,
                   LAG(CLOSE_PRICE) OVER (PARTITION BY SYMBOL ORDER BY TRADE_DATE)
                        AS PREVIOUS_CLOSE
            FROM clean
        ),
        returns AS (
            SELECT *,
                   CASE WHEN PREVIOUS_CLOSE > 0
                        THEN LN(CLOSE_PRICE / PREVIOUS_CLOSE)
                   END AS LOG_RETURN,
                   CASE WHEN PREVIOUS_CLOSE > 0
                        THEN CLOSE_PRICE / PREVIOUS_CLOSE - 1
                   END AS SIMPLE_RETURN
            FROM lagged
        ),
        windows AS (
            SELECT *,
                   COUNT(VOLUME) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                   ) AS N_VOLUME_20D,
                   AVG(VOLUME) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                   ) AS AVG_VOLUME_20D,
                   COUNT(VOLUME) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                   ) AS N_VOLUME_60D,
                   AVG(VOLUME) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                   ) AS AVG_VOLUME_60D,
                   COUNT(VOLUME) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                   ) AS N_VOLUME_252D,
                   AVG(VOLUME) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                   ) AS AVG_VOLUME_252D,
                   COUNT(*) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                   ) AS N_CLOSE_20D,
                   AVG(CLOSE_PRICE) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                   ) AS AVG_CLOSE_20D,
                   COUNT(*) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                   ) AS N_CLOSE_60D,
                   AVG(CLOSE_PRICE) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                   ) AS AVG_CLOSE_60D,
                   COUNT(LOG_RETURN) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                   ) AS N_RETURNS_20D,
                   STDDEV_SAMP(LOG_RETURN) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                   ) AS STD_RETURNS_20D,
                   COUNT(LOG_RETURN) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                   ) AS N_RETURNS_252D,
                   STDDEV_SAMP(LOG_RETURN) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                   ) AS STD_RETURNS_252D,
                   AVG(ABS(LOG_RETURN)) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                   ) AS MEAN_ABS_RETURN_252D,
                   MAX(CASE WHEN HIGH_PRICE > 0 THEN HIGH_PRICE END) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       RANGE BETWEEN INTERVAL '364 days' PRECEDING AND CURRENT ROW
                   ) AS HIGH_52W_RAW,
                   MIN(CASE WHEN LOW_PRICE > 0 THEN LOW_PRICE END) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       RANGE BETWEEN INTERVAL '364 days' PRECEDING AND CURRENT ROW
                   ) AS LOW_52W_RAW,
                   COUNT(*) OVER (
                       PARTITION BY SYMBOL ORDER BY TRADE_DATE
                       RANGE BETWEEN INTERVAL '364 days' PRECEDING AND CURRENT ROW
                   ) AS N_OBS_52W
            FROM returns
        ),
        features AS (
            SELECT SYMBOL, TRADE_DATE, CLOSE_PRICE,
                   LOG_RETURN AS DAILY_LOG_RETURN,
                   SIMPLE_RETURN AS DAILY_SIMPLE_RETURN,
                   IFF(N_VOLUME_20D = 20, AVG_VOLUME_20D, NULL) AS VOLUME_AVG_20D,
                   IFF(N_VOLUME_60D = 60, AVG_VOLUME_60D, NULL) AS VOLUME_AVG_60D,
                   IFF(N_VOLUME_252D = 252, AVG_VOLUME_252D, NULL) AS VOLUME_AVG_252D,
                   IFF(N_CLOSE_20D = 20, AVG_CLOSE_20D, NULL) AS CLOSE_SMA_20D,
                   IFF(N_CLOSE_60D = 60, AVG_CLOSE_60D, NULL) AS CLOSE_SMA_60D,
                   IFF(N_VOLUME_20D = 20 AND AVG_VOLUME_20D > 0,
                       VOLUME / AVG_VOLUME_20D, NULL) AS VOLUME_RELATIVE_20D,
                   IFF(N_RETURNS_20D = 20, STD_RETURNS_20D, NULL) AS VOLATILITY_DAILY_20D,
                   IFF(N_RETURNS_20D = 20,
                       STD_RETURNS_20D * SQRT(252.0), NULL) AS VOLATILITY_ANNUAL_20D,
                   IFF(N_RETURNS_252D = 252,
                       STD_RETURNS_252D * SQRT(252.0), NULL) AS VOLATILITY_ANNUAL_252D,
                   IFF(N_RETURNS_252D = 252,
                       MEAN_ABS_RETURN_252D, NULL) AS MEAN_ABS_LOG_RETURN_252D,
                   HIGH_52W_RAW AS HIGH_52W,
                   LOW_52W_RAW AS LOW_52W,
                   IFF(HIGH_52W_RAW > LOW_52W_RAW,
                       (CLOSE_PRICE - LOW_52W_RAW) / (HIGH_52W_RAW - LOW_52W_RAW),
                       NULL) AS POSITION_52W,
                   N_OBS_52W AS OBSERVATIONS_52W,
                   (N_OBS_52W >= 200) AS COMPLETE_52W
            FROM windows
            WHERE TRADE_DATE BETWEEN :P_START_DATE AND :P_END_DATE
        )
        SELECT * FROM features
    ) s
    ON t.SYMBOL = s.SYMBOL AND t.TRADE_DATE = s.TRADE_DATE
    WHEN MATCHED THEN UPDATE SET
        t.CLOSE_PRICE = s.CLOSE_PRICE,
        t.DAILY_LOG_RETURN = s.DAILY_LOG_RETURN,
        t.DAILY_SIMPLE_RETURN = s.DAILY_SIMPLE_RETURN,
        t.VOLUME_AVG_20D = s.VOLUME_AVG_20D,
        t.VOLUME_AVG_60D = s.VOLUME_AVG_60D,
        t.VOLUME_AVG_252D = s.VOLUME_AVG_252D,
        t.CLOSE_SMA_20D = s.CLOSE_SMA_20D,
        t.CLOSE_SMA_60D = s.CLOSE_SMA_60D,
        t.VOLUME_RELATIVE_20D = s.VOLUME_RELATIVE_20D,
        t.VOLATILITY_DAILY_20D = s.VOLATILITY_DAILY_20D,
        t.VOLATILITY_ANNUAL_20D = s.VOLATILITY_ANNUAL_20D,
        t.VOLATILITY_ANNUAL_252D = s.VOLATILITY_ANNUAL_252D,
        t.MEAN_ABS_LOG_RETURN_252D = s.MEAN_ABS_LOG_RETURN_252D,
        t.HIGH_52W = s.HIGH_52W,
        t.LOW_52W = s.LOW_52W,
        t.POSITION_52W = s.POSITION_52W,
        t.OBSERVATIONS_52W = s.OBSERVATIONS_52W,
        t.COMPLETE_52W = s.COMPLETE_52W,
        t.CALC_VERSION = 1,
        t.CALCULATED_AT = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT (
        SYMBOL, TRADE_DATE, CLOSE_PRICE, DAILY_LOG_RETURN, DAILY_SIMPLE_RETURN,
        VOLUME_AVG_20D, VOLUME_AVG_60D, VOLUME_AVG_252D,
        CLOSE_SMA_20D, CLOSE_SMA_60D, VOLUME_RELATIVE_20D,
        VOLATILITY_DAILY_20D, VOLATILITY_ANNUAL_20D, VOLATILITY_ANNUAL_252D,
        MEAN_ABS_LOG_RETURN_252D, HIGH_52W, LOW_52W, POSITION_52W,
        OBSERVATIONS_52W, COMPLETE_52W, CALC_VERSION, CALCULATED_AT
    ) VALUES (
        s.SYMBOL, s.TRADE_DATE, s.CLOSE_PRICE, s.DAILY_LOG_RETURN, s.DAILY_SIMPLE_RETURN,
        s.VOLUME_AVG_20D, s.VOLUME_AVG_60D, s.VOLUME_AVG_252D,
        s.CLOSE_SMA_20D, s.CLOSE_SMA_60D, s.VOLUME_RELATIVE_20D,
        s.VOLATILITY_DAILY_20D, s.VOLATILITY_ANNUAL_20D, s.VOLATILITY_ANNUAL_252D,
        s.MEAN_ABS_LOG_RETURN_252D, s.HIGH_52W, s.LOW_52W, s.POSITION_52W,
        s.OBSERVATIONS_52W, s.COMPLETE_52W, 1, CURRENT_TIMESTAMP()
    );

    RETURN 'GPW metrics refreshed';
END;
$$;
