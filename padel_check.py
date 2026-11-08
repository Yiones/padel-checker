#!/usr/bin/env python3
"""Check Padelbase SALZBURG-INDOOR for free weekday slots 18:00-22:00 and notify via ntfy."""

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
WINDOW_START = dt.time(18, 0)
WINDOW_END = dt.time(22, 0)
TZ = ZoneInfo("Europe/Vienna")
HEADERS = {"User-Agent": "Mozilla/5.0"}
NTFY_TOPIC = os.environ["NTFY_TOPIC"]


def fetch_week(week_start):
    r = requests.get(BASE_URL, params={"c": CLUB_ID, "t": week_start},
                     headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def parse_week(soup):
    prices = {}
    for el in soup.select(".pricebox .price"):
        for cls in el.get("class", []):
            if cls.startswith("price") and cls != "price":
                prices[cls] = el.get_text(strip=True)

    header = soup.select_one(".day-head .day-courts")
    names = [c.get_text(strip=True) for c in header.select(".court")] if header else []

    slots = []
    for body in soup.select(".day-body"):
        for idx, court in enumerate(body.select(":scope > .court")):
            court_name = names[idx] if idx < len(names) else f"Court {idx + 1}"
            for slot in court.select(".slot.av"):
                price_cls = next((c for c in slot.get("class", [])
                                  if c.startswith("price")), None)
                slots.append({
                    "court": court_name,
                    "start": int(slot["data-begin"]),
                    "minutes": int(slot["data-size"]) * 30,
                    "price": prices.get(price_cls, "?"),
                })
    return slots


def merge_blocks(slots):
    slots.sort(key=lambda s: (s["court"], s["start"]))
    blocks = []
    for s in slots:
        end = s["start"] + s["minutes"] * 60
        if (blocks and blocks[-1]["court"] == s["court"]
                and blocks[-1]["price"] == s["price"]
                and blocks[-1]["end"] == s["start"]):
            blocks[-1]["end"] = end
        else:
            blocks.append({"court": s["court"], "price": s["price"],
                           "start": s["start"], "end": end})
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
    if clipped_end <= clipped_start or clipped_start >= horizon:
        return None

    return {"court": block["court"], "price": block["price"],
            "start": clipped_start, "end": clipped_end}


def notify(message):
    r = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=message.encode("utf-8"),
                      headers={"Title": "Padel slot free"},
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

    print(f"Found {len(results)} free weekday slot(s) in 18:00-22:00.")
    if not results:
        return

    lines = [f"{b['start']:%a %d.%m} {b['start']:%H:%M}-{b['end']:%H:%M} "
             f"{b['court']} {b['price']}" for b in results]
    notify("\n".join(lines))
    print("Notification sent.")


if __name__ == "__main__":
    main()
