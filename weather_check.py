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
  6:00 AM check → looks ahead through 8:00 PM (~14 hours)
  8:00 PM check → looks ahead through 6:00 AM next day (~10 hours)

Runs May–October only.
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
    """
    Load previous run state from state.json.
    Returns dict with 'rain_alert_active' (bool) and 'last_run' (str).
    Defaults to clear/unknown on first run.
    """
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    # First run or corrupted file — default to clear
    return {"rain_alert_active": False, "last_run": None}


def save_state(rain_alert_active):
    """Save current run state to state.json."""
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
    6 AM check  → now through 10 PM local
    10 PM check → now through 6 AM next day local
    """
    et_offset = timedelta(hours=-4)  # EDT (May–Oct is always EDT, not EST)
    now_utc = datetime.now(timezone.utc)
    now_et  = now_utc + et_offset
    hour_et = now_et.hour

    # Morning run territory: 5 AM–2 PM (absorbs multi-hour GitHub Actions delays on 6 AM cron)
    if 5 <= hour_et < 14:
        window_end_et = now_et.replace(hour=20, minute=0, second=0, microsecond=0)
        label = "today"
    # Evening run territory: 2 PM–5 AM (absorbs delays on 8 PM cron, including past midnight)
    else:
        tomorrow_et   = now_et + timedelta(days=1)
        window_end_et = tomorrow_et.replace(hour=6, minute=0, second=0, microsecond=0)
        label = "overnight"

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
        f"&forecast_days=3"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


def parse_rain_in_window(forecast, window_start, window_end):
    """
    Return (max_probability, peak_hour_label) for hours within the window.
    """
    times = forecast["hourly"]["time"]
    probs = forecast["hourly"]["precipitation_probability"]

    max_prob   = 0
    peak_label = None
    et_offset  = timedelta(hours=-4)  # EDT

    for time_str, prob in zip(times, probs):
        local_dt = datetime.fromisoformat(time_str)
        utc_dt   = local_dt.replace(tzinfo=timezone.utc) - et_offset

        if window_start <= utc_dt <= window_end:
            if prob > max_prob:
                max_prob   = prob
                peak_label = local_dt.strftime("%-I %p")  # e.g. "3 PM"

    return max_prob, peak_label


def get_future_windows():
    """Return (start_utc, end_utc, label) for the next 3 scheduled runs after the current one."""
    et_offset = timedelta(hours=-4)  # EDT
    now_utc   = datetime.now(timezone.utc)
    now_et    = now_utc + et_offset
    today_et  = now_et.replace(hour=0, minute=0, second=0, microsecond=0)

    # Generate candidate run slots (6 AM and 10 PM ET) across the next few days,
    # then take the first 3 that start after now — works regardless of current hour.
    candidates = []
    for day_offset in range(4):
        day = today_et + timedelta(days=day_offset)
        candidates.append((day.replace(hour=6),  day.replace(hour=22),                        "morning"))
        candidates.append((day.replace(hour=20), (day + timedelta(days=1)).replace(hour=6),   "evening"))

    future_runs = []
    for start_et, end_et, period in candidates:
        if start_et > now_et:
            days_ahead = (start_et.date() - now_et.date()).days
            if days_ahead == 0:
                suffix = "tonight" if period == "evening" else "today"
            elif days_ahead == 1:
                suffix = "tomorrow"
            else:
                suffix = f"in {days_ahead} days"
            time_str = "6 AM" if period == "morning" else "10 PM"
            future_runs.append((start_et, end_et, f"{time_str} {suffix}"))
        if len(future_runs) == 3:
            break

    return [
        (s.replace(tzinfo=timezone.utc) - et_offset,
         e.replace(tzinfo=timezone.utc) - et_offset,
         lbl)
        for s, e, lbl in future_runs
    ]


def format_window_forecast(max_prob, peak_label):
    """Format a single future window as a human-readable status string."""
    if max_prob > RAIN_THRESHOLD:
        s = f"⚠️  Rain likely — {max_prob}%"
        if peak_label:
            s += f" peak around {peak_label}"
        return s
    return f"Clear — max {max_prob}%"


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

def build_rain_email(max_prob, peak_label, window_label, future_block):
    """Compose rain warning email."""
    subject = "🌧️ Deck cushion alert — bring them in!"
    body = (
        f"Rain alert for Mansfield, MA\n\n"
        f"Forecast window: {window_label}\n"
        f"Peak rain probability: {max_prob}% (around {peak_label})\n\n"
        f"Recommended action: bring in the deck cushions before it rains.\n\n"
        f"Forecast ahead:\n{future_block}\n\n"
        f"— Your automated cushion watchdog 🛋️"
    )
    return subject, body


def build_clear_email(window_label, future_block):
    """Compose all-clear email."""
    subject = "✅ Deck cushions — all clear!"
    body = (
        f"Good news for Mansfield, MA\n\n"
        f"Forecast window: {window_label}\n"
        f"Rain probability has dropped below {RAIN_THRESHOLD}%.\n\n"
        f"The cushions can go back out.\n\n"
        f"Forecast ahead:\n{future_block}\n\n"
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
    max_prob, peak_label = parse_rain_in_window(forecast, window_start, window_end)
    print(f"Peak precipitation probability in window: {max_prob}%"
          + (f" (around {peak_label})" if peak_label else ""))

    # --- Determine current state ---
    is_raining = max_prob > RAIN_THRESHOLD

    # --- Build forward forecast block for email body ---
    future_windows = get_future_windows()
    print(f"Forward forecast windows ({len(future_windows)}): {[lbl for _, _, lbl in future_windows]}")
    future_lines = []
    for win_start, win_end, win_label in future_windows:
        max_p, peak_l = parse_rain_in_window(forecast, win_start, win_end)
        print(f"  {win_label}: max={max_p}% peak={peak_l}")
        future_lines.append(f"  {win_label}: {format_window_forecast(max_p, peak_l)}")
    future_block = "\n".join(future_lines) or "  (unavailable)"

    # --- Build recipient list ---
    recipients = [PRIMARY_RECIPIENT]
    if INCLUDE_SECONDARY and SECONDARY_RECIPIENT:
        recipients.append(SECONDARY_RECIPIENT)

    # --- Notify only on state change ---
    if is_raining and not was_raining:
        print("State change: Clear → Rain. Sending bring-in alert.")
        subject, body = build_rain_email(max_prob, peak_label, window_label, future_block)
        try:
            send_email(subject, body, recipients)
            print(f"Alert sent to: {', '.join(recipients)}")
        except Exception as e:
            print(f"ERROR: Could not send email: {e}")
            sys.exit(1)

    elif not is_raining and was_raining:
        print("State change: Rain → Clear. Sending all-clear.")
        subject, body = build_clear_email(window_label, future_block)
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
