"""Geopolitics, the calendar and the .ics export (§6.11)."""

import datetime as dt

import numpy as np
import polars as pl
import pytest

from pipeline import cli, store
from pipeline.fetchers import calendar as calendar_feed
from pipeline.fetchers import geopolitics as geo_feed
from pipeline.metrics import geopolitics as geo


def _write_theme(theme: str, values: list[float], start: dt.date) -> None:
    rows = [{
        "theme": theme,
        "date": (start + dt.timedelta(days=i)).isoformat(),
        "volume_pct": float(v),
        "tone": 0.0,
    } for i, v in enumerate(values)]
    store.write_snapshot("gdelt_themes", pl.DataFrame(rows), key=["theme", "date"])


def test_spike_baseline_excludes_the_spike(tmp_store):
    """The window that scores a spike must not contain it.

    A baseline overlapping the recent window is dragged upward by the very move
    it is supposed to measure, so the bigger the spike the more the score
    understates it. This pins the non-overlap by constructing a series where
    the two answers differ a lot.
    """
    quiet = [1.0] * 120
    spike = [10.0] * geo.RECENT_DAYS
    _write_theme("Test", quiet + spike, dt.date(2026, 1, 1))

    result = geo.theme_spikes()
    row = next(r for r in result["rows"] if r["theme"] == "Test")

    assert row["available"]
    assert row["baseline_volume_pct"] == pytest.approx(1.0)
    assert row["recent_volume_pct"] == pytest.approx(10.0)
    # A flat baseline has zero dispersion, so there is no scale to score on and
    # the honest answer is "undefined", not zero and not infinity.
    assert row["spike_z"] is None


def test_spike_is_measured_against_prior_variation(tmp_store):
    rng = np.random.default_rng(7)
    baseline = list(1.0 + rng.normal(0, 0.1, 120))
    _write_theme("Noisy", baseline + [3.0] * geo.RECENT_DAYS, dt.date(2026, 1, 1))

    row = next(r for r in geo.theme_spikes()["rows"] if r["theme"] == "Noisy")
    assert row["spike_z"] is not None
    assert row["spike_z"] > 5          # a 2-unit move on a 0.1 sd baseline


def test_a_theme_without_enough_history_says_so(tmp_store):
    _write_theme("Short", [1.0] * 10, dt.date(2026, 1, 1))
    row = next(r for r in geo.theme_spikes()["rows"] if r["theme"] == "Short")
    assert row["available"] is False
    assert "history" in row["reason"]


def test_threat_and_act_reading_is_directional():
    assert "priced" in geo._threat_act_reading(200.0, 100.0)
    assert "underway" in geo._threat_act_reading(100.0, 200.0)
    assert "close" in geo._threat_act_reading(100.0, 100.0)
    assert geo._threat_act_reading(None, 100.0) == "not measurable"


def test_oil_chain_names_the_missing_source_rather_than_showing_a_number(tmp_store):
    chain = geo.oil_chain()
    assert chain["total"] == len(geo.OIL_CHAIN)
    for link in chain["links"]:
        if not link["available"]:
            # §0.2: never a zero, never a placeholder -- a stated reason.
            assert link.get("reason")
            assert "value" not in link or link["value"] is None


def test_fomc_parse_puts_the_decision_on_the_final_day():
    html = (
        '<a id="1">2026 FOMC Meetings</a>'
        '<div class="fomc-meeting__month"><strong>March</strong></div>'
        '<div class="fomc-meeting__date">17-18</div>'
        '<a id="2">2027 FOMC Meetings</a>'
        '<div class="fomc-meeting__month"><strong>January</strong></div>'
        '<div class="fomc-meeting__date">26-27</div>'
    )
    events = calendar_feed._parse_fomc(html)
    dates = [e["date"] for e in events]
    assert "2026-03-18" in dates
    assert "2027-01-27" in dates
    # The start day must not also appear as its own event.
    assert "2026-03-17" not in dates


def test_fomc_parse_attributes_months_to_the_right_year():
    html = (
        '<a id="1">2026 FOMC Meetings</a>'
        '<div class="fomc-meeting__month"><strong>December</strong></div>'
        '<div class="fomc-meeting__date">8-9</div>'
        '<a id="2">2027 FOMC Meetings</a>'
        '<div class="fomc-meeting__month"><strong>December</strong></div>'
        '<div class="fomc-meeting__date">7-8</div>'
    )
    dates = {e["date"] for e in calendar_feed._parse_fomc(html)}
    assert dates == {"2026-12-09", "2027-12-08"}


def test_option_expiries_land_on_a_friday():
    for event in calendar_feed.option_expiries(months_ahead=12):
        assert dt.date.fromisoformat(event["date"]).weekday() == 4


def test_ics_end_date_is_exclusive(tmp_store):
    store.write_snapshot("calendar", pl.DataFrame([{
        "date": dt.date.today().isoformat(), "title": "Test event",
        "category": "test", "source": "test", "provenance": "configured",
        "detail": "detail",
    }]), key=["date", "title"])

    cli.write_ics()
    # Read BYTES: read_text() applies universal-newline translation and would
    # turn a correct CRLF file into one that looks like it uses bare LF.
    raw = (cli.paths.ARTEFACTS / "calendar.ics").read_bytes()
    today = dt.date.today()
    assert f"DTSTART;VALUE=DATE:{today:%Y%m%d}".encode() in raw
    # RFC 5545: an all-day event's DTEND is the day AFTER it. Getting this
    # wrong renders a one-day event as zero-length in some clients.
    assert f"DTEND;VALUE=DATE:{today + dt.timedelta(days=1):%Y%m%d}".encode() in raw
    assert raw.endswith(b"\r\n")
    assert raw.count(b"\n") == raw.count(b"\r\n")      # no bare LF anywhere


def test_ics_uids_are_stable_across_runs(tmp_store):
    """A salted hash would make every rebuild look like a new event."""
    store.write_snapshot("calendar", pl.DataFrame([{
        "date": dt.date.today().isoformat(), "title": "Stable event",
        "category": "test", "source": "test", "provenance": "configured",
        "detail": "",
    }]), key=["date", "title"])

    cli.write_ics()
    first = [line for line in (cli.paths.ARTEFACTS / "calendar.ics")
             .read_text().splitlines() if line.startswith("UID:")]
    cli.write_ics()
    second = [line for line in (cli.paths.ARTEFACTS / "calendar.ics")
              .read_text().splitlines() if line.startswith("UID:")]
    assert first == second and first


def test_event_topics_do_not_collide():
    """A market must not be claimable by two topics through the same phrase."""
    phrases = [(label, phrase)
               for label, group in geo_feed.EVENT_TOPICS for phrase in group]
    seen: dict[str, str] = {}
    for label, phrase in phrases:
        assert phrase not in seen or seen[phrase] == label, (
            f"{phrase!r} is claimed by both {seen.get(phrase)} and {label}")
        seen[phrase] = label


def test_balance_of_power_does_not_land_in_a_single_chamber_topic():
    assert geo_feed._topic_for("2026 Balance of Power: D Senate, D House") \
        == "Balance of power"
    assert geo_feed._topic_for(
        "Will the Democratic Party control the House after the 2026 Midterm "
        "elections?") == "House control"


def test_gdelt_sweep_prefers_the_stalest_theme(tmp_store):
    """Otherwise the themes at the bottom of the list never refresh at all."""
    fresh = geo_feed.THEMES[0][0]
    store.write_snapshot("gdelt_themes", pl.DataFrame([{
        "theme": fresh, "date": "2026-01-01", "volume_pct": 1.0, "tone": 0.0,
    }]), key=["theme", "date"])

    order = [label for label, _ in geo_feed._stalest_first()]
    assert order[-1] == fresh
    assert set(order) == {label for label, _ in geo_feed.THEMES}


@pytest.mark.parametrize(("question", "topic"), [
    # "brent" is a substring of "Brentford", which put a football market under
    # Oil until phrase matching was moved to word boundaries.
    ("Will Brentford win the 2026-27 English Premier League Championship?", None),
    ("Brent crude above $90 by year end?", "Oil"),
    ("Will Crude Oil reach a new all-time high by December 31?", "Oil"),
    ("Strait of Hormuz traffic returns to normal by December 31?", "Hormuz reopening"),
    ("Clarity Act (H.R.3633) signed into law in 2026?", "Crypto market structure"),
])
def test_event_topics_match_whole_words_only(question, topic):
    assert geo_feed._topic_for(question) == topic


def test_stored_topic_labels_are_re_derived_on_read(tmp_store):
    """A derived label must not freeze yesterday's classifier bug into history.

    The store is append-only, so a row written while the matcher was wrong
    keeps its wrong label forever unless the topic is recomputed from the
    question at read time. This writes a row with a deliberately wrong stored
    topic and asserts the reader ignores it.
    """
    store.write_snapshot("event_markets", pl.DataFrame([
        {"venue": "polymarket", "topic": "Oil",           # the wrong label
         "question": "Will Brentford win the 2026-27 EPL Championship?",
         "slug": "brentford-epl", "outcome": "Yes", "probability": 0.4,
         "liquidity": 1.0, "volume": 1.0, "end_date": "2027-05-30"},
        {"venue": "polymarket", "topic": "Nonsense",      # also wrong
         "question": "Will Crude Oil reach a new all-time high by December 31?",
         "slug": "crude-ath", "outcome": "Yes", "probability": 0.11,
         "liquidity": 1.0, "volume": 2.0, "end_date": "2026-12-31"},
    ]), key=["venue", "slug", "as_of"])

    odds = geo.event_odds()
    assert odds["available"]
    # The football market matches no topic now and drops out entirely.
    every = [m["question"] for markets in odds["topics"].values() for m in markets]
    assert not any("Brentford" in q for q in every)
    # The crude market is re-filed under Oil despite its stored label.
    assert "Oil" in odds["topics"]
    assert "Nonsense" not in odds["topics"]


@pytest.mark.parametrize(("label", "query"), geo_feed.THEMES)
def test_theme_queries_obey_gdelt_syntax(label, query):
    """GDELT reports a malformed query as a 200 with a plain-text body.

    That makes a syntax error look like a transport failure, which is how five
    of these ten queries returned nothing for days. The two rules it enforces
    are checkable without a network call, so they are checked here.
    """
    import re as _re

    # Rule 1: a quoted phrase must be at least five characters.
    for phrase in _re.findall(r'"([^"]*)"', query):
        assert len(phrase) >= 5, (
            f"{label}: quoted phrase {phrase!r} is {len(phrase)} chars; GDELT "
            "rejects anything under five")

    # Rule 2: OR'd terms must sit inside parentheses. Walk the string tracking
    # depth so a top-level OR is caught wherever it appears.
    depth = 0
    for token in _re.findall(r'\(|\)|\bOR\b', query):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        else:
            assert depth > 0, f"{label}: a top-level OR must be parenthesised"
    assert depth == 0, f"{label}: unbalanced parentheses"
