-- Optional explicit migration; run/backfill_rates.py also performs this before --write.
-- Run in Snowsight when you prefer controlled schema migrations.
ALTER TABLE DAEDALUS.MARKET_DATA.RATE_OBSERVATIONS
  ADD COLUMN IF NOT EXISTS "BID" FLOAT;
ALTER TABLE DAEDALUS.MARKET_DATA.RATE_OBSERVATIONS
  ADD COLUMN IF NOT EXISTS "ASK" FLOAT;
