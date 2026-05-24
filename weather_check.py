#!/usr/bin/env python3
"""
Deck Cushion Rain Alert
-----------------------
Checks the Open-Meteo forecast for Mansfield, MA and sends a Gmail alert
only when the rain status CHANGES:
  - Clear → Rain coming : "Bring in the cushions"
  - Rain coming → Clear : "All clear — cushions can go back out"
  - No change           : Silent

State is persisted in state.json and committed back to the repo by the
GitHub Actions workflow after each run.

Schedule:
  Every 3 hours, 3 AM–9 PM ET. Runs May–October only.
Setup: See README.md
"""

import os
import smtplib
import sys
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from itertools import groupby
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

# Mansfield, MA coordinates
LATITUDE  = 42.0334
LONGITUDE = -71.2170
TIMEZONE  = "America/New_York"

# Rain probability threshold (%)
RAIN_THRESHOLD = 10

# Active months (1=Jan, 12=Dec) — cushion season only
ACTIVE_MONTHS = range(5, 11)  # May through October

# State file — committed to repo, tracks last known rain status
STATE_FILE = Path(__file__).parent / "state.json"

# Email recipients — set via environment variables, never hardcoded
PRIMARY_RECIPIENT   = os.environ.get("ALERT_PRIMARY_EMAIL", "")
SECONDARY_RECIPIENT = os.environ.get("ALERT_SECONDARY_EMAIL", "")

# Set INCLUDE_SECONDARY = True after your trial period
INCLUDE_SECONDARY = True

# Gmail SMTP — credentials set via environment variables, never hardcoded
GMAIL_SENDER   = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")


# ---------------------------------------------------------------------------
# STATE MANAGEMENT
# ---------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"rain_alert_active": False, "last_run": None}


def save_state(rain_alert_active):
    state = {
        "rain_alert_active": rain_alert_active,
        "last_run": datetime.now(timezone.utc).isoformat()
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"State saved: rain_alert_active={rain_alert_active}")


# ---------------------------------------------------------------------------
# WEATHER
# ---------------------------------------------------------------------------

def get_check_window():
    """
    Return (window_start, window_end, label) in UTC based on current local hour.
    Morning run territory (5 AM–2 PM ET): check through 8 PM same day.
    Evening run territory (2 PM–5 AM ET): check through 6 AM next day.
    Wide bands absorb multi-hour GitHub Actions scheduling delays.
    """
    et_offset = timedelta(hours=-4)  # EDT (May–Oct is always EDT, not EST)
    now_utc = datetime.now(timezone.utc)
    now_et  = now_utc + et_offset
    hour_et = now_et.hour

    if 5 <= hour_et < 14:
        window_end_et = now_et.replace(hour=20, minute=0, second=0, microsecond=0)
        label = "today"
    else:
        tomorrow_et   = now_et + timedelta(days=1)
        window_end_et = tomorrow_et.replace(hour=6, minute=0, second=0, microsecond=0)
        label = "overnight"

    window_start_utc = now_utc
    window_end_utc   = window_end_et - et_offset
    return window_start_utc, window_end_utc, label


def fetch_forecast():
    """Fetch hourly precipitation_probability from Open-Meteo (no API key needed)."""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&hourly=precipitation_probability"
        f"&timezone={TIMEZONE}"
        f"&forecast_days=3"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


def parse_rain_in_window(forecast, window_start, window_end):
    """
    Return (max_prob, peak_label, first_label) for hours in the window.
    first_label is the earliest hour exceeding RAIN_THRESHOLD, or None.
    """
    times = forecast["hourly"]["time"]
    probs = forecast["hourly"]["precipitation_probability"]

    max_prob    = 0
    peak_label  = None
    first_label = None
    et_offset   = timedelta(hours=-4)

    for time_str, prob in zip(times, probs):
        local_dt = datetime.fromisoformat(time_str)
        utc_dt   = local_dt.replace(tzinfo=timezone.utc) - et_offset

        if window_start <= utc_dt <= window_end:
            if prob > RAIN_THRESHOLD and first_label is None:
                first_label = local_dt.strftime("%-I %p")
            if prob > max_prob:
                max_prob   = prob
                peak_label = local_dt.strftime("%-I %p")

    return max_prob, peak_label, first_label


def build_hourly_block(forecast, start_utc):
    """
    Return a banded 24-hour hourly forecast table as a plain-text string.
    Bands: Morning (6–11 AM), Midday (12–5 PM), Evening (6–11 PM), Overnight (12–5 AM).
    Hours above RAIN_THRESHOLD are prefixed with ⚠️.
    Bands appear in chronological order; a band split across midnight shows as two rows.
    """
    et_offset = timedelta(hours=-4)
    end_utc   = start_utc + timedelta(hours=24)
    times     = forecast["hourly"]["time"]
    probs     = forecast["hourly"]["precipitation_probability"]

    HOUR_TO_BAND = {}
    for h in range(6,  12): HOUR_TO_BAND[h] = "Morning"
    for h in range(12, 18): HOUR_TO_BAND[h] = "Midday"
    for h in range(18, 24): HOUR_TO_BAND[h] = "Evening"
    for h in range(0,  6):  HOUR_TO_BAND[h] = "Overnight"

    entries = []
    for time_str, prob in zip(times, probs):
        local_dt = datetime.fromisoformat(time_str)
        utc_dt   = local_dt.replace(tzinfo=timezone.utc) - et_offset
        if start_utc <= utc_dt < end_utc:
            entries.append((local_dt.hour, local_dt.strftime("%-I%p"), prob))

    lines = []
    for band, group in groupby(entries, key=lambda e: HOUR_TO_BAND[e[0]]):
        parts = []
        for _, lbl, prob in group:
            marker = "⚠️" if prob > RAIN_THRESHOLD else ""
            parts.append(f"{marker}{lbl}:{prob}%")
        lines.append(f"{band:<9} {'  '.join(parts)}")

    return "\n".join(lines) or "  (unavailable)"


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

def build_rain_email(max_prob, peak_label, first_label, window_label, hourly_block):
    """Compose rain warning email."""
    action  = f"Bring cushions in before {first_label}" if first_label else "Bring cushions in soon"
    subject = "🌧️ Deck cushion alert — bring them in!"
    body = (
        f"Rain alert for Mansfield, MA — {window_label}\n\n"
        f"Action: {action}\n"
        f"Peak:   {max_prob}% around {peak_label}\n\n"
        f"24-hour forecast:\n{hourly_block}\n\n"
        f"— Your automated cushion watchdog 🛋️"
    )
    return subject, body


def build_clear_email(max_prob, window_label, hourly_block):
    """Compose all-clear email."""
    subject = "✅ Deck cushions — all clear!"
    body = (
        f"All clear for Mansfield, MA — {window_label}\n\n"
        f"Action: Cushions can go back out\n"
        f"Rain probability: Under {RAIN_THRESHOLD}% (max {max_prob}%)\n\n"
        f"24-hour forecast:\n{hourly_block}\n\n"
        f"— Your automated cushion watchdog 🛋️"
    )
    return subject, body


def send_email(subject, body, recipients):
    """Send via Gmail SMTP using an app password."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_SENDER, GMAIL_APP_PASS)
        server.sendmail(GMAIL_SENDER, recipients, msg.as_string())


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    # --- Validate required environment variables ---
    missing = [v for v in ["GMAIL_SENDER", "GMAIL_APP_PASSWORD", "ALERT_PRIMARY_EMAIL"]
               if not os.environ.get(v)]
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print("Set these as GitHub Secrets. See README.md.")
        sys.exit(1)

    # --- Seasonal guard ---
    now_month = datetime.now().month
    if now_month not in ACTIVE_MONTHS:
        print(f"Month {now_month} is outside cushion season (May–Oct). Skipping.")
        sys.exit(0)

    # --- Load previous state ---
    state = load_state()
    was_raining = state["rain_alert_active"]
    print(f"Previous state: rain_alert_active={was_raining} (last run: {state['last_run']})")

    # --- Determine check window ---
    window_start, window_end, window_label = get_check_window()
    print(f"Checking rain forecast for window: {window_label}")
    print(f"  UTC range: {window_start.strftime('%H:%M')} → {window_end.strftime('%H:%M')}")

    # --- Fetch forecast ---
    try:
        forecast = fetch_forecast()
    except Exception as e:
        print(f"ERROR: Could not fetch forecast: {e}")
        sys.exit(1)

    # --- Parse relevant hours ---
    max_prob, peak_label, first_label = parse_rain_in_window(forecast, window_start, window_end)
    print(f"Peak precipitation probability in window: {max_prob}%"
          + (f" (around {peak_label})" if peak_label else ""))
    if first_label:
        print(f"First rainy hour: {first_label}")

    # --- Determine current state ---
    is_raining = max_prob > RAIN_THRESHOLD

    # --- Build 24-hour hourly forecast block ---
    hourly_block = build_hourly_block(forecast, window_start)
    print(f"Hourly block ({len(hourly_block.splitlines())} rows):\n{hourly_block}")

    # --- Build recipient list ---
    recipients = [PRIMARY_RECIPIENT]
    if INCLUDE_SECONDARY and SECONDARY_RECIPIENT:
        recipients.append(SECONDARY_RECIPIENT)

    # --- Notify only on state change ---
    if is_raining and not was_raining:
        print("State change: Clear → Rain. Sending bring-in alert.")
        subject, body = build_rain_email(max_prob, peak_label, first_label, window_label, hourly_block)
        try:
            send_email(subject, body, recipients)
            print(f"Alert sent to: {', '.join(recipients)}")
        except Exception as e:
            print(f"ERROR: Could not send email: {e}")
            sys.exit(1)

    elif not is_raining and was_raining:
        print("State change: Rain → Clear. Sending all-clear.")
        subject, body = build_clear_email(max_prob, window_label, hourly_block)
        try:
            send_email(subject, body, recipients)
            print(f"All-clear sent to: {', '.join(recipients)}")
        except Exception as e:
            print(f"ERROR: Could not send email: {e}")
            sys.exit(1)

    else:
        status = "Rain continuing" if is_raining else "Still clear"
        print(f"No state change ({status}). No email sent.")

    # --- Save new state ---
    save_state(is_raining)


if __name__ == "__main__":
    main()
