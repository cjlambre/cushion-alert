#!/usr/bin/env python3
"""
Deck Cushion Rain Alert
-----------------------
Checks the Open-Meteo forecast for Mansfield, MA and sends a Gmail alert
if rain probability exceeds 10% during the upcoming window.

Schedule:
  6:00 AM check  → looks ahead through 10:00 PM (~16 hours)
  10:00 PM check → looks ahead through 6:00 AM next day (~8 hours)

Runs May–October only. Sends email only when rain is likely (no all-clear spam).

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

# Email recipients — set via environment variables, never hardcoded
PRIMARY_RECIPIENT   = os.environ.get("ALERT_PRIMARY_EMAIL", "")
SECONDARY_RECIPIENT = os.environ.get("ALERT_SECONDARY_EMAIL", "")

# Set INCLUDE_SECONDARY = True after your trial period
INCLUDE_SECONDARY = False

# Gmail SMTP — credentials set via environment variables, never hardcoded
GMAIL_SENDER   = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")


# ---------------------------------------------------------------------------
# LOGIC
# ---------------------------------------------------------------------------

def get_check_window():
    """
    Return (window_start, window_end) in UTC based on current local hour.
    6 AM check  → now through 10 PM local
    10 PM check → now through 6 AM next day local
    """
    et_offset = timedelta(hours=-4)  # EDT (May–Oct is always EDT, not EST)
    now_utc = datetime.now(timezone.utc)
    now_et  = now_utc + et_offset
    hour_et = now_et.hour

    # Morning check: 5–8 AM window (catches 6 AM cron with up to 90s jitter)
    if 5 <= hour_et < 9:
        window_end_et = now_et.replace(hour=22, minute=0, second=0, microsecond=0)
        label = "today"
    # Evening check: 9 PM–midnight window (catches 10 PM cron)
    elif 21 <= hour_et <= 23:
        tomorrow_et    = now_et + timedelta(days=1)
        window_end_et  = tomorrow_et.replace(hour=6, minute=0, second=0, microsecond=0)
        label = "overnight"
    else:
        # Script called outside expected windows — default to next 8 hours
        window_end_et = now_et + timedelta(hours=8)
        label = "next 8 hours"

    window_start_utc = now_utc
    window_end_utc   = window_end_et - et_offset  # convert back to UTC
    return window_start_utc, window_end_utc, label


def fetch_forecast():
    """Fetch hourly precipitation_probability from Open-Meteo (no API key needed)."""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&hourly=precipitation_probability"
        f"&timezone={TIMEZONE}"
        f"&forecast_days=2"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


def parse_rain_in_window(forecast, window_start, window_end):
    """
    Return (max_probability, peak_hour_label) for hours falling within the window.
    """
    times = forecast["hourly"]["time"]           # list of "YYYY-MM-DDTHH:MM" strings
    probs = forecast["hourly"]["precipitation_probability"]

    max_prob   = 0
    peak_label = None

    et_offset = timedelta(hours=-4)  # EDT

    for time_str, prob in zip(times, probs):
        # Parse naive local time, treat as ET
        local_dt = datetime.fromisoformat(time_str)
        utc_dt   = local_dt.replace(tzinfo=timezone.utc) - et_offset  # convert ET→UTC

        if window_start <= utc_dt <= window_end:
            if prob > max_prob:
                max_prob   = prob
                peak_label = local_dt.strftime("%-I %p")  # e.g. "3 PM"

    return max_prob, peak_label


def build_email(max_prob, peak_label, window_label):
    """Compose a plain-text alert email."""
    subject = "🌧️ Deck cushion alert — bring them in!"
    body = (
        f"Rain alert for Mansfield, MA\n\n"
        f"Forecast window: {window_label}\n"
        f"Peak rain probability: {max_prob}% (around {peak_label})\n\n"
        f"Recommended action: bring in the deck cushions before it rains.\n\n"
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


def main():
    # --- Validate required environment variables ---
    missing = [v for v in ["GMAIL_SENDER", "GMAIL_APP_PASSWORD", "ALERT_PRIMARY_EMAIL"]
               if not os.environ.get(v)]
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print("Set these in the Routine's environment settings. See README.md.")
        sys.exit(1)

    # --- Seasonal guard ---
    now_month = datetime.now().month
    if now_month not in ACTIVE_MONTHS:
        print(f"Month {now_month} is outside cushion season (May–Oct). Skipping.")
        sys.exit(0)

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
    max_prob, peak_label = parse_rain_in_window(forecast, window_start, window_end)
    print(f"Peak precipitation probability in window: {max_prob}% (around {peak_label})")

    # --- Send alert only if threshold exceeded ---
    if max_prob > RAIN_THRESHOLD:
        recipients = [PRIMARY_RECIPIENT]
        if INCLUDE_SECONDARY:
            recipients.append(SECONDARY_RECIPIENT)

        subject, body = build_email(max_prob, peak_label, window_label)

        try:
            send_email(subject, body, recipients)
            print(f"Alert sent to: {', '.join(recipients)}")
        except Exception as e:
            print(f"ERROR: Could not send email: {e}")
            sys.exit(1)
    else:
        print(f"No alert needed — max probability {max_prob}% is under {RAIN_THRESHOLD}% threshold.")


if __name__ == "__main__":
    main()
