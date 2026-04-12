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


def format_slack_message(bridge_alerts, ggp_events, ggp_status, errors):
    """Format everything into a Slack message."""
    today_str = datetime.now().strftime("%A, %B %-d")
    blocks = []

    # Header
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"SF Road Closures — {today_str}"}
    })

    # GG Bridge section
    blocks.append({"type": "divider"})
    bridge_text = "*Golden Gate Bridge*\n"
    if isinstance(bridge_alerts, list) and bridge_alerts and isinstance(bridge_alerts[0], str):
        # Error case
        bridge_text += bridge_alerts[0]
    elif bridge_alerts:
        for alert in bridge_alerts:
            title = alert["title"]
            details = alert["details"]
            href = alert["href"]
            if href:
                bridge_text += f"\n• *<{href}|{title}>*\n"
            else:
                bridge_text += f"\n• *{title}*\n"
            for detail in details[:3]:
                bridge_text += f"  {detail[:200]}\n"
    else:
        bridge_text += "No current bridge alerts affecting cyclists."

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": bridge_text[:3000]}
    })

    # GGP section
    blocks.append({"type": "divider"})
    ggp_text = "*Golden Gate Park*\n"

    if ggp_status:
        # Summarize key status
        status_lines = ggp_status.split("\n")[:8]
        ggp_text += "_Current status:_\n"
        for line in status_lines:
            ggp_text += f"  {line}\n"

    if ggp_events:
        ggp_text += "\n_Upcoming closures:_\n"
        for event in ggp_events[:5]:
            ggp_text += f"• *{event['title']}*\n"
            for detail in event["details"][:3]:
                ggp_text += f"  {detail[:120]}\n"
    elif not ggp_status:
        ggp_text += "No upcoming GGP closure info found."

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": ggp_text[:3000]}
    })

    # Footer
    blocks.append({"type": "divider"})
    source_text = (
        "_Sources: "
        "<https://www.goldengate.org/service-alerts/|GG Bridge Alerts> · "
        "<https://sfrecpark.org/547/Golden-Gate-Park-Road-Closures|GGP Closures>_"
    )
    if errors:
        source_text += f"\n:warning: Errors: {'; '.join(errors)}"

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": source_text}]
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

    print(f"Found {len(bridge_alerts)} bridge alert categories, "
          f"{len(upcoming)} upcoming GGP events")

    message = format_slack_message(bridge_alerts, upcoming, ggp_status, errors)

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
