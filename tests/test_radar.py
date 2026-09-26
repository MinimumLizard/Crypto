"""The catalyst radar's gate and score, and news tagging (§6.13)."""

import numpy as np
import polars as pl
import pytest

from pipeline.fetchers import feeds
from pipeline.metrics import radar


def test_volume_zscore_excludes_today(tmp_store):
    """A z-score that includes the observation being scored understates it."""
    n = 90
    volume = [100.0] * (n - 1) + [1000.0]
    bars = pl.DataFrame({"volume": volume, "close": [1.0] * n,
                         "date": list(range(n))})
    # Flat history has zero dispersion; there is no scale, so no score.
    assert radar._volume_zscore(bars) is None

    rng = np.random.default_rng(5)
    noisy = list(100 + rng.normal(0, 5, n - 1)) + [1000.0]
    bars = pl.DataFrame({"volume": noisy, "close": [1.0] * n,
                         "date": list(range(n))})
    z = radar._volume_zscore(bars)
    assert z is not None and z > 50       # 900 above a sd of ~5


def test_volume_zscore_needs_a_full_window():
    short = pl.DataFrame({"volume": [1.0] * 10, "close": [1.0] * 10,
                          "date": list(range(10))})
    assert radar._volume_zscore(short) is None


def test_clamp_bounds_every_component():
    assert radar._clamp(-5.0) == 0.0
    assert radar._clamp(5.0) == 1.0
    assert radar._clamp(0.4) == pytest.approx(0.4)


def test_near_level_picks_the_closest_and_names_it():
    distance, name = radar._near_level({
        "available": True, "price": 100.0,
        "sma200d": 90.0,          # 11.1% away
        "sma50w": 101.0,          # 1.0% away
        "prev_month_high": 120.0,  # 16.7% away
    })
    assert name == "50-week"
    assert distance == pytest.approx(0.990, abs=1e-2)


def test_near_level_without_price_is_unmeasurable():
    assert radar._near_level({"available": False}) == (None, "")
    assert radar._near_level({"available": True}) == (None, "")


def test_money_formats_without_inventing_precision():
    assert radar._money(None) == "unknown"
    assert radar._money(1_500_000_000) == "$1.5bn"
    assert radar._money(2_400_000) == "$2.4m"
    assert radar._money(950_000) == "$950.0k"


def test_the_gate_is_a_real_threshold():
    """The whole design rests on these being applied BEFORE ranking."""
    assert radar.MIN_DAY_VOLUME_USD > 0
    assert radar.MIN_MARKET_CAP_USD > 0


def test_every_component_has_a_visible_weight():
    assert radar.COMPONENTS
    for name, weight in radar.COMPONENTS.items():
        assert weight > 0, name


# --- news tagging ----------------------------------------------------------

@pytest.fixture(scope="module")
def aliases():
    return feeds._aliases()


@pytest.mark.parametrize(("headline", "expected"), [
    ("Uniform Resource Locator standard updated", set()),
    ("Uniswap DAO votes on the fee switch", {"UNI"}),
    ("Sky is blue today", set()),
    ("Sky protocol raises its stablecoin savings rate", {"SKY"}),
    ("Fluid dynamics research published", set()),
    ("Virtual reality headset sales fall", set()),
    ("A ray of sunshine after dusk", set()),
    ("Aave launches V4", {"AAVE"}),
    ("Chainlink oracle protocol adds new feeds", {"LINK"}),
])
def test_headlines_are_tagged_only_on_a_whole_word_with_context(
        headline, expected, aliases):
    """A wrongly tagged headline is worse than an untagged one.

    Substring matching turns "Uniform" into UNI, and whole-word matching alone
    still turns "Sky is blue" into SKY because the project's real name IS an
    English word. Those aliases additionally require crypto context.
    """
    assert set(feeds._tag(headline, aliases)) == expected


def test_ambiguous_aliases_are_all_lowercase():
    """They are compared against a lowercased headline, so a capital never matches."""
    for alias in feeds.AMBIGUOUS_ALIASES:
        assert alias == alias.lower()


def test_rss_parser_handles_rss_and_atom():
    rss = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>First</title><link>https://example.com/1</link>
      <pubDate>Fri, 25 Sep 2026 10:00:00 +0000</pubDate></item>
    </channel></rss>"""
    items = feeds._parse_rss(rss)
    assert items[0]["title"] == "First"
    assert feeds._normalise_date(items[0]["published"]) == "2026-09-25"

    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Second</title><link href="https://example.com/2"/>
      <updated>2026-09-24T08:00:00Z</updated></entry></feed>"""
    items = feeds._parse_rss(atom)
    assert items[0]["title"] == "Second"
    assert items[0]["link"] == "https://example.com/2"
    assert feeds._normalise_date(items[0]["published"]) == "2026-09-24"


def test_rss_parser_survives_malformed_xml():
    assert feeds._parse_rss("<not xml") == []
