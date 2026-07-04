"""Persistent AISStream.io listener for the Strait of Hormuz geofence.

Runs forever under systemd (Restart=always). Reconnects with backoff on
any drop -- websocket disconnects are normal/expected, not exceptional.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import websocket

API_KEY = os.environ["AISSTREAM_API_KEY"]
DB_PATH = os.environ.get("HORMUZ_DB_PATH", "hormuz.db")

# Strait of Hormuz geofence
BOUNDING_BOX = [[[25.5, 55.0], [27.0, 57.5]]]

WS_URL = "wss://stream.aisstream.io/v0/stream"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    # WAL mode lets aggregate_and_push.py read concurrently while this process writes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ais_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            mmsi INTEGER,
            ship_name TEXT,
            latitude REAL,
            longitude REAL,
            sog REAL,
            cog REAL,
            nav_status INTEGER,
            raw_json TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_received_at ON ais_messages(received_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mmsi ON ais_messages(mmsi)")
    conn.commit()
    return conn


def store_message(conn, msg: dict):
    meta = msg.get("MetaData", {})
    report = msg.get("Message", {}).get("PositionReport", {})
    conn.execute(
        """INSERT INTO ais_messages
           (received_at, mmsi, ship_name, latitude, longitude, sog, cog, nav_status, raw_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            meta.get("MMSI"),
            meta.get("ShipName", "").strip() or None,
            report.get("Latitude") or meta.get("latitude"),
            report.get("Longitude") or meta.get("longitude"),
            report.get("Sog"),
            report.get("Cog"),
            report.get("NavigationalStatus"),
            json.dumps(msg),
        ),
    )
    conn.commit()


def run():
    conn = init_db()
    backoff = 5

    while True:
        try:
            print(f"[ingest] connecting to {WS_URL}")
            ws = websocket.create_connection(WS_URL, timeout=30)
            ws.send(json.dumps({
                "APIKey": API_KEY,
                "BoundingBoxes": BOUNDING_BOX,
                "FilterMessageTypes": ["PositionReport"],
            }))
            print("[ingest] subscribed, listening")
            backoff = 5

            while True:
                raw = ws.recv()
                if not raw:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("MessageType") == "PositionReport":
                    store_message(conn, msg)

        except Exception as e:
            print(f"[ingest] connection error: {e} -- reconnecting in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)


if __name__ == "__main__":
    run()
