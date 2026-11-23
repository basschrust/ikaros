"""Legacy local GPW provider adapter (direct price writes are retired)."""

from __future__ import annotations

import logging
import pandas as pd
from ingestion.prices import fetch_eod_prices

logger = logging.getLogger(__name__)


def main() -> None:
    raise RuntimeError(
        "run.basic_ingest directly wrote authoritative PRICES and is retired. "
        "Use python -m run.update_all --datasets GPW --write."
    )


def fetch_wig_history(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    frames = []
    failed = []
    for symbol in []:
        try:
            df = fetch_eod_prices(symbol=symbol, start_date=start_date, end_date=end_date)
            frames.append(df)
        except Exception:
            logger.exception("Failed to fetch %s", symbol)
            failed.append(symbol)
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    logger.info("WIG download finished: %d successful, %d failed", len(frames), len(failed))
    if failed:
        logger.warning("Failed symbols: %s", ", ".join(failed))
    return result


if __name__ == "__main__":
    main()
