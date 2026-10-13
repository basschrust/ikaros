import sys
import pandas as pd
from pathlib import Path


from database.duckdb_storage import Repository
from config.config import load_config
from utils.id_generator import id_generator


if __name__ == "__main__":

    print("Filling currencies...")
    repository = Repository()

    ccy_config = load_config("currencies.yaml")
    ccy_df = pd.DataFrame(ccy_config.get("currencies", []))

    ccy_df["currency_id"] = [id_generator.next() for _ in range(len(ccy_df))]
    ccy_df["calendar_id"]= 1
    ccy_df["symbol"] = None
    ccy_df["comment"] = None
    ccy_df["is_active"] = True

    repository.save_currencies(currencies_df=ccy_df)

    # TODO: get ids for currencies to be able to match them for pairs

    # currency pairs
    ccy_pairs = ccy_config.get("currency_pairs", [])
    ccy_pairs_df = pd.DataFrame(ccy_pairs)
    ccy_pairs_df["currency_pair_id"] = [id_generator.next() for _ in range(len(ccy_pairs_df))]

    ccy_pairs_fin = (
        ccy_pairs_df
        .merge(
            ccy_df[["code", "currency_id"]].rename(
                columns={"code": "base", "currency_id": "base_currency_id"}
            ),
            on="base",
            how="left",
            validate="many_to_one",
        )
        .merge(
            ccy_df[["code", "currency_id"]].rename(
                columns={"code": "quote", "currency_id": "quote_currency_id"}
            ),
            on="quote",
            how="left",
            validate="many_to_one",
        )
    )

    print(ccy_pairs_fin)

    ccy_pairs_fin["is_active"] = True

    assert ccy_pairs_fin["base_currency_id"].notna().all()
    assert ccy_pairs_fin["quote_currency_id"].notna().all()

    repository.save_currency_pairs(currency_pairs_df=ccy_pairs_fin)



