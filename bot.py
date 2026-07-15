#!/usr/bin/env python3
"""SF Road Closure Bot for cycling club Slack.

Scrapes Golden Gate Bridge and Golden Gate Park closure info
and posts a morning summary to Slack.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# NOAA station 9414806 = Sausalito
TIDE_STATION = "9414806"
TIDE_THRESHOLD_FT = 6.0


def scrape_gg_bridge_alerts():
    """Scrape Golden Gate Bridge service alerts page for bridge-related closures.

    Returns a list of (category, [(text, href), ...]) tuples.
    Focuses on sidewalk/cyclist-relevant alerts.
    """
    url = "https://www.goldengate.org/service-alerts/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        return [f"Failed to fetch GG Bridge alerts: {e}"]

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the "Bridge Alerts" or "Bridge Events" accordion toggles
    bridge_content = []
    for toggle in soup.select(".accordion-toggle"):
        text = toggle.get_text(strip=True)
        if text.startswith("Bridge"):
            sibling = toggle.find_next_sibling("div")
            if sibling:
                bridge_content.append(sibling)

    if not bridge_content:
        return []

    # Parse all <p> and <li> elements from the bridge sections.
    # Group them into alerts: an alert starts with an ALL-CAPS or bold title line,
    # followed by detail lines.
    raw_items = []
    for container in bridge_content:
        for el in container.find_all(["p", "li"]):
            text = el.get_text(" ", strip=True)
            if not text or len(text) < 5:
                continue
            # Skip pure navigation links
            clean = text.upper().replace(" ", "").replace(">", "").replace("<", "")
            if clean in ("READMORE", "READMOREHERE", "HERE", "MOREINFO"):
                continue
            # Clean trailing "READ MORE" etc from text
            for suffix in ["> More here <", "READ MORE HERE", "READ MORE", "MORE INFO", "> Here <"]:
                if text.endswith(suffix):
                    text = text[: -len(suffix)].strip()
            # Grab best link
            link = el.find("a", href=True)
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"https://www.goldengate.org{href}"
            raw_items.append((text, href))

    # Group into alerts. Title lines typically:
    # - Start with a date pattern like "4/12" or "4/13 - 4/17"
    # - Are ALL CAPS and short
    # - Contain key category words like "Weeknight Lane Closures"
    # Detail lines are longer sentences/descriptions.
    alerts = []
    current = None
    seen_text = set()

    for text, href in raw_items:
        # Deduplicate
        if text in seen_text:
            continue
        seen_text.add(text)

        # A line starts a new alert only if it carries a date prefix, or if it's
        # a short ALL-CAPS heading before any alert has opened. All-caps lines
        # after an open alert are continuations of it (the site splits one
        # alert across lines, e.g. "DUE TO SF MARATHON. BIKE SHUTTLES WILL BE
        # PROVIDED." following a dated closure title).
        has_date_prefix = bool(re.match(r"^\d{1,2}/\d{1,2}", text))
        is_allcaps_short = text == text.upper() and 10 < len(text) < 80
        is_title = has_date_prefix or (is_allcaps_short and current is None)

        if is_title:
            if current:
                alerts.append(current)
            current = {"title": text, "details": [], "href": href}
        elif current:
            current["details"].append(text)
            if href and not current["href"]:
                current["href"] = href

    if current:
        alerts.append(current)

    # Only keep alerts about sidewalk closures/restrictions and bike detours.
    # Filter on the title — detail text is too generic ("bicyclists" appears everywhere).
    sidewalk_keywords = ["sidewalk", "bike", "cyclist", "trail"]
    filtered = [
        a for a in alerts
        if any(kw in a["title"].lower() for kw in sidewalk_keywords)
    ]
    return filtered


def scrape_ggp_event_closures():
    """Scrape Golden Gate Park event-based road closures."""
    url = "https://sfrecpark.org/547/Golden-Gate-Park-Road-Closures"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        return [], [f"Failed to fetch GGP closures: {e}"]

    soup = BeautifulSoup(resp.text, "html.parser")

    # Parse upcoming events from the page content
    today = datetime.now()
    events = []

    # The page lists events with dates and road closures in prose format.
    # Look for the main content area.
    content = soup.select_one("#divContent, .fr-view, .interior-content, main, article")
    if not content:
        content = soup

    text_blocks = content.get_text(separator="\n").split("\n")
    current_event = None

    for line in text_blocks:
        line = line.strip()
        if not line:
            continue

        # Try to detect event headers (usually have a date like "January 11, 2026")
        has_date = False
        for month in [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]:
            if month in line:
                has_date = True
                break

        if has_date and len(line) < 200:
            current_event = {"title": line, "details": []}
            events.append(current_event)
        elif current_event and line and len(line) > 10:
            current_event["details"].append(line)

    return events, []


def scrape_ggp_current_status():
    """Scrape current GGP route status (car-free roads, etc.)."""
    url = "https://sfrecpark.org/1575/Current-Route-Details"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        return None, str(e)

    soup = BeautifulSoup(resp.text, "html.parser")
    content = soup.select_one("#divContent, .fr-view, .interior-content, main, article")
    if not content:
        content = soup

    text = content.get_text(separator="\n", strip=True)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Extract key facts about what's car-free vs open to traffic
    car_free = []
    shared = []
    status_note = None

    for line in lines:
        lower = line.lower()
        if "closed to vehicle" in lower or "car-free" in lower and "24" in lower:
            status_note = line
        # Look for road names with their status
        road_keywords = ["jfk", "mlk", "conservatory", "overlook", "middle drive",
                         "transverse", "chain of lakes", "nancy pelosi", "lincoln",
                         "polo field", "great highway"]
        for kw in road_keywords:
            if kw in lower:
                if any(x in lower for x in ["car-free", "closed to vehicle"]):
                    car_free.append(line)
                elif any(x in lower for x in ["shared", "open to", "regular traffic"]):
                    shared.append(line)
                break

    summary_parts = []
    if status_note:
        summary_parts.append(status_note)
    if car_free:
        summary_parts.append("Car-free: JFK Promenade, Conservatory Dr, Overlook Dr, Middle Dr, MLK Dr")
    if shared:
        summary_parts.append("Open to cars: Transverse Dr, Chain of Lakes Dr, Nancy Pelosi Dr, Lincoln Way")
    if not summary_parts:
        # Fallback — just grab the first useful lines
        for line in lines:
            if any(kw in line.lower() for kw in ["car-free", "route", "jfk", "closed"]):
                summary_parts.append(line)
                if len(summary_parts) >= 3:
                    break

    result = "\n".join(summary_parts) if summary_parts else "Could not parse current route status."
    return result, None


def filter_upcoming_events(events, days_ahead=30):
    """Filter events to only show upcoming ones within N days."""
    today = datetime.now()
    upcoming = []

    for event in events:
        title = event["title"]
        # Try to parse a date from the title
        event_date = None
        for fmt in ["%B %d, %Y", "%B %d %Y", "%b %d, %Y"]:
            for month in [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December",
            ]:
                if month in title:
                    try:
                        # Extract just the date portion
                        idx = title.index(month)
                        date_str = title[idx:].strip().rstrip(")")
                        # Handle ranges like "August 7-9, 2026"
                        date_str = date_str.split("-")[0].split("–")[0].strip()
                        if "," not in date_str:
                            date_str = date_str.rsplit(" ", 1)
                            if len(date_str) == 2:
                                date_str = f"{date_str[0]}, {date_str[1]}"
                            else:
                                date_str = date_str[0]
                        else:
                            date_str = date_str
                        event_date = datetime.strptime(date_str, "%B %d, %Y")
                        break
                    except (ValueError, IndexError):
                        continue
            if event_date:
                break

        if event_date:
            diff = (event_date - today).days
            if -1 <= diff <= days_ahead:
                upcoming.append(event)
        else:
            # Can't parse date — include it anyway
            upcoming.append(event)

    return upcoming


def fetch_high_tides():
    """Fetch today's and tomorrow's tide predictions from NOAA for Sausalito.

    Returns list of dicts with keys: day ("today" or "tomorrow"), start, end,
    peak_height, peak_time for periods where tide >= TIDE_THRESHOLD_FT
    during 6am-6pm.
    """
    RIDE_HOURS_START = 6   # 6am
    RIDE_HOURS_END = 18    # 6pm

    today = datetime.now().date()
    url = (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
        f"?begin_date={today.strftime('%Y%m%d')}&range=48"
        f"&station={TIDE_STATION}&product=predictions"
        "&datum=MLLW&time_zone=lst_ldt&units=english&format=json"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"Tide API error: {e}", file=sys.stderr)
        return []

    # Parse all predictions into (datetime, height) pairs
    predictions = []
    for pred in data.get("predictions", []):
        try:
            t = datetime.strptime(pred["t"], "%Y-%m-%d %H:%M")
            h = float(pred["v"])
            predictions.append((t, h))
        except (ValueError, KeyError):
            continue

    if not predictions:
        return []

    # Find contiguous intervals above threshold
    intervals = []
    in_interval = False
    for t, h in predictions:
        if h >= TIDE_THRESHOLD_FT and not in_interval:
            in_interval = True
            interval = {"start": t, "end": t, "peak_height": h, "peak_time": t}
        elif h >= TIDE_THRESHOLD_FT and in_interval:
            interval["end"] = t
            if h > interval["peak_height"]:
                interval["peak_height"] = h
                interval["peak_time"] = t
        elif h < TIDE_THRESHOLD_FT and in_interval:
            in_interval = False
            intervals.append(interval)
    if in_interval:
        intervals.append(interval)

    # Filter to intervals that overlap with ride hours (6am-6pm)
    def fmt_time(dt):
        return dt.strftime("%-I:%M%p").lower()

    tomorrow = today + timedelta(days=1)
    results = []
    for iv in intervals:
        # Skip intervals entirely outside ride hours
        if iv["end"].hour < RIDE_HOURS_START or iv["start"].hour >= RIDE_HOURS_END:
            continue
        if iv["start"].date() == today:
            day = "today"
        elif iv["start"].date() == tomorrow:
            day = "tomorrow"
        else:
            continue
        results.append({
            "day": day,
            "start": fmt_time(iv["start"]),
            "end": fmt_time(iv["end"]),
            "peak_height": iv["peak_height"],
            "peak_time": fmt_time(iv["peak_time"]),
        })

    return results


def parse_alert_with_llm(alert):
    """Use Haiku 4.5 to parse a bridge alert into structured data.

    Returns dict with keys: start_date, end_date, affects_daytime_cyclists,
    is_closure, summary. Returns None on failure.
    """
    import anthropic

    raw = alert["title"]
    if alert["details"]:
        raw += "\n" + "\n".join(alert["details"])

    today_str = datetime.now().strftime("%Y-%m-%d")
    prompt = (
        "You are parsing Golden Gate Bridge alerts for a cycling club Slack bot. "
        "Members ride during the day (roughly 6am-6pm). We only care about alerts "
        "that affect DAYTIME cycling — sidewalk closures, bike path closures, "
        "or detours happening between 6am and 6pm.\n\n"
        "Given the raw alert text, extract:\n"
        '- start_date: first day the alert applies (as "YYYY-MM-DD"), or null '
        "if the text has no date\n"
        '- end_date: last day the alert applies (as "YYYY-MM-DD"), equal to '
        "start_date for single-day alerts, or null if the text has no date\n"
        "- affects_daytime_cyclists: true ONLY if the alert involves a sidewalk/bike "
        "closure or detour happening during daytime hours (roughly 6am-6pm). "
        "Set to FALSE for overnight/weeknight lane closures, even if they mention "
        "bike detours — those don't affect daytime riders.\n"
        "- is_closure: true if fully closed (not just narrowed/restricted)\n"
        "- summary: a short, plain-English summary for cyclists (1-2 sentences). "
        "Include dates, times, and which sidewalk. Be direct and actionable.\n\n"
        f"Today is {today_str}. Assume current year for dates, but if that "
        "date has already passed this year, assume next year.\n\n"
        "Respond with JSON only, no explanation.\n\n"
        f"Alert: {raw}"
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Strip markdown code fencing if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text)
    except Exception as e:
        print(f"LLM parse error: {e}", file=sys.stderr)
        return None



def classify_bridge_alerts(bridge_alerts):
    """Split bridge alerts into today's closures and upcoming ones.

    Uses Haiku 4.5 to parse alert dates and determine relevance.
    Returns (today_alerts, upcoming_alerts).
    """
    if not isinstance(bridge_alerts, list):
        return [], []

    today = datetime.now().date()
    today_alerts = []
    upcoming_alerts = []

    for alert in bridge_alerts:
        if not isinstance(alert, dict):
            continue

        parsed = parse_alert_with_llm(alert)
        if not parsed or not parsed.get("affects_daytime_cyclists"):
            continue

        def to_date(value):
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return None

        start = to_date(parsed.get("start_date"))
        end = to_date(parsed.get("end_date"))

        info = {
            "alert": alert,
            "parsed": parsed,
            "start": start,
            "end": end,
        }

        tomorrow = today + timedelta(days=1)
        if start and end:
            if start <= today <= end:
                info["when"] = "today"
                today_alerts.append(info)
            elif start <= tomorrow <= end:
                info["when"] = "tomorrow"
                upcoming_alerts.append(info)
        else:
            # Can't parse date — heads up without @channel rather than
            # paging the channel every day the alert stays on the page
            info["when"] = "undated"
            upcoming_alerts.append(info)

    return today_alerts, upcoming_alerts


def format_slack_message(bridge_alerts, ggp_events, ggp_status, high_tides, errors):
    """Format Slack message. Returns None if nothing to report."""
    today = datetime.now().date()
    today_closures, upcoming_closures = classify_bridge_alerts(bridge_alerts)

    # Only post if there's something actionable
    if not today_closures and not upcoming_closures and not high_tides and not ggp_events:
        return None

    blocks = []

    # Today's sidewalk closures — @channel
    for info in today_closures:
        summary = info["parsed"].get("summary", info["alert"]["title"])
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":rotating_light: <!channel> *Golden Gate Bridge alert:*\n{summary}"}
        })

    # Tomorrow's and undated closures — heads up, no @channel
    for info in upcoming_closures:
        summary = info["parsed"].get("summary", info["alert"]["title"])
        header = "tomorrow" if info["when"] == "tomorrow" else "heads up"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":bridge_at_night: *Golden Gate Bridge — {header}:*\n{summary}"}
        })

    # High tides — Sausalito bike path flooding
    def tide_part(iv):
        return (
            f"above {TIDE_THRESHOLD_FT:.0f}ft from {iv['start']} to {iv['end']} "
            f"(peak {iv['peak_height']:.1f}ft at {iv['peak_time']})"
        )

    today_tides = [iv for iv in high_tides if iv["day"] == "today"]
    tomorrow_tides = [iv for iv in high_tides if iv["day"] == "tomorrow"]

    if today_tides:
        is_king = any(iv["peak_height"] > 6.5 for iv in today_tides)
        if is_king:
            tide_text = ":ocean: <!channel> *King tide warning — Sausalito bike path likely flooded*\n" + "\n".join(f"• {tide_part(iv)}" for iv in today_tides)
        else:
            tide_text = ":ocean: *Sausalito bike path flood risk*\n" + "\n".join(f"• Tide {tide_part(iv)}" for iv in today_tides)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": tide_text}
        })

    # Tomorrow's tides are a heads up with no @channel regardless of height
    if tomorrow_tides:
        tide_text = ":ocean: *Sausalito bike path flood risk — tomorrow*\n" + "\n".join(f"• Tide {tide_part(iv)}" for iv in tomorrow_tides)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": tide_text}
        })

    # GGP upcoming closures
    if ggp_events:
        blocks.append({"type": "divider"})
        ggp_text = "*Golden Gate Park — upcoming closures:*\n"
        for event in ggp_events[:5]:
            ggp_text += f"• *{event['title']}*\n"
            for detail in event["details"][:2]:
                ggp_text += f"  {detail[:120]}\n"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": ggp_text[:3000]}
        })

    return {"blocks": blocks}


def post_to_slack(message):
    """Post message to Slack via incoming webhook."""
    if not SLACK_WEBHOOK_URL:
        print("No SLACK_WEBHOOK_URL set. Printing message instead:\n")
        print(json.dumps(message, indent=2))
        return False

    resp = requests.post(
        SLACK_WEBHOOK_URL,
        json=message,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"Slack post failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        return False
    return True


def main():
    errors = []

    print("Scraping GG Bridge alerts...")
    bridge_alerts = scrape_gg_bridge_alerts()

    print("Scraping GGP closures...")
    ggp_events, ggp_errors = scrape_ggp_event_closures()
    errors.extend(ggp_errors)

    print("Scraping GGP current status...")
    ggp_status, status_err = scrape_ggp_current_status()
    if status_err:
        errors.append(status_err)

    # Filter to upcoming events (next 30 days)
    upcoming = filter_upcoming_events(ggp_events, days_ahead=30)

    print("Checking Sausalito tides...")
    high_tides = fetch_high_tides()

    print(f"Found {len(bridge_alerts)} bridge alerts, "
          f"{len(upcoming)} upcoming GGP events, "
          f"{len(high_tides)} high tide periods >= {TIDE_THRESHOLD_FT}ft during ride hours")

    message = format_slack_message(bridge_alerts, upcoming, ggp_status, high_tides, errors)

    if message is None:
        print("Nothing to report today. No Slack post.")
        return

    if "--dry-run" in sys.argv:
        print("\n--- DRY RUN ---")
        print(json.dumps(message, indent=2))
    else:
        if post_to_slack(message):
            print("Posted to Slack.")
        else:
            print("Slack post skipped or failed.")


if __name__ == "__main__":
    main()
