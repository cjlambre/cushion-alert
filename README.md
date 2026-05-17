# 🛋️ Deck Cushion Rain Alert

Automated twice-daily weather check for Mansfield, MA. Sends a Gmail alert
when rain probability exceeds 10% in the upcoming window. Runs May–October only.

---

## How it works

| Check time | Forecast window |
|------------|-----------------|
| 6:00 AM    | Through 10:00 PM same day (~16 hrs) |
| 10:00 PM   | Through 6:00 AM next morning (~8 hrs) |

Alert is sent **only when rain is likely** — no all-clear spam.

---

## One-time setup (do this before creating the Routine)

### Step 1 — Create a Gmail App Password

This lets the script send email on your behalf without storing your real password.

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
2. Under "How you sign in to Google," make sure **2-Step Verification is ON**
3. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
4. Click **Create app password**
5. Name it something like `cushion-alert`
6. Google will show you a **16-character password** (e.g. `abcd efgh ijkl mnop`)
7. Copy it — you won't see it again

### Step 2 — Note your credentials for the Routine setup

Keep these handy — you'll enter them as environment variables in Step 5,
not in the code:

- `GMAIL_SENDER` → your Gmail address
- `GMAIL_APP_PASSWORD` → the 16-char app password from Step 1 (no spaces)
- `ALERT_PRIMARY_EMAIL` → your Gmail address (primary alert recipient)
- `ALERT_SECONDARY_EMAIL` → secondary recipient's email (optional, for after trial)

### Step 3 — Test it locally first

Set the environment variables in your terminal, then run the script:

```bash
export GMAIL_SENDER="your.gmail@gmail.com"
export GMAIL_APP_PASSWORD="your16charpassword"
export ALERT_PRIMARY_EMAIL="your.gmail@gmail.com"

python3 weather_check.py
```

You should see output like:
```
Checking rain forecast for window: today
  UTC range: 10:00 → 02:00
Peak precipitation probability in window: 34% (around 3 PM)
Alert sent to: your.gmail@gmail.com
```
or:
```
Peak precipitation probability in window: 4% is under 10% threshold. No alert sent.
```

Check your Gmail inbox to confirm the alert arrived.

---

## Setting up the Claude Code Routine

### Step 4 — Put the files in a repository

Create a new GitHub repo (e.g. `cushion-alert`) and push both files:
```
cushion-alert/
├── weather_check.py
└── CLAUDE.md
```

### Step 5 — Create the Routine

1. Go to [claude.ai/code/routines](https://claude.ai/code/routines)
2. Click **New routine**
3. Fill in:
   - **Name:** Deck Cushion Rain Alert
   - **Instructions:** `Run the weather check script by executing: python3 weather_check.py. The script is fully self-contained. Just run it and report the output.`
   - **Repository:** select your `cushion-alert` repo
   - **Connectors:** remove all (Excalidraw, Gmail, Google Calendar, Hugging Face) — the script handles email directly
4. Under **Environment variables** (look for this in the Behavior or Settings tab), add:
   - `GMAIL_SENDER` = your Gmail address
   - `GMAIL_APP_PASSWORD` = your 16-char app password
   - `ALERT_PRIMARY_EMAIL` = your Gmail address
   - `ALERT_SECONDARY_EMAIL` = secondary email address
5. Under **Trigger**, add two schedule triggers:
   - `0 10 * * *` — 6:00 AM ET (UTC-4 in summer = 10:00 UTC)
   - `0 2 * * *`  — 10:00 PM ET (UTC-4 = 02:00 UTC next day)
6. Click **Create**

> **Note on timing:** Claude Code Routines add up to 10 minutes of jitter after
> the scheduled time. The script handles this — the check window is defined by
> local hour (5–8 AM catches the morning run, 9 PM–midnight catches the evening run),
> so jitter won't cause the wrong window to be used.

---

## Enabling alerts to secondary email (after trial period)

Open `weather_check.py` and change this one line:

```python
# Before
INCLUDE_SECONDARY = False

# After
INCLUDE_SECONDARY = True
```

Commit and push. The `ALERT_SECONDARY_EMAIL` environment variable is already
set in the Routine, so no changes needed there.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| No email received | Check spam folder; verify app password has no spaces |
| `SMTPAuthenticationError` | App password is wrong or 2FA isn't enabled on Google account |
| `urlopen error` | Open-Meteo is down or Routine has no internet (rare) |
| Wrong window used | Check that your cron expressions use UTC (see Step 5) |
| Routine not firing | Verify it shows as "Active" at claude.ai/code/routines |

---

## Files

| File | Purpose |
|------|---------|
| `weather_check.py` | Main script — weather fetch, threshold check, email send |
| `CLAUDE.md` | Routine instructions (Claude reads this when the task runs) |
| `README.md` | This file |
