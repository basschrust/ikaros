-- Read-only Snowsight checks. Does not modify any stored observations.
USE DATABASE DAEDALUS;
USE SCHEMA MARKET_DATA;

-- Every missing WIG20 index session in the last 45 calendar days, as of
-- the last fully completed GPW day (replace the date when replaying).
-- The explicit November holiday exclusion is correct for 2026 only.
-- For other periods, use the officially maintained GPW calendar.
WITH calendar_days AS (
    SELECT DATEADD(
        DAY,
        -(ROW_NUMBER() OVER (ORDER BY SEQ4()) - 1),
        TO_DATE('2026-11-18')
    ) AS trade_date
    FROM TABLE(GENERATOR(ROWCOUNT => 45))
), gpw_sessions AS (
    SELECT trade_date
    FROM calendar_days
    WHERE DAYOFWEEKISO(trade_date) <= 5
      AND trade_date NOT IN (TO_DATE('2026-11-11'))
)
SELECT g.trade_date AS MISSING_WIG20_DATE
FROM gpw_sessions g
LEFT JOIN INDEX_PRICES p
    ON p.INDEX_SYMBOL = 'WIG20' AND TO_DATE(p.DATE) = g.trade_date
WHERE p.INDEX_SYMBOL IS NULL
ORDER BY g.trade_date;

-- Inspect the actual index price rows and provenance around the gap.
SELECT p.INDEX_SYMBOL,
       TO_DATE(p.DATE) AS DAY,
       p.OPEN,
       p.HIGH,
       p.LOW,
       p.CLOSE,
       p.SOURCE,
       p.PROVIDER_ID,
       p.CANDIDATE_ID,
       p.SELECTION_REASON
FROM INDEX_PRICES p
WHERE p.INDEX_SYMBOL='WIG20'
  AND TO_DATE(p.DATE) BETWEEN '2026-11-09' AND '2026-11-18'
ORDER BY DAY;

-- Read-back from the candidate evidence. This shows whether a single
-- provider was selected and whether any candidate was rejected.
SELECT c.INDEX_SYMBOL,
       c.DATE,
       p.PROVIDER_CODE,
       c.VALIDATION_STATUS,
       c.VALIDATION_REASON,
       c.OPEN,
       c.HIGH,
       c.LOW,
       c.CLOSE
FROM INDEX_PRICE_CANDIDATES c
JOIN PROVIDERS p ON p.PROVIDER_ID = c.PROVIDER_ID
WHERE c.INDEX_SYMBOL = 'WIG20'
  AND c.DATE BETWEEN '2026-11-09' AND '2026-11-18'
ORDER BY c.DATE, p.PROVIDER_CODE;

-- The next three queries audit whether existing authoritative datasets
-- have candidate provenance. Historical rows preceding the deployment
-- legitimately have NULL CANDIDATE_ID: this is an audit, not a failure rule.
SELECT 'GPW-EQUITIES' AS DATASET,
       COUNT(*) AS PRICE_ROWS,
       COUNT_IF(p.CANDIDATE_ID IS NOT NULL) AS LINKED_CANDIDATES
FROM PRICES p
JOIN SECURITIES s ON s.ASSET_ID = p.ASSET_ID
WHERE s.VENUE = 'GPW' AND TO_DATE(p.DATE) >= '2026-11-01';

SELECT 'WIG20-INDEX' AS DATASET,
       COUNT(*) AS PRICE_ROWS,
       COUNT_IF(CANDIDATE_ID IS NOT NULL) AS LINKED_CANDIDATES
FROM INDEX_PRICES
WHERE INDEX_SYMBOL = 'WIG20' AND TO_DATE(DATE) >= '2026-11-01';

SELECT 'GPW-DERIVATIVES' AS DATASET,
       COUNT(*) AS PRICE_ROWS,
       COUNT_IF(CANDIDATE_ID IS NOT NULL) AS LINKED_CANDIDATES
FROM DERIVATIVE_PRICES
WHERE TO_DATE(DATE) >= '2026-11-01';
