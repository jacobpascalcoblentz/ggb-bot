#!/usr/bin/env python3
"""SF Road Closure Bot for cycling club Slack.

Scrapes Golden Gate Bridge and Golden Gate Park closure info
and posts a morning summary to Slack.
"""

import json
import os
import re
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

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

        # Detect title lines: starts with date pattern, or is ALL CAPS and shortish
        has_date_prefix = bool(re.match(r"^\d{1,2}/\d{1,2}", text))
        is_allcaps_short = text == text.upper() and len(text) < 80
        is_title = has_date_prefix or (is_allcaps_short and len(text) > 10)

        # "SIDEWALKS CLOSED 5-9:30AM..." after a Mermaid Run title is a detail, not a new alert
        if is_title and current and any(kw in text.upper() for kw in ["CLOSED", "SHUTTLE"]):
            if any(kw in current["title"].upper() for kw in ["MERMAID", "RUN", "EVENT", "RACE"]):
                current["details"].append(text)
                if href and not current["href"]:
                    current["href"] = href
                continue

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
    """Fetch today's high tides from NOAA for Sausalito.

    Returns list of (time_str, height_ft) for highs >= TIDE_THRESHOLD_FT.
    """
    url = (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
        f"?date=today&station={TIDE_STATION}&product=predictions"
        "&datum=MLLW&time_zone=lst_ldt&units=english&format=json&interval=hilo"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"Tide API error: {e}", file=sys.stderr)
        return []

    high_tides = []
    for pred in data.get("predictions", []):
        if pred["type"] == "H":
            height = float(pred["v"])
            if height >= TIDE_THRESHOLD_FT:
                # Parse time like "2026-04-12 08:07"
                try:
                    t = datetime.strptime(pred["t"], "%Y-%m-%d %H:%M")
                    time_str = t.strftime("%-I:%M%p").lower()
                except ValueError:
                    time_str = pred["t"]
                high_tides.append((time_str, height))
    return high_tides


def get_sidewalk_closure_info(bridge_alerts):
    """If sidewalks are fully closed, return the time range. Otherwise None."""
    if not isinstance(bridge_alerts, list):
        return None
    for alert in bridge_alerts:
        if not isinstance(alert, dict):
            continue
        text = (alert["title"] + " " + " ".join(alert["details"])).upper()
        if "SIDEWALK" in text and "CLOSED" in text and "NARROWED" not in text:
            # Extract time range from title + details
            all_text = alert["title"] + " " + " ".join(alert["details"])
            time_match = re.search(r"\d{1,2}[:\.]?\d{0,2}\s*-\s*\d{1,2}[:\.]?\d{0,2}\s*[AaPp][Mm]", all_text)
            if time_match:
                return time_match.group(0).strip()
            return "check alert for times"
    return None


def format_slack_message(bridge_alerts, ggp_events, ggp_status, high_tides, errors):
    """Format Slack message. Returns None if nothing to report."""
    closure_time = get_sidewalk_closure_info(bridge_alerts)

    # Only post if there's something actionable
    if not closure_time and not high_tides and not ggp_events:
        return None

    blocks = []

    # Sidewalk closure — urgent
    if closure_time:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":rotating_light: <!channel> *GG Bridge sidewalks CLOSED {closure_time}* :rotating_light:\nBikes must take shuttle."}
        })

    # High tides — Sausalito bike path flooding
    if high_tides:
        tide_parts = [f"{height:.1f}ft at {time}" for time, height in high_tides]
        tide_text = ":ocean: *Sausalito bike path flood risk* — high tide " + ", ".join(tide_parts)
        if any(h >= 7.0 for _, h in high_tides):
            tide_text = f":ocean: <!channel> *King tide warning — Sausalito bike path likely flooded* — {', '.join(tide_parts)}"
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
          f"{len(high_tides)} high tides >= {TIDE_THRESHOLD_FT}ft")

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
