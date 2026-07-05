"""One-shot diagnostic: subscribes to a guaranteed-busy shipping lane
(Singapore Strait) for a fixed window to determine whether the AISStream
subscription code itself works, isolating it from the question of whether
the Hormuz geofence is genuinely quiet right now. Not a long-running
service -- run manually, not under systemd.
"""

import json
import os
import time

import websocket

API_KEY = os.environ["AISSTREAM_API_KEY"]
WS_URL = "wss://stream.aisstream.io/v0/stream"

# Singapore Strait -- one of the busiest shipping lanes on earth
BUSY_BOUNDING_BOX = [[[1.0, 103.5], [1.35, 104.2]]]

RUN_SECONDS = 90


def run():
    print(f"[debug] connecting to {WS_URL}")
    ws = websocket.create_connection(WS_URL, timeout=RUN_SECONDS + 30)
    ws.send(json.dumps({
        "APIKey": API_KEY,
        "BoundingBoxes": BUSY_BOUNDING_BOX,
        "FilterMessageTypes": ["PositionReport"],
    }))
    print(f"[debug] subscribed to Singapore Strait, listening for {RUN_SECONDS}s...")

    start = time.time()
    count = 0
    while time.time() - start < RUN_SECONDS:
        try:
            raw = ws.recv()
        except Exception as e:
            print(f"[debug] recv error: {e}")
            break
        if not raw:
            break
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if msg.get("MessageType") == "PositionReport":
            count += 1
            meta = msg.get("MetaData", {})
            print(f"[debug] #{count} MMSI={meta.get('MMSI')} ship={meta.get('ShipName')}")

    ws.close()
    print(f"\n[debug] DONE -- received {count} PositionReport messages in {RUN_SECONDS}s")


if __name__ == "__main__":
    run()
