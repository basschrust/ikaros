import pandas as pd

from database.duckdb_storage import Repository
from config.config import load_config
from utils.dateutils import working_days_in_year


if __name__ == "__main__":
    print("Filling calendar...")
    repository = Repository()

    calendars_config = load_config("calendars.yaml")

    # calendar_id = sys.argv[1]
    for calendar in calendars_config.get("calendars", []):
        # check if this calendar is in DB?
        existing_calendars = repository.get_calendar(calendar.get("calendar_id"))
        if len(existing_calendars) > 0:
            print(f"Calendar {calendar.get('name')} already exists. Skipping...")
            continue

        # save calendar in DB
        # print(calendar)
        calendar_df = pd.DataFrame([calendar])
        # print(calendar_df)
        repository.save_calendars(calendars_df=calendar_df)

        # save trading days (2025-2027)
        non_trading_days = calendar.pop("non_trading_days", [])

        trading_days_a = []
        for year in range(1981, 2027):
            trading_days_a.extend(working_days_in_year(year=year))
        trading_days_b = []
        for td in trading_days_a:
            if td not in non_trading_days:
                trading_days_b.append(td)  # TODO: optimize

        trading_days_df = pd.DataFrame(
            {
                "date": trading_days_b,
                "calendar_id": calendar.get("calendar_id"),
                "comment": calendar.get("comment"),
                "load_status": "PENDING",
                "load_date": None,
            }
        )

        repository.save_trading_days(trading_days_df=trading_days_df)

        # save non-trading days (2025-2027)
        non_trading_days_df = pd.DataFrame(
            {
                "date": non_trading_days,
                "calendar_id": calendar.get("calendar_id"),
                "comment": "",
            }
        )

        repository.save_non_trading_days(non_trading_days_df=non_trading_days_df)
