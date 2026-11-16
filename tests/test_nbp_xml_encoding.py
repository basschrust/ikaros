"""NBP XML decoding regressions; no HTTP or Snowflake access."""

from datetime import date

import pytest

from ikaros_app.rates_central_europe import NBPPolicyProvider, parse_nbp_xml


XML = """<?xml version="1.0" encoding="ISO-8859-2"?>
<stopy><opis>Stopy procentowe zażółć</opis>
<pozycje obowiazuje_od="2024-01-01">
<pozycja id="ref" oprocentowanie="5,75"/>
<pozycja id="dep" oprocentowanie="5,25"/>
<pozycja id="lom" oprocentowanie="6,25"/>
<pozycja id="red" oprocentowanie="5,80"/>
<pozycja id="dys" oprocentowanie="5,85"/>
</pozycje></stopy>"""


def test_nbp_xml_non_utf8_archive_bytes():
    rows = parse_nbp_xml(XML.encode("iso-8859-2"), date(2025, 1, 1), date(2025, 1, 2))
    assert len(rows) == 10
    assert next(r.rate for r in rows if r.series_id == "NBP_REFERENCE_PLN") == 0.0575


def test_nbp_provider_uses_raw_bytes_not_response_text():
    payload = XML.encode("iso-8859-2")

    class Response:
        content = payload

        @property
        def text(self):
            raise AssertionError("Provider must not access response.text")

        def raise_for_status(self):
            pass

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    rows = NBPPolicyProvider().history(Session(), date(2025, 1, 1), date(2025, 1, 1))
    assert len(rows) == 5


def test_nbp_html_response_fails_explicitly():
    with pytest.raises(ValueError, match="HTML instead of XML"):
        parse_nbp_xml(
            b"<!doctype html><html><body>blocked</body></html>", date(2025, 1, 1), date(2025, 1, 2)
        )


def test_nbp_empty_response_fails_explicitly():
    with pytest.raises(ValueError, match="Empty NBP XML"):
        parse_nbp_xml(b"", date(2025, 1, 1), date(2025, 1, 2))
