from datetime import date

import pytest

from ikaros_app.rates import (
    configured_series, parse_treasury_csv, parse_ecb_csv, percent_to_decimal,
    selected_sources,
)
from ikaros_app.update import Instrument, build_plan, DATASETS


def test_treasury_par_rate_normalized():
    csv = 'Date,1 Mo,10 Yr,30 Yr\n11/06/2026,4.25,4.73,5.15\n'
    rates = parse_treasury_csv(csv, date(2026, 11, 1), date(2026, 11, 7))
    assert len(rates) == 3
    assert rates[0].day == date(2026, 11, 6)
    assert next(r.rate for r in rates if r.series_id == 'UST_PAR_USD_1M') == .0425


def test_ecb_negative_spot_rates():
    csv = ('KEY,TIME_PERIOD,OBS_VALUE,DATA_TYPE_FM\n'
           'YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y,2026-11-06,-0.25,SR_10Y\n')
    got = parse_ecb_csv(csv, date(2026, 11, 1), date(2026, 11, 7))
    assert len(got) == 1
    assert got[0].series_id == 'ECB_AAA_SPOT_EUR_10Y'
    assert got[0].rate == -.0025


def test_rate_registry():
    series, sources = selected_sources(['UST_PAR_USD_10Y', 'ECB_AAA_SPOT_EUR_2Y'])
    assert sources == {'UST', 'ECB'}
    assert series['ECB_AAA_SPOT_EUR_2Y'].compounding == 'CONTINUOUS'
    with pytest.raises(ValueError):
        selected_sources(['UNKNOWN'])


def test_rates_daily_planner():
    target = date(2026, 11, 9)
    plan = build_plan({
        'RATES': [Instrument('UST_PAR_USD_10Y', date(2026, 11, 6)),
                  Instrument('ECB_AAA_SPOT_EUR_10Y', None)],
    }, target=target, datasets=('RATES',))
    assert len(plan.jobs) == 2
    assert all(j.argv[2] == 'run.backfill_rates' for j in plan.jobs)
    assert all('--write' not in j.argv for j in plan.jobs)
    assert any(j.start == date(2026, 10, 10) for j in plan.jobs)
    assert 'RATES' in DATASETS


def test_invalid_and_duplicate_rates():
    with pytest.raises(ValueError):
        percent_to_decimal('nan', 'rate')
    csv = 'Date,10 Yr\n11/06/2026,4.1\n11/06/2026,4.2\n'
    with pytest.raises(ValueError):
        parse_treasury_csv(csv, date(2026, 11, 1), date(2026, 11, 9))
