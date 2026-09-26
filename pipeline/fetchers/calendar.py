"""The unified event calendar (SPEC §6.11).

Four kinds of event, each with a different provenance, and the page says which
is which because they are not equally reliable:

* **Scraped** — FOMC meeting dates, parsed from the Fed's own calendar page.
  There is no API; the page is stable HTML and the parse is narrow enough to
  fail loudly rather than silently return the wrong dates.
* **Computed** — quarterly and monthly options expiries, which are a rule
  (the last Friday) rather than a published list.
* **Configured** — anything in `config/calendar.yaml`, which is where a date
  that only a human knows about goes.
* **Derived from the store** — token unlocks, from `config/unlocks.yaml`.

Nothing here is guessed. The Bitcoin halving estimate is deliberately absent:
it depends on block height, which no source in this project fetches, and a
halving date interpolated from a calendar would be a fabricated number.
"""

from __future__ import annotations

import calendar as _calendar
import datetime as dt
import re
import time

import polars as pl

from pipeline import health, http, paths, store

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

MONTHS = {name.lower(): number for number, name in enumerate(_calendar.month_name)
          if name}


def _parse_fomc(html: str) -> list[dict]:
    """Pull (year, month, day-range) out of the Fed's calendar markup.

    The page groups meetings under a per-year panel heading and then repeats a
    month block and a date block per meeting. A meeting spanning a month
    boundary is written "28-1" with the month naming the first day, so the end
    day is only trusted when it is greater than the start.
    """
    events: list[dict] = []
    # Split on the year panels so a month block is attributed to the right year.
    panels = re.split(r'<a id="\d+">(\d{4})\s+FOMC Meetings</a>', html)
    for index in range(1, len(panels) - 1, 2):
        year = int(panels[index])
        block = panels[index + 1]
        pairs = re.findall(
            r'fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([A-Za-z]+)'
            r'[\s\S]{0,400}?fomc-meeting__date[^>]*>\s*([0-9]{1,2})(?:-([0-9]{1,2}))?',
            block)
        for month_name, start_day, end_day in pairs:
            month = MONTHS.get(month_name.strip().lower())
            if not month:
                continue
            try:
                start = dt.date(year, month, int(start_day))
            except ValueError:
                continue
            end = start
            if end_day and int(end_day) > int(start_day):
                try:
                    end = dt.date(year, month, int(end_day))
                except ValueError:
                    end = start
            # The decision lands on the final day of the meeting.
            events.append({
                "date": end.isoformat(),
                "title": "FOMC decision",
                "category": "monetary policy",
                "source": "federalreserve.gov",
                "provenance": "scraped",
                "detail": (f"Two-day meeting {start.isoformat()} to {end.isoformat()}"
                           if end != start else "One-day meeting"),
            })
    return events


def fomc_dates() -> list[dict]:
    started = time.perf_counter()
    try:
        response = http.request(FOMC_URL, cache_hours=24, timeout=60)
        events = _parse_fomc(response.text)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="federalreserve", endpoint="fomccalendars", dataset="FOMC dates",
            status="http_error", error=str(exc)[:200]))
        return []

    if not events:
        # The parse failing is a real failure, not an empty calendar. Saying so
        # is the difference between "no meetings" and "we stopped being able to
        # read the page", and only one of those is true.
        health.record(health.Record(
            source="federalreserve", endpoint="fomccalendars", dataset="FOMC dates",
            status="empty",
            error="the calendar page parsed to zero meetings; the markup changed"))
        return []

    health.record(health.Record(
        source="federalreserve", endpoint="fomccalendars", dataset="FOMC dates",
        status="ok", rows=len(events), as_of=max(e["date"] for e in events),
        expected_lag_days=400.0, archival=True,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return events


def option_expiries(months_ahead: int = 6) -> list[dict]:
    """Last Friday of each month; quarterlies are the big ones.

    Deribit settles monthly options on the last Friday at 08:00 UTC, and the
    March/June/September/December ones carry far more open interest. This is a
    rule, not a fetch, so it is computed rather than requested.
    """
    today = dt.date.today()
    events = []
    for offset in range(months_ahead + 1):
        month = today.month + offset
        year = today.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        last_day = _calendar.monthrange(year, month)[1]
        date = dt.date(year, month, last_day)
        while date.weekday() != 4:          # 4 = Friday
            date -= dt.timedelta(days=1)
        if date < today:
            continue
        quarterly = month in (3, 6, 9, 12)
        events.append({
            "date": date.isoformat(),
            "title": ("Quarterly options expiry" if quarterly
                      else "Monthly options expiry"),
            "category": "derivatives",
            "source": "computed (last Friday of the month)",
            "provenance": "computed",
            "detail": ("Deribit settles at 08:00 UTC. Quarterlies carry most of "
                       "the open interest. Computed from the rule, not read from "
                       "a listing, so an exchange moving a holiday expiry would "
                       "not show here." if quarterly
                       else "Deribit settles at 08:00 UTC. Computed from the "
                            "rule, not read from a listing."),
        })
    return events


def configured_events() -> list[dict]:
    """Anything a human put in config/calendar.yaml."""
    import yaml

    path = paths.CONFIG / "calendar.yaml"
    if not path.exists():
        return []
    try:
        loaded = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="config", endpoint="calendar.yaml", dataset="manual events",
            status="empty", error=f"could not parse: {exc}"[:200]))
        return []

    events = []
    for entry in loaded.get("events") or []:
        raw = str(entry.get("date", ""))
        try:
            dt.date.fromisoformat(raw)
        except ValueError:
            continue
        events.append({
            "date": raw,
            "title": str(entry.get("title", "")).strip(),
            "category": str(entry.get("category", "manual")),
            "source": "config/calendar.yaml",
            "provenance": "configured",
            "detail": str(entry.get("detail", "")),
        })
    return events


def unlock_events() -> list[dict]:
    """Forward unlocks from config. D007 leaves the file empty on purpose."""
    from pipeline import registry

    configured = registry.unlocks() or {}
    events = []
    for symbol, entries in (configured.get("unlocks") or {}).items():
        for entry in entries or []:
            raw = str(entry.get("date", ""))
            try:
                dt.date.fromisoformat(raw)
            except ValueError:
                continue
            share = entry.get("pct_of_float")
            events.append({
                "date": raw,
                "title": f"{symbol} unlock",
                "category": "supply",
                "source": "config/unlocks.yaml",
                "provenance": "configured",
                "detail": (f"{share}% of float" if share is not None
                           else "size not recorded"),
            })
    return events


def build() -> int:
    """Assemble the calendar and store it."""
    events = (fomc_dates() + option_expiries() + configured_events()
              + unlock_events())
    if not events:
        return 0
    frame = pl.DataFrame(events).unique(subset=["date", "title"], keep="last")
    return store.write_snapshot("calendar", frame, key=["date", "title"])


def fetch_all() -> dict[str, int]:
    return {"calendar": build()}


__all__ = ["build", "fetch_all", "fomc_dates", "option_expiries",
           "configured_events", "unlock_events", "_parse_fomc"]
