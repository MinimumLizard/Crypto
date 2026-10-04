"""How a regime reading is chosen, and what is said when there isn't one.

`_regime_for` picks a venue and `_score` decides whether the engine produced
anything on it. Both used to make claims that were not checked: a payload could
carry "Computed on Binance daily bars" while holding no score at all, and a
venue that cleared the row gate ended the search even when it scored nothing.
"""

import datetime as dt

import numpy as np
import polars as pl

from pipeline import artefacts, registry, store
from pipeline.signals import minilizard as ml


def _asset(symbol: str, *, binance: str | None = None) -> registry.Asset:
    return registry.Asset(symbol=symbol, name=symbol, coingecko_id=symbol.lower(),
                          kind="book", binance_spot=binance)


def _bars(n: int, *, volume: float | list[float] = 1000.0,
          start=dt.date(2020, 1, 1)) -> pl.DataFrame:
    """`n` daily bars that drift up, so every price block is well defined."""
    rng = np.random.default_rng(7)
    close = 100 * np.cumprod(1 + rng.normal(0.001, 0.02, n))
    volumes = ([volume] * n) if isinstance(volume, float) else list(volume)
    return pl.DataFrame({
        "date": [start + dt.timedelta(days=i) for i in range(n)],
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": [float(v) for v in volumes],
        "interval": ["1d"] * n, "source": ["test"] * n,
    })


def test_a_venue_with_enough_rows_but_no_score_falls_through_to_another(tmp_store):
    """Binance clearing the ROW gate used to end the search.

    The row gate counts bars; it does not ask whether the engine scored any of
    them. A pre-listing stretch of zero-volume candles leaves the newest bar
    unscoreable, and the old code published that failure under the heading
    "Computed on Binance daily bars, the series TradingView draws".
    """
    asset = _asset("TEST", binance="TESTUSDT")
    # 400 rows, but the newest bars have no volume, so no composite.
    volumes = [1000.0] * 380 + [0.0] * 20
    store.write_ohlcv("TEST", "binance", _bars(400, volume=volumes))
    store.write_ohlcv("TEST", "coinbase", _bars(700))

    result = artefacts._regime_for(asset)
    assert result["available"] is True
    assert result["scored_on"] == "coinbase"
    assert result["parity"] == "substitute"
    # The note must not claim Binance computed it.
    assert "Computed on Binance" not in result["parity_note"]
    assert "coinbase" in result["parity_note"]


def test_an_unscoreable_latest_bar_is_never_published_as_computed(tmp_store):
    asset = _asset("TEST", binance="TESTUSDT")
    volumes = [1000.0] * 380 + [0.0] * 20
    store.write_ohlcv("TEST", "binance", _bars(400, volume=volumes))

    result = artefacts._regime_for(asset)
    assert result["available"] is False
    assert "parity_note" not in result
    assert "binance" in result["reason"]


def test_the_reason_names_the_block_that_is_actually_undefined(tmp_store):
    """A sentence that blames volume is wrong about half the cases.

    MORPHO's nans come from the SMA200 in the structure block; XMR's from a
    zero-volume backfill. The reason is derived from which block is nan on the
    newest bar rather than asserted.
    """
    asset = _asset("TEST", binance="TESTUSDT")
    volumes = [1000.0] * 380 + [0.0] * 20
    store.write_ohlcv("TEST", "binance", _bars(400, volume=volumes))
    bars = store.read_ohlcv("TEST", "binance")

    result = artefacts._score(asset, bars)
    assert result["available"] is False
    assert "volume" in result["reason"]
    assert "price structure" not in result["reason"]
    assert result["required_bars"] == ml.WARMUP_BARS


def test_every_venue_tried_is_named_with_its_bar_count(tmp_store):
    """"No reading" must say what was looked at. GEOD and AZTEC are the live
    cases: no Binance pair and nothing else long enough either."""
    asset = _asset("TEST")
    store.write_ohlcv("TEST", "coinbase", _bars(93))
    store.write_ohlcv("TEST", "hyperliquid", _bars(224))

    result = artefacts._regime_for(asset)
    assert result["available"] is False
    assert "no Binance pair exists" in result["reason"]
    assert "coinbase has only 93 bars" in result["reason"]
    assert "hyperliquid has only 224 bars" in result["reason"]


def test_a_short_engine_history_is_disclosed_rather_than_withheld(tmp_store):
    """§0.2 asks for the limitation stated, not the panel emptied.

    A 356-bar Binance series scores from bar 200, giving 156 bars of engine
    output. The reading is computed from settled indicators and is real; what
    it lacks is enough history to backtest. Suppressing it would discard a
    sound score.
    """
    asset = _asset("TEST", binance="TESTUSDT")
    store.write_ohlcv("TEST", "binance", _bars(356))

    result = artefacts._regime_for(asset)
    assert result["available"] is True
    assert result["latest"]["score"] is not None
    assert result["engine_bars"] < ml.WARMUP_BARS
    assert result["bars"] == 356
    assert result["short_history_note"]
    assert str(result["engine_bars"]) in result["short_history_note"]
    assert result["engine_from"] in result["short_history_note"]


def test_a_long_history_carries_no_short_history_note(tmp_store):
    asset = _asset("TEST", binance="TESTUSDT")
    store.write_ohlcv("TEST", "binance", _bars(900))

    result = artefacts._regime_for(asset)
    assert result["available"] is True
    assert result["short_history_note"] is None
    assert result["engine_bars"] >= ml.WARMUP_BARS
    assert result["scored_on"] == "binance"
    assert result["parity"] == "binance"


def test_the_published_history_leaves_unscoreable_bars_blank(tmp_store):
    """Not a NEUTRAL label. §0.2 forbids the placeholder."""
    asset = _asset("TEST", binance="TESTUSDT")
    store.write_ohlcv("TEST", "binance", _bars(700))

    result = artefacts._regime_for(asset)
    blank = [row for row in result["history"] if row["s"] is None]
    for row in blank:
        assert row["r"] is None, row
    # Every scored row does carry a regime.
    for row in result["history"]:
        if row["s"] is not None:
            assert row["r"]


def test_a_signal_is_only_ever_emitted_on_a_scored_bar(tmp_store):
    asset = _asset("TEST", binance="TESTUSDT")
    store.write_ohlcv("TEST", "binance", _bars(900))
    result = artefacts._regime_for(asset)
    for entry in result["signals"]:
        assert entry["score"] is not None
        assert not np.isnan(entry["score"])
