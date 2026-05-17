# 🛋️ Deck Cushion Rain Alert

Automated twice-daily weather check for Mansfield, MA. Sends a Gmail alert
when rain status **changes** — bring-in warnings and all-clear notifications.
Runs May–October only.

Runs entirely in the cloud via GitHub Actions — no computer needs to be on.

---

## How it works

| Check time | Forecast window |
|------------|-----------------|
| 6:00 AM ET | Through 10:00 PM same day (~16 hrs) |
| 10:00 PM ET | Through 6:00 AM next morning (~8 hrs) |

Alerts fire **only on a state change** — no repeated reminders when nothing changes:

| Transition | Alert sent |
|------------|------------|
| Clear → Rain likely | "Bring in the cushions" |
| Rain → Clear | "All clear — cushions can go back out" |
| No change | Silent |

Each alert email also includes a **3-run forward forecast** (the next three scheduled
checks) so you can anticipate whether another alert is coming.

Rain state is persisted in `state.json` and committed back to the repo by the
workflow after every run.

Weather data comes from [Open-Meteo](https://open-meteo.com/) — free, no API key needed.

---

## One-time setup

### Step 1 — Create a Gmail App Password

This lets the script send email on your behalf without storing your real password.

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
2. Under "How you sign in to Google," make sure **2-Step Verification is ON**
3. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
4. Click **Create app password**
5. Name it something like `cushion-alert`
6. Google will show you a **16-character password**
7. Copy it — you won't see it again

### Step 2 — Add GitHub Secrets

Go to your repo on GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

Add these four secrets:

| Secret name | Value |
|-------------|-------|
| `GMAIL_SENDER` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-char app password from Step 1 (no spaces) |
| `ALERT_PRIMARY_EMAIL` | your Gmail address (primary alert recipient) |
| `ALERT_SECONDARY_EMAIL` | secondary recipient's email (optional, for after trial) |

These are encrypted by GitHub and never visible to anyone after saving.

### Step 3 — Push the workflow file

Make sure `.github/workflows/rain-alert.yaml` is committed and pushed.
GitHub Actions will pick it up automatically and start running on schedule.

### Step 4 — Test manually

Go to your repo on GitHub → **Actions** tab → click **Deck Cushion Rain Alert**
→ **Run workflow** button. This triggers it immediately without waiting for the
next scheduled run. Check your inbox within a minute or two.

---

## Enabling alerts for a second recipient (after trial period)

Open `weather_check.py` and change this one line:

```python
# Before
INCLUDE_SECONDARY = False

# After
INCLUDE_SECONDARY = True
```

Commit and push. The `ALERT_SECONDARY_EMAIL` secret is already stored in GitHub,
so no other changes are needed.

---

## Rotating the Gmail app password

If you ever need to generate a new app password:

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   and revoke the old one
2. Create a new one
3. Go to GitHub → **Settings** → **Secrets and variables** → **Actions**
   → click `GMAIL_APP_PASSWORD` → **Update**

No code changes needed.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| No email received | Check spam folder; verify app password has no spaces |
| `SMTPAuthenticationError` | App password is wrong or 2FA isn't enabled on Google account |
| `urlopen error` | Open-Meteo is temporarily down (rare); will self-resolve |
| Wrong forecast window | Cron expressions are in UTC — `0 10 * * *` = 6 AM ET, `0 2 * * *` = 10 PM ET |
| Workflow not firing | Go to Actions tab and confirm the workflow is enabled |
| Test run output | Click any run in the Actions tab to see the full script log |

---

## Files

| File | Purpose |
|------|---------|
| `weather_check.py` | Main script — weather fetch, threshold check, email send |
| `state.json` | Persisted rain status — committed back to repo after each run |
| `.github/workflows/rain-alert.yaml` | GitHub Actions workflow — schedule and runner config |
| `README.md` | This file |
