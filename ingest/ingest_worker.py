"""Persistent AISStream.io listener, tracking multiple strait geofences in
one connection.

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

# name -> [[lat_min, long_min], [lat_max, long_max]]
REGIONS = {
    "hormuz": [[25.5, 55.0], [27.0, 57.5]],
    "singapore": [[1.0, 103.5], [1.35, 104.2]],
}

WS_URL = "wss://stream.aisstream.io/v0/stream"


def classify_region(lat, lon):
    if lat is None or lon is None:
        return None
    for name, ((lat_min, lon_min), (lat_max, lon_max)) in REGIONS.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return None


def init_db():
    conn = sqlite3.connect(DB_PATH)
    # WAL mode lets aggregate_and_push.py read concurrently while this process writes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ais_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            region TEXT,
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
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(ais_messages)")}
    if "region" not in existing_cols:
        conn.execute("ALTER TABLE ais_messages ADD COLUMN region TEXT")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_received_at ON ais_messages(received_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mmsi ON ais_messages(mmsi)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_region ON ais_messages(region)")
    conn.commit()
    return conn


def store_message(conn, msg: dict):
    meta = msg.get("MetaData", {})
    report = msg.get("Message", {}).get("PositionReport", {})
    lat = report.get("Latitude") or meta.get("latitude")
    lon = report.get("Longitude") or meta.get("longitude")
    conn.execute(
        """INSERT INTO ais_messages
           (received_at, region, mmsi, ship_name, latitude, longitude, sog, cog, nav_status, raw_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            classify_region(lat, lon),
            meta.get("MMSI"),
            meta.get("ShipName", "").strip() or None,
            lat,
            lon,
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
            # generous timeout: current transit volume through the strait is
            # ~27 vessels/day (vs ~90 normal), so gaps between AIS reports in
            # this geofence regularly exceed 30s -- that's not a dead connection
            ws = websocket.create_connection(WS_URL, timeout=180)
            ws.send(json.dumps({
                "APIKey": API_KEY,
                "BoundingBoxes": list(REGIONS.values()),
                "FilterMessageTypes": ["PositionReport"],
            }))
            print(f"[ingest] subscribed to {list(REGIONS.keys())}, listening")
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
