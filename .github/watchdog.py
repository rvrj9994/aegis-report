#!/usr/bin/env python3
"""AEGIS external watchdog — runs on GitHub Actions (OFF the trading Mac).

Purpose: detect when the Mac mini / bot did NOT start on a trading day. The bot
publishes a fresh commit to this repo (rvrj9994/aegis-report) every weekday morning
(~09:45 ET) via live_report.py. If, on an NYSE trading day, no commit landed today by
the time this runs (~10:30-11:30 ET), the bot almost certainly didn't come up — so we
alert Discord AND fail the job (GitHub emails the repo owner as an independent backup).

Why this exists: it is the ONE monitor that survives a TOTAL Mac/power failure, because
it runs on GitHub's infrastructure, not the Mac. The on-Mac monitors (gateway-check, EOD
health card, trader supervisor) cover component failures while the Mac is up; this covers
"the Mac never came up at all" — which is exactly what happened 2026-06-16 (booted to a
FileVault/login screen, no agents ran).

Stdlib only (no pip installs) so the watchdog itself can never flake on a dependency.
Read-only: makes one unauthenticated GitHub API call + one Discord webhook POST.
"""
import datetime
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

REPO = "rvrj9994/aegis-report"
PAGES = "https://rvrj9994.github.io/aegis-report/"
NY = ZoneInfo("America/New_York")

# NYSE full-day closures (update annually). Half-days still trade, so they're omitted.
# 2026-2027 covered; add future years before Jan of that year or the check will
# false-alarm on those holidays (it would fire a 🔴 card; harmless but noisy).
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}


def discord(title, desc, color):
    """Best-effort Discord webhook POST. Webhook arrives via the DISCORD_WEBHOOK_URL
    Actions secret; if it's unset we rely on the job-failure email instead."""
    wh = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not wh:
        print("No DISCORD_WEBHOOK_URL secret set — relying on GitHub job-failure email.")
        return False
    body = json.dumps({
        "username": "AEGIS Watchdog",
        "embeds": [{"title": title, "description": desc, "color": color}],
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            wh, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "aegis-watchdog"},
        ), timeout=25)
        print("Discord alert sent.")
        return True
    except Exception as e:
        print(f"Discord send failed: {e}")
        return False


def main():
    now = datetime.datetime.now(NY)
    today = now.date()
    force = os.environ.get("FORCE_ALERT") == "true"

    if not force:
        if today.weekday() >= 5:
            print(f"{today} is a weekend — bot is idle, skip.")
            return 0
        if today.isoformat() in NYSE_HOLIDAYS:
            print(f"{today} is an NYSE holiday — bot is idle, skip.")
            return 0

    # Latest commit on the public report repo (no auth needed for a public repo).
    url = f"https://api.github.com/repos/{REPO}/commits?per_page=1"
    req = urllib.request.Request(url, headers={
        "User-Agent": "aegis-watchdog",
        "Accept": "application/vnd.github+json",
    })
    try:
        data = json.load(urllib.request.urlopen(req, timeout=25))
        iso = data[0]["commit"]["committer"]["date"]  # ISO8601 UTC, e.g. 2026-06-16T20:25:00Z
    except Exception as e:
        # Inconclusive (transient API hiccup) — do NOT false-alarm. A real outage
        # repeats and will be caught on the next run.
        print(f"GitHub API error ({e}) — treating as inconclusive, no alert.")
        return 0

    commit_et = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(NY)
    age_h = (now - commit_et).total_seconds() / 3600
    published_today = commit_et.date() == today
    print(f"now={now:%F %T %Z} | latest report commit={commit_et:%F %T %Z} "
          f"({age_h:.1f}h ago) | published_today={published_today}")

    if published_today and not force:
        print("Report updated today — bot is alive. No alert.")
        return 0

    if force:
        ok = discord(
            "🟢 AEGIS Watchdog — TEST alert (forced)",
            f"This is a forced test of the external watchdog. The last live-report publish "
            f"was **{commit_et:%a %b %d %H:%M ET}** ({age_h:.0f}h ago).\n{PAGES}",
            0x2ECC71,
        )
        if ok:
            print("Forced test complete — Discord card delivered.")
            return 0
        # Non-zero so the run CONCLUSION (readable via the public API) flags a
        # missing/invalid DISCORD_WEBHOOK_URL secret even though it's a 'test'.
        print("Forced test FAILED — no/invalid DISCORD_WEBHOOK_URL secret. Add it in "
              "repo Settings -> Secrets and variables -> Actions.")
        return 1

    discord(
        "🔴 AEGIS Watchdog — bot may be DOWN",
        f"No live-report update today (**{today:%a %b %d} ET**). Last publish was "
        f"**{commit_et:%a %b %d %H:%M ET}** ({age_h:.0f}h ago).\n\n"
        f"The Mac mini may not have booted / auto-logged-in, or the bot didn't start. "
        f"Check the Mac + IB Gateway.\n{PAGES}",
        0xC0392B,
    )
    # Non-zero exit → GitHub marks the run failed and emails the repo owner: an alert
    # path fully independent of Discord (and of the Mac).
    return 1


if __name__ == "__main__":
    sys.exit(main())
