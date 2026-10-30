from datetime import date
import pytest
from run.backfill_wig20_prices import fetch_csv, validate_csv_response


@pytest.mark.parametrize("body,ctype", [
    ("<!DOCTYPE html><html>Get API key</html>", "text/html"),
    ("Access denied", "text/plain"),
    ("Exceeded the daily hits limit", "text/plain"),
    ("Uzyskaj apikey: CAPTCHA", "text/plain"),
])
def test_denials_fail_without_silent_empty_data(body, ctype):
    with pytest.raises(RuntimeError, match="denied CSV"):
        validate_csv_response(body, ctype)


def test_valid_csv_accepted():
    text = "Date,Open,High,Low,Close,Volume\n2026-10-28,4241.43,4256.15,4225.72,4247.19,100\n"
    assert validate_csv_response(text, "text/plain") == text


def test_apikey_is_sent_without_logging(monkeypatch):
    calls = []
    class Response:
        text = "Date,Open,High,Low,Close\n2026-10-28,4241.43,4256.15,4225.72,4247.19\n"
        headers = {"Content-Type": "text/csv"}
        def raise_for_status(self):
            pass
    def fake_get(*a, **kw):
        calls.append(kw)
        return Response()
    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    assert fetch_csv(date(2026, 1, 1), date(2026, 10, 28), api_key="secret").startswith("Date,")
    assert calls[0]["params"]["apikey"] == "secret"
    assert calls[0]["params"]["s"] == "wig20"
