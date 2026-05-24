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

# Set True to receive an email even when rain status has not changed (debug)
NOTIFY_ON_NO_CHANGE = True

# Forecast hour → band name
_HOUR_TO_BAND = {
    **{h: "Morning"   for h in range(6,  12)},
    **{h: "Afternoon" for h in range(12, 18)},
    **{h: "Evening"   for h in range(18, 24)},
    **{h: "Overnight" for h in range(0,  6)},
}


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


def get_hourly_data(forecast, start_utc):
    """
    Return list of (band_name, [(hour_label, prob), ...]) for the 24 hours starting
    at start_utc, in chronological order. A band split across midnight appears as
    two separate entries (e.g. Evening at start + Evening at end of window).
    """
    et_offset = timedelta(hours=-4)
    end_utc   = start_utc + timedelta(hours=24)
    times     = forecast["hourly"]["time"]
    probs     = forecast["hourly"]["precipitation_probability"]

    entries = []
    for time_str, prob in zip(times, probs):
        local_dt = datetime.fromisoformat(time_str)
        utc_dt   = local_dt.replace(tzinfo=timezone.utc) - et_offset
        if start_utc <= utc_dt < end_utc:
            entries.append((local_dt.hour, local_dt.strftime("%-I%p"), prob))

    result = []
    for band, group in groupby(entries, key=lambda e: _HOUR_TO_BAND[e[0]]):
        result.append((band, [(lbl, prob) for _, lbl, prob in group]))
    return result


# ---------------------------------------------------------------------------
# FORMATTING
# ---------------------------------------------------------------------------

def format_hourly_text(hourly_data):
    """Return the banded hourly forecast as plain text (email fallback)."""
    lines = []
    for band, hours in hourly_data:
        parts = []
        for lbl, prob in hours:
            marker = "⚠️" if prob > RAIN_THRESHOLD else ""
            parts.append(f"{marker}{lbl}:{prob}%")
        lines.append(f"{band:<9} {'  '.join(parts)}")
    return "\n".join(lines) or "  (unavailable)"


def format_hourly_html(hourly_data):
    """Return the banded hourly forecast as an HTML table."""
    TD_LABEL  = 'style="padding:4px 8px;text-align:center;color:#666;font-size:12px;"'
    TD_CELL   = 'style="padding:5px 8px;text-align:center;"'
    TD_RAIN   = 'style="padding:5px 8px;text-align:center;background:#ffe0b2;font-weight:600;"'
    TD_SPACER = 'style="background:#f2f2f2;"'

    rows = []
    for i, (band, hours) in enumerate(hourly_data):
        sep     = "border-top:2px solid #ddd;" if i > 0 else ""
        td_band = (
            f'style="padding:5px 10px 5px 8px;font-weight:600;font-size:11px;'
            f'color:#555;text-transform:uppercase;letter-spacing:.5px;'
            f'background:#f2f2f2;{sep}"'
        )
        labels = "".join(f'<td {TD_LABEL}>{lbl}</td>' for lbl, _ in hours)
        rows.append(f'  <tr><td {td_band}>{band}</td>{labels}</tr>')

        values = []
        for _, prob in hours:
            if prob > RAIN_THRESHOLD:
                values.append(f'<td {TD_RAIN}>⚠️ {prob}%</td>')
            else:
                values.append(f'<td {TD_CELL}>{prob}%</td>')
        rows.append(f'  <tr><td {TD_SPACER}></td>{"".join(values)}</tr>')

    return (
        '<table cellspacing="0" cellpadding="0" '
        'style="border-collapse:collapse;font-size:14px;font-family:inherit;width:100%;">\n'
        + "\n".join(rows) + "\n</table>"
    )


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

_HTML_TMPL = """\
<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,sans-serif;font-size:15px;color:#333;max-width:480px;padding:16px;margin:0;">
<p style="margin:0 0 14px;font-size:16px;font-weight:600;">{title}</p>
<p style="margin:0 0 16px;line-height:1.7;">{details}</p>
<p style="margin:0 0 8px;font-weight:600;font-size:13px;">24-hour forecast</p>
{table}
<p style="color:#aaa;font-size:13px;margin-top:18px;">&mdash; Your automated cushion watchdog 🛋️</p>
</body>
</html>"""


def _html_email(title, detail_pairs, table_html):
    """Assemble a complete HTML email body from structured parts."""
    details = "<br>\n".join(f"<strong>{k}:</strong> {v}" for k, v in detail_pairs)
    return _HTML_TMPL.format(title=title, details=details, table=table_html)


def build_rain_email(max_prob, peak_label, first_label, window_label, hourly_text, hourly_html):
    """Compose rain warning email, returning (subject, plain_body, html_body)."""
    action  = f"Bring cushions in before {first_label}" if first_label else "Bring cushions in soon"
    subject = "🌧️ Deck cushion alert — bring them in!"
    plain = (
        f"Rain alert for Mansfield, MA — {window_label}\n\n"
        f"Action: {action}\n"
        f"Peak:   {max_prob}% around {peak_label}\n\n"
        f"24-hour forecast:\n{hourly_text}\n\n"
        f"— Your automated cushion watchdog 🛋️"
    )
    html = _html_email(
        f"Rain alert — Mansfield, MA — {window_label}",
        [("Action", action), ("Peak", f"{max_prob}% around {peak_label}")],
        hourly_html,
    )
    return subject, plain, html


def build_clear_email(max_prob, window_label, hourly_text, hourly_html):
    """Compose all-clear email, returning (subject, plain_body, html_body)."""
    subject = "✅ Deck cushions — all clear!"
    plain = (
        f"All clear for Mansfield, MA — {window_label}\n\n"
        f"Action: Cushions can go back out\n"
        f"Rain probability: Under {RAIN_THRESHOLD}% (max {max_prob}%)\n\n"
        f"24-hour forecast:\n{hourly_text}\n\n"
        f"— Your automated cushion watchdog 🛋️"
    )
    html = _html_email(
        f"All clear — Mansfield, MA — {window_label}",
        [("Action", "Cushions can go back out"),
         ("Rain probability", f"Under {RAIN_THRESHOLD}% (max {max_prob}%)")],
        hourly_html,
    )
    return subject, plain, html


def build_debug_email(max_prob, is_raining, window_label, hourly_text, hourly_html):
    """Compose no-change debug email, returning (subject, plain_body, html_body)."""
    status  = "Rain continuing" if is_raining else "Still clear"
    subject = f"🔔 No change — {status.lower()}"
    plain = (
        f"Status check for Mansfield, MA — {window_label}\n\n"
        f"Status: No change ({status})\n"
        f"Peak:   {max_prob}% in window\n\n"
        f"24-hour forecast:\n{hourly_text}\n\n"
        f"— Your automated cushion watchdog 🛋️"
    )
    html = _html_email(
        f"Status check — Mansfield, MA — {window_label}",
        [("Status", f"No change ({status})"), ("Peak", f"{max_prob}% in window")],
        hourly_html,
    )
    return subject, plain, html


def send_email(subject, plain_body, html_body, recipients):
    """Send a multipart/alternative email with plain-text and HTML parts."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body,  "html"))   # HTML last = preferred by email clients

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

    # --- Build hourly forecast (text + HTML) ---
    hourly_data = get_hourly_data(forecast, window_start)
    hourly_text = format_hourly_text(hourly_data)
    hourly_html = format_hourly_html(hourly_data)
    print(f"Hourly block ({len(hourly_data)} bands):\n{hourly_text}")

    # --- Build recipient list ---
    recipients = [PRIMARY_RECIPIENT]
    if INCLUDE_SECONDARY and SECONDARY_RECIPIENT:
        recipients.append(SECONDARY_RECIPIENT)

    # --- Notify only on state change (or always if NOTIFY_ON_NO_CHANGE) ---
    if is_raining and not was_raining:
        print("State change: Clear → Rain. Sending bring-in alert.")
        subject, plain, html = build_rain_email(
            max_prob, peak_label, first_label, window_label, hourly_text, hourly_html
        )
        try:
            send_email(subject, plain, html, recipients)
            print(f"Alert sent to: {', '.join(recipients)}")
        except Exception as e:
            print(f"ERROR: Could not send email: {e}")
            sys.exit(1)

    elif not is_raining and was_raining:
        print("State change: Rain → Clear. Sending all-clear.")
        subject, plain, html = build_clear_email(
            max_prob, window_label, hourly_text, hourly_html
        )
        try:
            send_email(subject, plain, html, recipients)
            print(f"All-clear sent to: {', '.join(recipients)}")
        except Exception as e:
            print(f"ERROR: Could not send email: {e}")
            sys.exit(1)

    else:
        status = "Rain continuing" if is_raining else "Still clear"
        print(f"No state change ({status}). No email sent.")
        if NOTIFY_ON_NO_CHANGE:
            subject, plain, html = build_debug_email(
                max_prob, is_raining, window_label, hourly_text, hourly_html
            )
            try:
                send_email(subject, plain, html, recipients)
                print(f"Debug email sent to: {', '.join(recipients)}")
            except Exception as e:
                print(f"ERROR: Could not send debug email: {e}")

    # --- Save new state ---
    save_state(is_raining)


if __name__ == "__main__":
    main()
