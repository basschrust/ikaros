import logging
import pandas as pd

from database.duckdb_storage import Repository
from ingestion.prices import fetch_eod_prices
from config.config import load_config

logger = logging.getLogger(__name__)




### more important functions

print("Ikaros, fly high!")

def main():
    print("Starting the Ikaros ingest...")
    print("Preparing DB...")
    repository = Repository()
    print("DB ready. Starting ingestion...")
    # get instruments to ingest
    config = load_config()

    instruments = config.get("instruments", [])

    for instr in instruments:
        print("Ingesting instrument ", instr["symbol"])
        prices = fetch_eod_prices(symbol=instr["symbol"],
                                  start_date=config["history"]["start_date"],)

        # dump to a file at first
        file_name = f"output/{instr["symbol"]}.csv"

        prices.to_csv(file_name, index=False)

        # store in DB
        print("Storing result into DB...")
        repository.save_prices(prices_df=prices)
        print("Done!")


def fetch_wig_history(start_date: str,
                      end_date: str|None = None
                      ) -> pd.DataFrame:
    frames = []
    failed = []

    for symbol in []:
        try:
            df = fetch_eod_prices(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
            frames.append(df)
        except Exception:
            logger.exception("Failed to fetch %s", symbol)
            failed.append(symbol)

    result = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame()
    )

    logger.info(
        "WIG download finished: %d successful, %d failed",
        len(frames),
        len(failed),
    )

    if failed:
        logger.warning("Failed symbols: %s", ", ".join(failed))

    return result



if __name__ == "__main__":
    #main()
    repository = Repository()

    for day in ["2024-01-02", "2024-01-03"]:
        #dates_in_year(2025):
        print("Ingesting day ", day)
        prices = fetch_wig_history(start_date=day,
                          end_date=day)

    # prices = fetch_wig_history(start_date="2026-09-30",
    #                            end_date="2026-09-30")

    # store in DB
        print("Storing result into DB...")
        repository.save_prices(prices_df=prices)
        print("Done!")
