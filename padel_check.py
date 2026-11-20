#!/usr/bin/env python3
"""Check Padelbase SALZBURG-INDOOR for weekday free blocks, save them, and notify when a saved block is taken."""

import datetime as dt
import json
import os
from pathlib import Path
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
MIN_MINUTES = 90
TZ = ZoneInfo("Europe/Vienna")
HEADERS = {"User-Agent": "Mozilla/5.0"}
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
STATE_FILE = Path("slots_state.json")


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


def block_key(block):
    return f"{block['court']}|{block['start']}|{block['end']}"


def qualifies(block, now, horizon):
    """Return the part of a weekday block inside the window, if it is at least MIN_MINUTES long."""
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
    return clipped_start, clipped_end


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return []


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=1))


def notify(title, message):
    r = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=message.encode("utf-8"),
                      headers={"Title": title},
                      timeout=20)
    r.raise_for_status()


def format_lines(items):
    """items: list of (start_dt, end_dt, court) sorted by time."""
    lines = []
    current_day = None
    for start, end, court in items:
        if start.date() != current_day:
            if lines:
                lines.append("")
            lines.append(f"{start:%A %d.%m.%Y}")
            current_day = start.date()
        lines.append(f"  {start:%H:%M}-{end:%H:%M}  {court}")
    return "\n".join(lines).rstrip()


def main():
    now = dt.datetime.now(TZ)
    horizon = now + dt.timedelta(days=DAYS_AHEAD)

    slots = []
    for week_start in (FIRST_WEEK, FIRST_WEEK + WEEK):
        slots += parse_week(fetch_week(week_start))

    # Safety: if the page came back empty, don't treat every saved slot as taken
    if not slots:
        print("No slot data found on the page. State not changed.")
        return

    raw_blocks = merge_blocks(slots)

    # Every free block in the next 14 days, regardless of window (used to decide "still free")
    current_keys = {
        block_key(b) for b in raw_blocks
        if b["end"] > now.timestamp() and b["start"] < horizon.timestamp()
    }

    # Blocks that match the window and minimum length, saved for the next run
    new_state = []
    for b in raw_blocks:
        result = qualifies(b, now, horizon)
        if result:
            clipped_start, clipped_end = result
            new_state.append({
                "key": block_key(b),
                "court": b["court"],
                "start": int(clipped_start.timestamp()),
                "end": int(clipped_end.timestamp()),
            })

    previous = load_state()
    previous_keys = {p["key"] for p in previous}

    # 1) Newly free: in the current state but not in the saved one
    newly_free = sorted((b for b in new_state if b["key"] not in previous_keys),
                        key=lambda b: (b["start"], b["court"]))

    # 2) Taken: saved earlier, still in the future, but no longer free
    taken = sorted((p for p in previous
                    if p["end"] > now.timestamp() and p["key"] not in current_keys),
                   key=lambda p: (p["start"], p["court"]))

    print(f"Saved: {len(previous)}, now free: {len(new_state)}, "
          f"newly free: {len(newly_free)}, taken: {len(taken)}")

    if newly_free:
        items = [(dt.datetime.fromtimestamp(b["start"], TZ),
                  dt.datetime.fromtimestamp(b["end"], TZ),
                  b["court"]) for b in newly_free]
        notify("Padel slot free", "Now free:\n\n" + format_lines(items))

    if taken:
        items = [(dt.datetime.fromtimestamp(p["start"], TZ),
                  dt.datetime.fromtimestamp(p["end"], TZ),
                  p["court"]) for p in taken]
        notify("Padel slot taken", "No longer free:\n\n" + format_lines(items))

    save_state([{k: b[k] for k in ("key", "court", "start", "end")} for b in new_state])


if __name__ == "__main__":
    main()
