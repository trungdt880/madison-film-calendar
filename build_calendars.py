#!/usr/bin/env python3
"""Scrape UW Cinematheque and WUD Film (Marquee) schedules into .ics feeds.

Stdlib only, so it runs as-is on a GitHub Actions runner or any machine with Python 3.9+.

Outputs (in ./docs):
  cinematheque.ics  - https://cinema.wisc.edu/calendar/
  marquee.ics       - WUD Film screenings, https://union.wisc.edu/visit/union-south/the-marquee
  madison-film.ics  - both combined
"""
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
UA = "Mozilla/5.0 (madison-film-calendar; +https://github.com/)"
OUT_DIR = Path(__file__).resolve().parent / "docs"
DEFAULT_RUNTIME_MIN = 120
PAST_DAYS = 30        # keep recent screenings so the calendar has some history
FUTURE_DAYS = 365

CINEMA_CAL = "https://cinema.wisc.edu/calendar/"
CINEMA_AJAX = "https://cinema.wisc.edu/?evo-ajax=eventon_init_load"
CINEMA_REST = "https://cinema.wisc.edu/wp-json/wp/v2/ajde_events"
UNION_FEED = "https://union.wisc.edu/events-and-activities/event-calendar/feed"


def fetch(url, data=None, retries=3):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 - retry on any network error
            if attempt == retries - 1:
                raise
            print(f"  retry {url}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))


def strip_html(s):
    s = re.sub(r"<br\s*/?>|</p>\s*<p[^>]*>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def runtime_minutes(text):
    """Sum runtimes from metadata lines like 'USA | 2026 | DCP | 101 mins.'"""
    mins = [int(m) for m in re.findall(r"\|\s*(\d{1,3})\s*min", text or "", flags=re.I)]
    return sum(mins) or None


# --------------------------------------------------------------------------- Cinematheque
def flatten(prefix, value, out):
    """Encode nested dicts the way jQuery.ajax does: a[b][c]=v."""
    if isinstance(value, dict):
        for k, v in value.items():
            flatten(f"{prefix}[{k}]", v, out)
    else:
        out.append((prefix, value))


def cinematheque_events():
    page = fetch(CINEMA_CAL)
    sc = json.loads(html.unescape(re.search(r"evo_cal_data' data-sc=\"([^\"]*)\"", page).group(1)))
    params = json.loads(re.search(r"evo_general_params = (\{.*?\});", page).group(1))

    now = int(time.time())
    sc.update(
        focus_start_date_range=str(now - PAST_DAYS * 86400),
        focus_end_date_range=str(now + FUTURE_DAYS * 86400),
        number_of_months=str(FUTURE_DAYS // 30 + 2),
        hide_past="no",
    )
    form = []
    flatten("cals[cal][sc]", sc, form)
    form.append(("nonce", params["n"]))
    data = json.loads(fetch(CINEMA_AJAX, data=form))
    raw = data["cals"]["cal"]["json"]

    # Descriptions/links come from the WP REST API in one batched call.
    ids = sorted({str(e["event_id"]) for e in raw})
    posts = {}
    for i in range(0, len(ids), 100):
        q = urllib.parse.urlencode({"include": ",".join(ids[i:i + 100]), "per_page": 100,
                                    "_fields": "id,link,content"})
        for p in json.loads(fetch(f"{CINEMA_REST}?{q}")):
            posts[str(p["id"])] = p

    events = []
    for e in raw:
        pmv = e.get("event_pmv", {})
        post = posts.get(str(e["event_id"]), {})
        desc_html = post.get("content", {}).get("rendered", "")
        desc = strip_html(desc_html)
        start = dt.datetime.fromtimestamp(int(e["unix_start"]), tz=dt.timezone.utc)
        hide_end = pmv.get("evo_hide_endtime", ["no"])[0] == "yes"
        if hide_end or int(e["unix_end"]) <= int(e["unix_start"]):
            end = start + dt.timedelta(minutes=runtime_minutes(desc_html) or DEFAULT_RUNTIME_MIN)
        else:
            end = dt.datetime.fromtimestamp(int(e["unix_end"]), tz=dt.timezone.utc)
        loc_name = (pmv.get("evcal_location_name") or [""])[0].strip()
        loc_addr = (pmv.get("evcal_location") or [""])[0].strip()
        events.append({
            "uid": f"cinematheque-{e['event_id']}-{e.get('ri', '0')}-{int(e['unix_start'])}@cinema.wisc.edu",
            "title": html.unescape(e["event_title"]).strip(),
            "start": start,
            "end": end,
            "location": ", ".join(x for x in (loc_name, loc_addr) if x) or "UW Cinematheque",
            "url": post.get("link", ""),
            "description": desc,
            "source": "UW Cinematheque",
        })
    return events


# --------------------------------------------------------------------------- WUD Film / Marquee
def marquee_events():
    raw = fetch(UNION_FEED)
    # The feed's root element is malformed (<xml version=...>), so parse each <event> on its own.
    events = []
    for chunk in re.findall(r"<event>.*?</event>", raw, flags=re.S):
        try:
            node = ET.fromstring(chunk)
        except ET.ParseError:
            node = ET.fromstring(re.sub(r"&(?!\w+;|#\d+;)", "&amp;", chunk))
        g = lambda tag: html.unescape((node.findtext(tag) or "").strip())  # noqa: E731
        title = g("event_title")
        if g("category") != "Film" or not title.lower().startswith("wud film presents"):
            continue  # skip committee meetings and non-film events
        day = dt.date.fromisoformat(g("event_start_date"))
        start = dt.datetime.combine(day, dt.time.fromisoformat(g("event_start_time")), TZ)
        end_t = g("event_end_time")
        end = dt.datetime.combine(day, dt.time.fromisoformat(end_t), TZ) if end_t else None
        if end is None or end <= start:
            end = (end + dt.timedelta(days=1)) if end else start + dt.timedelta(minutes=DEFAULT_RUNTIME_MIN)
        room = g("room")
        place = ", ".join(x for x in (room if room != "See Event Details" else "", g("location")) if x)
        desc = strip_html(g("description")) or g("short_description")
        if g("cost"):
            desc = f"Cost: {g('cost')}\n\n{desc}"
        events.append({
            "uid": f"wud-{g('event_id')}-{start.strftime('%Y%m%dT%H%M')}@union.wisc.edu",
            "title": re.sub(r"^WUD Film Presents:\s*", "", title, flags=re.I),
            "start": start.astimezone(dt.timezone.utc),
            "end": end.astimezone(dt.timezone.utc),
            "location": place,
            "url": g("url"),
            "description": desc,
            "source": "WUD Film (The Marquee)",
        })
    return events


# --------------------------------------------------------------------------- iCalendar
def ics_escape(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def fold(line):
    """RFC 5545: lines over 75 octets are folded with CRLF + space."""
    out, buf = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(buf) + len(b) > 74:
            out.append(buf.decode("utf-8"))
            buf = b" "
        buf += b
    out.append(buf.decode("utf-8"))
    return "\r\n".join(out)


def write_ics(path, name, events):
    fmt = lambda d: d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//madison-film-calendar//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(name)}", "X-WR-TIMEZONE:America/Chicago",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for e in sorted(events, key=lambda e: (e["start"], e["title"])):
        desc = "\n\n".join(x for x in (e["description"], e["url"], f"Source: {e['source']}") if x)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            # Deterministic DTSTAMP keeps output byte-identical when nothing changed (no noisy commits).
            f"DTSTAMP:{fmt(e['start'])}",
            f"DTSTART:{fmt(e['start'])}",
            f"DTEND:{fmt(e['end'])}",
            f"SUMMARY:{ics_escape(e['title'])}",
            f"LOCATION:{ics_escape(e['location'])}",
            f"DESCRIPTION:{ics_escape(desc)}",
        ]
        if e["url"]:
            lines.append(f"URL:{e['url']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    path.write_text("\r\n".join(fold(l) for l in lines) + "\r\n", encoding="utf-8")
    print(f"wrote {path.name}: {len(events)} events")


def main():
    OUT_DIR.mkdir(exist_ok=True)
    sources = {"cinematheque": ("UW Cinematheque", cinematheque_events),
               "marquee": ("WUD Film at the Marquee", marquee_events)}
    collected, failed = {}, []
    for key, (name, fn) in sources.items():
        try:
            evs = fn()
            if not evs:
                raise RuntimeError("0 events parsed (page layout may have changed)")
            collected[key] = evs
            write_ics(OUT_DIR / f"{key}.ics", name, evs)
        except Exception as e:  # noqa: BLE001 - keep the last good file if one source breaks
            failed.append(key)
            print(f"ERROR {key}: {e!r} - keeping previous {key}.ics", file=sys.stderr)
    # Only rewrite the combined feed when every source succeeded, so one broken site never empties it.
    if not failed:
        combined = [e for evs in collected.values() for e in evs]
        write_ics(OUT_DIR / "madison-film.ics", "Madison Campus Film", combined)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
