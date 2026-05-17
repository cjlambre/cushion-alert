# Deck Cushion Rain Alert — Routine Instructions

## What this routine does

This routine checks the weather forecast for Mansfield, MA twice daily and
sends a Gmail alert if there is greater than 10% chance of rain during the
upcoming window. It only runs May through October (cushion season).

## Schedule

- **6:00 AM ET** — checks forecast through 10:00 PM the same day
- **10:00 PM ET** — checks forecast through 6:00 AM the next morning

## How to run it

Execute the weather check script using Python 3:

```bash
python3 weather_check.py
```

The script is fully self-contained. It will:
1. Check whether the current month is within the active season (May–Oct)
2. Determine the appropriate forecast window based on the current time
3. Fetch the hourly precipitation probability from Open-Meteo for Mansfield, MA
4. Send a Gmail alert only if the peak probability in the window exceeds 10%
5. Print a clear status line indicating whether an alert was sent or skipped

## Success criteria

- Exit code 0 in all normal cases (including "no rain, no alert sent")
- Exit code 1 only on fetch or send errors
- A log line printed for every run so you can verify execution in the Routine history

## Do not modify

Do not modify recipient addresses, coordinates, thresholds, or the Gmail
credentials. These are configured inside the script. If the script fails,
report the error output in the run log and stop — do not retry or attempt
to fix the script autonomously.
