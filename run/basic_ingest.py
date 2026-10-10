
import pandas as pd

from database.duckdb_storage import Repository
from ingestion.prices import fetch_eod_prices
from config.config import load_config

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



if __name__ == "__main__":
    main()


