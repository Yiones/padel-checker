#!/usr/bin/env python3
"""Check Padelbase SALZBURG-INDOOR for weekday free blocks of 1h30 between 06:00-23:00, notify via ntfy."""

import datetime as dt
import os
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.buchung-padelbase.at/reservierung"
CLUB_ID = 5182
FIRST_WEEK = 1794200400
WEEK = 7 * 24 * 3600
DAYS_AHEAD = 14
WINDOW_START = dt.time(6, 0)
WINDOW_END = dt.time(23, 0)
MIN_MINUTES = 90
TZ = ZoneInfo("Europe/Vienna")
HEADERS = {"User-Agent": "Mozilla/5.0"}
NTFY_TOPIC = os.environ["NTFY_TOPIC"]


def fetch_week(week_start):
    r = requests.get(BASE_URL, params={"c": CLUB_ID, "t": week_start},
                     headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def parse_week(soup):
    header = soup.select_one(".day-head .day-courts")
    names = [c.get_text(strip=True) for c in header.select(".court")] if header else []

    slots = []
    for body in soup.select(".day-body"):
        for idx, court in enumerate(body.select(":scope > .court")):
            court_name = names[idx] if idx < len(names) else f"Court {idx + 1}"
            for slot in court.select(".slot.av"):
                slots.append({
                    "court": court_name,
                    "start": int(slot["data-begin"]),
                    "minutes": int(slot["data-size"]) * 30,
                })
    return slots


def merge_blocks(slots):
    slots.sort(key=lambda s: (s["court"], s["start"]))
    blocks = []
    for s in slots:
        end = s["start"] + s["minutes"] * 60
        if blocks and blocks[-1]["court"] == s["court"] and blocks[-1]["end"] == s["start"]:
            blocks[-1]["end"] = end
        else:
            blocks.append({"court": s["court"], "start": s["start"], "end": end})
    return blocks


def clip_to_window(block, now, horizon):
    start = dt.datetime.fromtimestamp(block["start"], TZ)
    end = dt.datetime.fromtimestamp(block["end"], TZ)

    if start.weekday() >= 5:          # 5 = Saturday, 6 = Sunday
        return None

    win_start = dt.datetime.combine(start.date(), WINDOW_START, tzinfo=TZ)
    win_end = dt.datetime.combine(start.date(), WINDOW_END, tzinfo=TZ)

    clipped_start = max(start, win_start, now)
    clipped_end = min(end, win_end)
    if clipped_start >= horizon:
        return None
    if (clipped_end - clipped_start) < dt.timedelta(minutes=MIN_MINUTES):
        return None

    return {"court": block["court"], "start": clipped_start, "end": clipped_end}


def notify(message):
    r = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=message.encode("utf-8"),
                      headers={"Title": "Padel 1h30 free"},
                      timeout=20)
    r.raise_for_status()


def main():
    now = dt.datetime.now(TZ)
    horizon = now + dt.timedelta(days=DAYS_AHEAD)

    slots = []
    for week_start in (FIRST_WEEK, FIRST_WEEK + WEEK):
        slots += parse_week(fetch_week(week_start))

    results = []
    for block in merge_blocks(slots):
        clipped = clip_to_window(block, now, horizon)
        if clipped:
            results.append(clipped)
    results.sort(key=lambda b: (b["start"], b["court"]))

    print(f"Found {len(results)} weekday block(s) of 1h30 or more.")
    if not results:
        return

    lines = []
    current_day = None
    for b in results:
        day = b["start"].date()
        if day != current_day:
            if lines:
                lines.append("")
            lines.append(f"{b['start']:%A %d.%m.%Y}")
            current_day = day
        lines.append(f"  {b['start']:%H:%M}-{b['end']:%H:%M}  {b['court']}")
        lines.append("")

    message = "\n".join(lines).rstrip()
    notify(f"Free 1h30 weekday slots 06:00-23:00\n\n{message}")


if __name__ == "__main__":
    main()
