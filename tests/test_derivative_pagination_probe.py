"""Regression tests for disappearing page-navigation links on BiznesRadar."""

from datetime import date, timedelta
from unittest.mock import Mock

import pytest

import ikaros_app.derivative_prices as dp


def _page_html(symbol, latest, count):
    dates = [(latest - timedelta(days=i)).isoformat() for i in range(count)]
    return f"{symbol} " + " ".join(dates)


def _fake_parser(html, symbol, start, end):
    from re import findall

    return [
        dict(
            contract_id=symbol,
            date=date.fromisoformat(v),
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=1.0,
            open_interest=None,
            settlement=None,
            source=dp.SOURCE,
        )
        for v in findall(r"\d{4}-\d{2}-\d{2}", html)
    ]


def test_full_page_without_next_link_probes_next_page(monkeypatch):
    monkeypatch.setattr(dp, "parse_page", _fake_parser)
    monkeypatch.setattr(dp, "advertised_pages", lambda html, symbol: set())
    s = Mock()
    s.get.side_effect = [
        Mock(text=_page_html("FUSDZ26", date(2026, 10, 31), 50)),
        Mock(text=_page_html("FUSDZ26", date(2026, 9, 1), 50)),
        Mock(text=_page_html("FUSDZ26", date(2026, 7, 3), 5)),
    ]
    rows, status, pages = dp.collect(s, "FUSDZ26", date(2026, 7, 1), date(2026, 9, 30), delay=0)
    assert pages == 3
    assert status == "WINDOW_COVERED"
    assert any(r["date"] == date(2026, 7, 1) for r in rows)
    assert s.get.call_count == 3


def test_repeated_unadvertised_page_rejected(monkeypatch):
    monkeypatch.setattr(dp, "parse_page", _fake_parser)
    monkeypatch.setattr(dp, "advertised_pages", lambda html, symbol: set())
    html = _page_html("FUSDZ26", date(2026, 10, 31), 50)
    s = Mock()
    s.get.side_effect = [Mock(text=html), Mock(text=html)]
    with pytest.raises(RuntimeError, match="Non-advancing pagination"):
        dp.collect(s, "FUSDZ26", date(2026, 7, 1), date(2026, 9, 30), delay=0)


def test_empty_probed_page_marked_unverified(monkeypatch):
    monkeypatch.setattr(dp, "parse_page", _fake_parser)
    monkeypatch.setattr(dp, "advertised_pages", lambda html, symbol: set())
    s = Mock()
    s.get.side_effect = [
        Mock(text=_page_html("FUSDZ26", date(2026, 10, 31), 50)),
        Mock(text="FUSDZ26 no history"),
    ]
    rows, status, pages = dp.collect(s, "FUSDZ26", date(2026, 7, 1), date(2026, 9, 30), delay=0)
    assert status == "UNVERIFIED_SOURCE_END"
    assert pages == 2
    assert len(rows) == 19  # Found observations, but coverage remains unverified.
