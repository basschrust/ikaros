"""Offline regression tests for historical WIG20 index pagination."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ingestion.market_quote_feeds import BiznesRadarIndexFeed


class Response:
    def __init__(self, *, html: str) -> None:
        self.text = html

    def raise_for_status(self) -> None:
        pass


class ArchiveSession:
    def __init__(self) -> None:
        self.pages: list[int] = []

    def get(self, url: str, **_kwargs: object) -> Response:
        page = int(url.rsplit(",", maxsplit=1)[-1]) if "," in url else 1
        self.pages.append(page)
        # One synthetic valid bar per numbered page, moving towards 2023.
        day = date(2026, 11, 26) - timedelta(days=(page - 1) * 105)
        row_date = day.strftime("%d.%m.%Y")
        html = (
            "<html><title>Notowania historyczne WIG20</title>"
            "<table><thead><tr><th>Data</th><th>Otwarcie</th><th>Max</th>"
            "<th>Min</th><th>Zamknięcie</th></tr></thead><tbody>"
            f"<tr><td>{row_date}</td><td>2100.00</td><td>2110.00</td>"
            "<td>2090.00</td><td>2105.00</td></tr></tbody></table></html>"
        )
        return Response(html=html)


def test_2023_archive_requires_more_than_ten_pages() -> None:
    session = ArchiveSession()
    feed = BiznesRadarIndexFeed(session=session)
    results = feed.history("WIG20", date(2023, 1, 1), date(2024, 12, 31))
    assert len(session.pages) > 10
    assert results
    assert all(date(2023, 1, 1) <= r.day <= date(2024, 12, 31) for r in results)
    assert len({r.day for r in results}) == len(results)
    assert all(r.volume is None for r in results)


def test_hitting_bound_reports_date_and_oldest_visited() -> None:
    session = ArchiveSession()
    feed = BiznesRadarIndexFeed(session=session, max_pages=2)
    with pytest.raises(ValueError, match=r"did not reach 2023-01-01 after 2 pages"):
        feed.history("WIG20", date(2023, 1, 1), date(2024, 12, 31))
    assert session.pages == [1, 2]
