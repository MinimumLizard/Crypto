"""Breadth and dominance (§6.7)."""

import datetime as dt

import polars as pl

from pipeline.metrics import breadth


def test_advance_decline_refuses_to_backfill_from_one_snapshot():
    """§6.7: the line is built forward. One day is not a line.

    Back-filling from today's top 100 would measure a basket chosen for having
    survived, which rises through periods the real market fell.
    """
    frame = pl.DataFrame({
        "as_of": ["2026-09-26"] * 3,
        "coingecko_id": ["a", "b", "c"],
        "change_24h": [1.0, -1.0, 2.0],
    })
    out = breadth.advance_decline(frame)
    assert not out["available"]
    assert "built forward" in out["reason"]
    assert "survived" in out["reason"]


def test_advance_decline_accumulates_once_there_are_two_days():
    frame = pl.DataFrame({
        "as_of": ["2026-09-25"] * 3 + ["2026-09-26"] * 3,
        "coingecko_id": ["a", "b", "c"] * 2,
        "change_24h": [1.0, 1.0, -1.0, -1.0, -1.0, -1.0],
    })
    out = breadth.advance_decline(frame)
    assert out["available"]
    assert out["days"] == 2
    # Day one: 2 up, 1 down = +1. Day two: 0 up, 3 down = -3. Cumulative -2.
    assert [p["v"] for p in out["series"]] == [1, -2]


def test_dominance_reports_both_bases():
    """Stablecoin cap is large enough to move the include-stables number
    without anything rotating, so both readings are needed."""
    global_snap = pl.DataFrame({
        "observed_at": ["2026-09-26T00:00:00"],
        "as_of": ["2026-09-26"],
        "total_market_cap": [2_000_000_000_000.0],
        "btc_dominance": [50.0],
        "eth_dominance": [10.0],
    })
    stables = pl.DataFrame({
        "date": [dt.date(2026, 9, 26)],
        "value": [200_000_000_000.0],
    })
    out = breadth.dominance(global_snap, stables)
    assert out["available"]
    assert out["btc_dominance_incl_stables"] == 50.0
    # BTC is 1.0T of a 1.8T non-stable market.
    assert out["btc_dominance_ex_stables"] == 55.56
    assert out["stablecoin_dominance"] == 10.0


def test_dominance_without_stablecoins_still_reports_what_it_has():
    global_snap = pl.DataFrame({
        "observed_at": ["2026-09-26T00:00:00"], "as_of": ["2026-09-26"],
        "total_market_cap": [1e12], "btc_dominance": [55.0], "eth_dominance": [12.0],
    })
    out = breadth.dominance(global_snap, pl.DataFrame())
    assert out["available"]
    assert out["btc_dominance_incl_stables"] == 55.0
    assert "stablecoin_note" in out


def test_dominance_says_so_when_there_is_no_snapshot():
    out = breadth.dominance(pl.DataFrame(), pl.DataFrame())
    assert not out["available"]
