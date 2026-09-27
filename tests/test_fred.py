"""FRED macro series (§6.10, and the oil chain on §6.11)."""

import datetime as dt

import polars as pl
import pytest

from pipeline import store
from pipeline.fetchers import fred


def test_missing_observations_are_dropped_not_zeroed():
    """FRED writes a missing observation as "." — not null, not zero.

    Coercing "." to 0.0 would put a fake zero into a rate series, and a zero
    fed funds rate is a very different claim from a day with no observation.
    """
    frame = fred._parse({"observations": [
        {"date": "2026-01-01", "value": "."},
        {"date": "2026-01-02", "value": "4.33"},
        {"date": "2026-01-03", "value": ""},
        {"date": "2026-01-04", "value": "4.31"},
    ]})
    assert frame.height == 2
    assert frame["value"].to_list() == [4.33, 4.31]
    assert dt.date(2026, 1, 1) not in frame["obs_date"].to_list()


def test_unparseable_rows_are_skipped_without_failing_the_series():
    frame = fred._parse({"observations": [
        {"date": "2026-01-02", "value": "not a number"},
        {"date": "nonsense", "value": "1.0"},
        {"value": "2.0"},
        {"date": "2026-01-05", "value": "5.5"},
    ]})
    assert frame.height == 1
    assert frame["value"].to_list() == [5.5]


def test_empty_payload_gives_a_typed_empty_frame():
    frame = fred._parse({})
    assert frame.is_empty()
    assert frame.schema["obs_date"] == pl.Date
    assert frame.schema["value"] == pl.Float64


def test_duplicate_dates_keep_the_last_observation():
    frame = fred._parse({"observations": [
        {"date": "2026-01-02", "value": "1.0"},
        {"date": "2026-01-02", "value": "2.0"},
    ]})
    assert frame.height == 1
    assert frame["value"].to_list() == [2.0]


def test_without_a_key_nothing_is_fetched_and_nothing_is_substituted(
        tmp_store, monkeypatch):
    """§0.2: a missing source says so. It never stands in another source.

    The temptation is real here — DXY is free and looks like a dollar index.
    It is six currencies and mostly euro; DTWEXBGS is 26 trade-weighted. Under
    the same label they are not the same number.
    """
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    from pipeline import health

    result = fred.fetch_all()
    assert result == {"series": 0, "rows": 0, "key": 0}

    statuses = {record.status for record in health._pending}
    assert statuses == {"needs_key"}
    assert len(health._pending) == len(fred.SERIES)
    for record in health._pending:
        assert "not substituted" in record.error or "no value is substituted" in record.error


def test_oil_chain_series_are_all_configured():
    """The §6.11 chain names five FRED series; all five must be fetchable."""
    for series_id in fred.OIL_CHAIN_SERIES:
        assert series_id in fred.SERIES, series_id


def test_oil_chain_ready_is_false_on_an_empty_store(tmp_store):
    assert fred.oil_chain_ready() is False


def test_oil_chain_ready_turns_true_once_every_series_lands(tmp_store):
    for series_id in fred.OIL_CHAIN_SERIES:
        store.write_macro(series_id, pl.DataFrame({
            "obs_date": [dt.date(2026, 9, 1)], "value": [1.0]}))
    assert fred.oil_chain_ready() is True


def test_every_series_carries_a_label_and_a_reason():
    """A series nobody can justify is one nobody notices has gone stale."""
    for series_id, entry in fred.SERIES.items():
        label, reason = entry
        assert label and reason, series_id


@pytest.mark.parametrize(("series_id", "minimum"), [
    ("CPIAUCSL", 30.0),     # monthly, published weeks later
    ("PAYEMS", 30.0),
    ("ICSA", 7.0),          # weekly
    ("WALCL", 7.0),
    ("DFF", 1.0),           # daily
])
def test_expected_lag_matches_publication_cadence(series_id, minimum):
    """A monthly series three days old is correct, not stale.

    Flagging it trains the eye to ignore the badge, which is the failure this
    project already hit once with the archival backfill.
    """
    assert fred._expected_lag(series_id) >= minimum


def test_net_liquidity_components_are_all_present():
    """Net liquidity = WALCL - WTREGEN - RRPONTSYD, and the units differ.

    WALCL and WTREGEN are in millions, RRPONTSYD in billions. This only pins
    that all three are fetched; the alignment belongs to whoever computes it.
    """
    for series_id in ("WALCL", "WTREGEN", "RRPONTSYD"):
        assert series_id in fred.SERIES
