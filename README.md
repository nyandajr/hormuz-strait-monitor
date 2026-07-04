# Strait of Hormuz Transit & Closure Monitor

Tracks vessel throughput through the Strait of Hormuz via live AIS data, using
a persistent ingestion worker (not GitHub Actions cron) so the numbers are
actually real-time rather than a periodic sample.

**[Live Dashboard →](https://nyandajr.github.io/hormuz-strait-monitor)**

## Why this isn't a GitHub Actions cron bot like the other repos

GitHub Actions runners are ephemeral — they can't hold a websocket connection
open between scheduled runs, and this session's own measurements showed
GitHub silently drops most sub-hourly `schedule` triggers anyway. Real AIS
tracking needs a connection that's actually always on. So the pieces split:

```
[Oracle Cloud VM, always on]
   ingest_worker.py (systemd, Restart=always)
        │ persistent websocket → AISStream.io, geofenced to the Strait
        ▼
   hormuz.db (SQLite, WAL mode)
        │
   aggregate_and_push.py  ← run from the VM's own crontab, e.g. every 15 min
        │ reads SQLite, computes snapshot, writes docs/data.json
        ▼
   git commit + push
        ▼
[GitHub repo — pure storage + Pages hosting, no ingestion logic]
```

## One-time setup

### 1. AISStream.io
Sign up at [aisstream.io](https://aisstream.io), grab your free API key.

### 2. Oracle Cloud Always Free VM
Create an account at [cloud.oracle.com](https://cloud.oracle.com) (needs a
card for identity verification — a temporary hold, not a charge, as long as
you stay within Always Free limits). Launch an **Always Free** Ampere A1 or
AMD Micro instance, Ubuntu image. Note its public IP.

### 3. On the VM
```bash
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/nyandajr/hormuz-strait-monitor.git
cd hormuz-strait-monitor
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

cp .env.example .env
nano .env   # fill in AISSTREAM_API_KEY, confirm the paths
```

### 4. Git push access from the VM
Generate a fine-grained GitHub PAT scoped to just this repo (Settings →
Developer settings → Personal access tokens → Fine-grained), contents:
read/write. Then on the VM:
```bash
git remote set-url origin https://<PAT>@github.com/nyandajr/hormuz-strait-monitor.git
```

### 5. Install the persistent worker as a service
```bash
sudo cp ingest/hormuz-ingest.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hormuz-ingest
sudo systemctl status hormuz-ingest   # confirm it's running
journalctl -u hormuz-ingest -f        # watch it receive messages
```

### 6. Schedule the aggregator on the VM's own crontab
```bash
crontab -e
```
Add:
```
*/15 * * * * cd /home/ubuntu/hormuz-strait-monitor && ./venv/bin/python aggregate/aggregate_and_push.py >> aggregate.log 2>&1
```

### 7. Enable GitHub Pages
Repo Settings → Pages → Source: `main` branch, `/docs` folder.

## Known limitations (be honest about these on the dashboard)

- `dwt_throughput_pct` is a **transit-count proxy**, not measured deadweight
  tonnage — AIS position reports don't carry cargo data. Don't present it as
  a precise DWT figure without pairing it with a real tonnage source.
- `vessels_underway_24h` only counts ships that emitted an AIS position
  report inside the bounding box in the last 24h — vessels running with AIS
  transponders off (common in this exact conflict zone for safety reasons)
  won't be counted. Worth a caveat on the dashboard itself.
- Baseline of 90 transits/day is a fixed constant from pre-crisis reporting,
  not pulled from a live source — revisit if better sourcing turns up.
