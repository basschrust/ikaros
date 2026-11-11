from datetime import date

import pandas as pd
import pytest

from run.review_equity_candidates import eligible, normalized_candidates


def test_review_accepts_recent_positive_prices():
    frame = pd.DataFrame({"date": ["2026-11-02", "2026-11-03", "2026-11-04"], "close": [2, 3, 4]})
    assert eligible(frame, through=date(2026, 11, 5), lookback_days=30,
                    min_sessions=3, max_stale_days=3)[0]


def test_review_rejects_stale_prices():
    frame = pd.DataFrame({"date": ["2026-10-01", "2026-10-02"], "close": [2, 3]})
    assert not eligible(frame, through=date(2026, 11, 5), lookback_days=60,
                        min_sessions=2, max_stale_days=7)[0]


def test_normalizer_rejects_wrong_suffix(tmp_path):
    f = tmp_path / "symbols.csv"
    f.write_text("ticker,name\\nAAPL,Apple\\n".replace("\\n", "\n"))
    with pytest.raises(ValueError, match="Invalid"):
        normalized_candidates(f, "XETRA")
