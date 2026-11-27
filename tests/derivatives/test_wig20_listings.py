"""Offline discovery tests; fixture tickers are explicit listing evidence."""

from datetime import date

import pytest

from providers.derivatives.wig20_listings import (
    BANKIER,
    BIZNESRADAR,
    combine_listings,
    contract_terms,
    discover,
    listing_rows,
)


def html(*, symbols: list[tuple[str, str]]) -> str:
    return (
        "<table>"
        + "".join(
            f"<tr><td><a>{symbol}</a></td><td>1</td><td>2</td><td>3</td><td>{expiry}</td></tr>"
            for symbol, expiry in symbols
        )
        + "</table>"
    )


def test_exact_symbols_only():
    page = html(symbols=[("FW20Z2620", "2026-12-17"), ("FW20KONT", ""), ("FPZUZ26", "2026-12-17")])
    assert listing_rows(html=page, kind="FUTURE", bankier=True) == {"FW20Z2620": date(2026, 12, 17)}


def test_union_contains_options_without_a_trade_or_reported_expiry():
    listed, issues = combine_listings(
        listings={
            BANKIER["OPTION"]: {"OW20A274250": date(2027, 1, 14)},
            BIZNESRADAR["OPTION"]: {"OW20A274250": None, "OW20M274300": None},
        },
        as_of=date(2026, 11, 27),
        closures=set(),
        calendar_years={2026, 2027},
    )
    assert not issues
    assert [item.symbol for item in listed] == ["OW20A274250", "OW20M274300"]
    assert [item.side for item in listed] == ["CALL", "PUT"]
    assert all(item.expiry == date(2027, 1, 15) for item in listed)
    assert listed[1].manifest_row()["strike"] == "4300"
    assert "STANDARD_DERIVED" in listed[0].manifest_row()["source"]


def test_wrong_expiry_is_not_silently_registered():
    listed, issues = combine_listings(
        listings={BANKIER["OPTION"]: {"OW20A274250": date(2027, 1, 22)}},
        as_of=date(2026, 11, 27),
        closures=set(),
        calendar_years={2027},
    )
    assert not listed
    assert "differs" in issues[0]


def test_expired_contract_excluded():
    listed, issues = combine_listings(
        listings={BIZNESRADAR["OPTION"]: {"OW20K264250": None}},
        as_of=date(2026, 11, 27),
        closures=set(),
        calendar_years={2026},
    )
    assert not listed and not issues


def test_third_friday_adjusted_for_closure():
    assert contract_terms(
        symbol="FW20Z2620", closures={date(2026, 12, 18)}, calendar_years={2026}
    ) == (
        "FUTURE",
        date(2026, 12, 17),
        None,
        None,
    )


def test_unknown_year_fails_closed():
    with pytest.raises(ValueError, match="calendar"):
        contract_terms(symbol="OW20A284250", closures=set(), calendar_years={2027})


def test_empty_page_is_source_error():
    with pytest.raises(ValueError, match="No exact"):
        listing_rows(html="<html>Access denied</html>", kind="OPTION", bankier=False)


def test_failed_provider_reported_without_discarding_other_listing():
    class Response:
        def __init__(self, *, text: str):
            self.text = text

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, **kwargs):
            if url == BANKIER["OPTION"]:
                raise OSError("offline fixture")
            if "opcje" in url.lower():
                return Response(text=html(symbols=[("OW20A274250", "")]))
            return Response(text=html(symbols=[("FW20Z2620", "2026-12-17")]))

    listed, issues = discover(
        session=Session(), as_of=date(2026, 11, 27), closures=set(), calendar_years={2026, 2027}
    )
    assert {item.symbol for item in listed} == {"FW20Z2620", "OW20A274250"}
    assert len(issues) == 1 and "offline fixture" in issues[0]
