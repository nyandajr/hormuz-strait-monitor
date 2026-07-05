"""Reads the SQLite feed the persistent worker is writing, computes a
snapshot, and pushes it to the git repo. Run this from the VM's own
crontab every 15-30 min -- NOT from GitHub Actions.
"""

import csv
import io
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DB_PATH = os.environ.get("HORMUZ_DB_PATH", "hormuz.db")
REPO_DIR = Path(os.environ.get("HORMUZ_REPO_DIR", "."))

CLOSURE_START = datetime(2026, 2, 28, tzinfo=timezone.utc)
BASELINE_DAILY_TRANSITS = 90
WINDOW_HOURS = 24

CRUDE_CSV_URL = "https://raw.githubusercontent.com/nyandajr/global-fuel-watch/main/data/live/crude.csv"


def latest_crude_price(commodity="brent"):
    """Reads global-fuel-watch's own published CSV directly -- no separate
    Alpha Vantage key needed here, and no duplicate fetch logic to maintain.
    """
    try:
        resp = requests.get(CRUDE_CSV_URL, timeout=15)
        resp.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(resp.text)))
    except Exception as e:
        print(f"[aggregate] crude price fetch failed: {e}")
        return None

    matching = [r for r in rows if r.get("commodity") == commodity]
    if not matching:
        return None

    latest = matching[-1]
    return {
        "commodity": commodity,
        "price_usd": float(latest["price_usd"]),
        "as_of_date": latest["date"],
        "fetched_at": latest["timestamp"],
    }


def latest_snapshot():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)).isoformat()

    vessels = conn.execute(
        "SELECT COUNT(DISTINCT mmsi) FROM ais_messages WHERE received_at > ? AND mmsi IS NOT NULL",
        (cutoff,),
    ).fetchone()[0]

    last_seen = conn.execute(
        "SELECT MAX(received_at) FROM ais_messages"
    ).fetchone()[0]

    conn.close()
    return vessels, last_seen


def build_payload(vessels_underway, last_seen, brent):
    now = datetime.now(timezone.utc)
    days_in_closure = (now - CLOSURE_START).days
    throughput_pct = round((vessels_underway / BASELINE_DAILY_TRANSITS) * 100, 1) if vessels_underway else 0.0

    return {
        "generated_at": now.isoformat(),
        "last_ais_message_at": last_seen,
        "closure_start": CLOSURE_START.date().isoformat(),
        "days_in_closure": days_in_closure,
        "vessels_underway_24h": vessels_underway,
        "baseline_daily_transits": BASELINE_DAILY_TRANSITS,
        "dwt_throughput_pct": throughput_pct,
        "brent_crude": brent,
        # vessel count is a same-day proxy for DWT throughput, not a real
        # tonnage calculation -- AIS position reports don't carry cargo data
        "note": "dwt_throughput_pct is an AIS-transit-count proxy, not measured tonnage",
    }


def append_history(payload):
    history_path = REPO_DIR / "data" / "history.csv"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not history_path.exists()

    row = {
        "generated_at": payload["generated_at"],
        "days_in_closure": payload["days_in_closure"],
        "vessels_underway_24h": payload["vessels_underway_24h"],
        "dwt_throughput_pct": payload["dwt_throughput_pct"],
        "brent_price_usd": payload["brent_crude"]["price_usd"] if payload["brent_crude"] else "",
    }

    with open(history_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def write_dashboard_json(payload):
    docs_path = REPO_DIR / "docs" / "data.json"
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(json.dumps(payload, indent=2))


def git_commit_and_push():
    def run(*args):
        subprocess.run(["git", "-C", str(REPO_DIR), *args], check=True)

    run("config", "user.name", "hormuz-bot")
    run("config", "user.email", "hormuz-bot@users.noreply.github.com")

    # fetch + reset --soft (not pull --rebase): docs/data.json is fully
    # rewritten every run, not appended to, so a rebase can genuinely
    # conflict with itself run-over-run. There's nothing worth merging
    # between two generated snapshots -- the newest one should just win.
    run("fetch", "origin", "main")
    run("reset", "--soft", "origin/main")
    run("add", "data/history.csv", "docs/data.json")

    diff = subprocess.run(["git", "-C", str(REPO_DIR), "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print("[aggregate] no changes to commit")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    run("commit", "-m", f"data: hormuz snapshot {timestamp}")
    run("push", "--force", "origin", "HEAD:main")


def main():
    vessels, last_seen = latest_snapshot()
    brent = latest_crude_price("brent")
    payload = build_payload(vessels, last_seen, brent)
    append_history(payload)
    write_dashboard_json(payload)
    git_commit_and_push()
    print(f"[aggregate] snapshot pushed: {payload}")


if __name__ == "__main__":
    main()
