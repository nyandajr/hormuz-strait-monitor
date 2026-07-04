"""Reads the SQLite feed the persistent worker is writing, computes a
snapshot, and pushes it to the git repo. Run this from the VM's own
crontab every 15-30 min -- NOT from GitHub Actions.
"""

import csv
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = os.environ.get("HORMUZ_DB_PATH", "hormuz.db")
REPO_DIR = Path(os.environ.get("HORMUZ_REPO_DIR", "."))

CLOSURE_START = datetime(2026, 2, 28, tzinfo=timezone.utc)
BASELINE_DAILY_TRANSITS = 90
WINDOW_HOURS = 24


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


def build_payload(vessels_underway, last_seen):
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
        # vessel count is a same-day proxy for DWT throughput, not a real
        # tonnage calculation -- AIS position reports don't carry cargo data
        "note": "dwt_throughput_pct is an AIS-transit-count proxy, not measured tonnage",
    }


def append_history(payload):
    history_path = REPO_DIR / "data" / "history.csv"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not history_path.exists()

    with open(history_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "generated_at", "days_in_closure", "vessels_underway_24h", "dwt_throughput_pct",
        ])
        if is_new:
            writer.writeheader()
        writer.writerow({k: payload[k] for k in writer.fieldnames})


def write_dashboard_json(payload):
    docs_path = REPO_DIR / "docs" / "data.json"
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(json.dumps(payload, indent=2))


def git_commit_and_push():
    subprocess.run(["git", "-C", str(REPO_DIR), "config", "user.name", "hormuz-bot"], check=True)
    subprocess.run(["git", "-C", str(REPO_DIR), "config", "user.email", "hormuz-bot@users.noreply.github.com"], check=True)
    subprocess.run(["git", "-C", str(REPO_DIR), "add", "data/history.csv", "docs/data.json"], check=True)

    diff = subprocess.run(["git", "-C", str(REPO_DIR), "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print("[aggregate] no changes to commit")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    subprocess.run(["git", "-C", str(REPO_DIR), "commit", "-m", f"data: hormuz snapshot {timestamp}"], check=True)

    push = subprocess.run(["git", "-C", str(REPO_DIR), "push"])
    if push.returncode != 0:
        subprocess.run(["git", "-C", str(REPO_DIR), "pull", "--rebase", "origin", "main"], check=True)
        subprocess.run(["git", "-C", str(REPO_DIR), "push"], check=True)


def main():
    vessels, last_seen = latest_snapshot()
    payload = build_payload(vessels, last_seen)
    append_history(payload)
    write_dashboard_json(payload)
    git_commit_and_push()
    print(f"[aggregate] snapshot pushed: {payload}")


if __name__ == "__main__":
    main()
