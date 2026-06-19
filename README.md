# Gridlock Intelligence

Event-driven congestion forecasting and patrol deployment for Bangalore traffic police.

Live: `https://gridlock-intelligence-575035862586.us-central1.run.app`

---

## Quickstart for evaluators

Five things to try, in order:

1. **Open the app** → Risk Zones tab loads automatically. 300 clusters, 2 patrol routes across 85,918 real Bangalore violation records.

2. **Events tab → IPL Match: RCB vs MI** → click it. You get a predicted risk score, an 8-point hour-by-hour forecast with the empirical phase curve, officer/barricade counts, and diversion suggestions. The green "Empirically derived" badge means this came from the Kaggle Bangalore Traffic Pulse dataset, not hardcoded constants.

3. **Hit "Run Impact Simulation"** → synthetic violations are injected near the match venue and the patrol routing reruns. Watch the map clusters shift.

4. **Spike detection** — already shown in the Events tab above the event list. These are grid cells where enforcement density in the last 3 days of the dataset exceeds the 21-day rolling baseline by 1.4×. No manual event creation needed.

5. **System tab → change Routing to OR-Tools VRP** → Apply & Recalculate. Compare the route layout against PuLP. Both solvers, both clustering engines (HDBSCAN / PostGIS ST_ClusterDBSCAN) are wired and switchable.

What's real: the 85,918 violation records, the phase curve calibration, the patrol routing math, the 1.98× spatial uplift validated on the RCB vs CSK match date (2024-03-28).
What's synthetic: event attendance figures, the violations injected when you hit "Run Impact Simulation".

---

## Problem

Planned and unplanned events (cricket matches, concerts, protests, accidents) create
predictable congestion spikes that traffic police currently respond to reactively.
This tool flips the posture: given an event, tell the officer where violations will
concentrate, by how much, when the peak hits, and exactly how many personnel and
barricades to send where.

| Requirement | What's built |
|---|---|
| Predict congestion from a planned event | 4-factor impact score: type weight × severity × log(attendance) + road closure penalty |
| Forecast hour-by-hour timeline | 8-point phase curve (T−2h → T+4h), calibrated from Kaggle Bangalore Traffic Pulse |
| Detect unplanned spikes automatically | Spatial anomaly scan: 0.01° grid cells, recent vs rolling baseline, configurable uplift threshold |
| Inject unplanned incidents | Synthetic violation insertion at any map coordinate via Scenario Simulation |
| Patrol routing | MILP (PuLP/CBC) or OR-Tools VRP; OSRM road-geometry routes |
| Learn from outcomes | `EventFeedback` stores actual vs predicted impact; effectiveness score; peer-event accuracy |
| Real spatial data | 85,918 peak-hour Bangalore parking violation records in PostGIS |

---

## Architecture

```mermaid
flowchart TD
    Browser["Browser\nReact 18 + deck.gl"]

    Browser -->|REST JSON same-origin| API

    subgraph API["FastAPI — Cloud Run PORT 8080"]
        spatial["spatial.py\nHDBSCAN / PostGIS ST_ClusterDBSCAN"]
        impact["impact.py\nHCM parking-adj + Erlang-B"]
        forecast["forecast.py\nEmpirical phase curve"]
        routing["routing.py\nPuLP MILP / OR-Tools VRP"]
        events["events.py\nImpactPrediction"]
        anomaly["live-incidents\nSpatial spike detection"]
    end

    API -->|SQLAlchemy + GeoAlchemy2\nUnix socket| DB

    subgraph DB["Cloud SQL — PostgreSQL 14 + PostGIS"]
        pv[police_violations]
        ev[events]
        ip[impact_predictions]
        ef[event_feedback]
    end

    subgraph Static["Baked into Docker image"]
        phasejson["phase_curve_empirical.json\nKaggle-calibrated"]
    end

    forecast --> phasejson
```

Single container: FastAPI serves `/api/v1/*` and the compiled React bundle from `StaticFiles`.
No cross-origin complexity, no separate frontend host.

---

## Data

### Police Violation Records

- Source: Bangalore police enforcement CSV (Jan–May)
- Raw: ~298,000 rows
- Filtered: **85,918** peak-hour (08–10h, 17–19h IST) parking violations with valid coordinates
- Table: `police_violations` with `GEOMETRY(Point, 4326)` PostGIS column

### Kaggle Bangalore Traffic Pulse

- [kaggle.com/datasets/preethamgouda/banglore-city-traffic-dataset](https://www.kaggle.com/datasets/preethamgouda/banglore-city-traffic-dataset)
- 8,936 rows across Bangalore roads (2022+)
- Used to calibrate the phase curve risk thresholds
- Key numbers: baseline TTI = 1.201, high-activity TTI = 1.412 → **1.176× travel-time uplift**; congestion level **1.812×** on high-activity days

### IPL Spatial Validation

Violation density within 1 km of M. Chinnaswamy Stadium on RCB 2024 home match nights
vs the prior 7-day rolling average:

| Date | Fixture | Match-night violations | 7-day baseline | Uplift |
|---|---|---|---|---|
| 2024-03-22 | RCB vs PBKS | 48 | 108.8 | 0.44× |
| 2024-03-25 | RCB vs GT | 109 | 126.6 | 0.86× |
| **2024-03-28** | **RCB vs CSK** | **247** | **124.8** | **1.98×** |
| 2024-04-07 | RCB vs SRH | 83 | 98.9 | 0.84× |

The RCB vs CSK date shows **2× overnight parking violations** vs baseline.
Timestamps are enforcement patrol logs (02–06h IST), so uplift is observed the
morning after the match. Pass `as_of=2024-03-28` to the `/api/v1/live-incidents`
endpoint to replay the auto-detection.

---

## Impact Score Formula

Computed per spatial cluster from violation density:

```
fp = (N_sat - 0.1 - 18 × Nm) / N_sat    # HCM parking adjustment
                                           # N_sat = 1900 veh/h/ln (HCM 6th Ed.)
                                           # Nm = effective maneuvers/peak-hour
                                           # fp clamped [0.05, 1.0]

offered_load = (1/fp - 1) × 1.6          # Erlang-B offered traffic load
B = offered_load / (1 + offered_load)     # M/M/1/1 blocking probability

hcm_score    = (1 - fp) × 100 × 0.45    # 45% — lane capacity loss
erlang_score = B        × 100 × 0.35    # 35% — stochastic obstruction
vehicle_score = vehicle_severity × 0.20  # 20% — heavy vs light vehicles

impact_score = clamp(hcm_score + erlang_score + vehicle_score, 0, 100)
```

Vehicle severity: tankers/buses/lorries +50%, scooters −15%; clamped [0.85, 1.50].

`PEAK_MANEUVER_CALIBRATION = 12.0`: each recorded violation proxies ~10 real events
spread across 8 peak hours → ~1.25 effective maneuvers/peak-hour per violation/day.

---

## Event Impact Prediction

When an event is created an `ImpactPrediction` is generated immediately:

```
base    = event_type_weight × 15
          (protest=1.7, accident=1.6, political/rally=1.5, festival=1.4, sports=1.3 …)

score   = base
        + base × (severity_mult − 1)        # critical=2.0, high=1.5, medium=1.0, low=0.5
        + base × severity_mult × max(0, log₁₀(attendance) − 1)
        + 20 if road closure required

score   = clamp(score, 0, 100)
```

---

## Congestion Forecast

### Phase Curve

Derived from Kaggle dataset (N=7,407 high-activity observations, N=1,529 baseline):

| Horizon | Phase fraction | Recommended action |
|---|---|---|
| T−2h | 0.12 | Routine monitoring |
| T−1h | 0.38 | Pre-deploy monitoring team |
| T−30m | 0.68 | Activate barricades |
| T+0 | 1.00 | Full deployment |
| T+1h | 0.80 | Maintain |
| T+2h | 0.55 | Begin gradual stand-down |
| T+3h | 0.28 | Skeleton crew |
| T+4h | 0.09 | Routine monitoring |

`risk_score = event_impact_score × phase_fraction`

### Risk Thresholds

Thresholds are set above generic HCM defaults because Bangalore's baseline TTI is
already 1.2 — the city is congested on a normal day:

| Level | Threshold | vs HCM default |
|---|---|---|
| Critical | ≥ 70 | +5 |
| High | ≥ 50 | +5 |
| Medium | ≥ 28 | +3 |

---

## Live Incident Detection

`GET /api/v1/live-incidents` — spatial-temporal anomaly scan:

1. Bucket all violations into 0.01° grid cells (~1 km²)
2. Count violations in a recent window (default: last 3 days relative to dataset max)
3. Compare to a baseline window (default: prior 21 days)
4. Return cells where `recent_daily_rate / baseline_daily_rate ≥ min_uplift` (default 1.4×)

The Events tab shows detected hotspots with their uplift ratio and auto-refreshes
every 60 seconds.

Demo mode: `?as_of=2024-03-28` reproduces the RCB vs CSK spike detection from the
violation dataset.

---

## Patrol Routing

Two solvers, switchable from the System tab:

**PuLP MILP (default)**  
MTZ subtour elimination. Falls back to greedy when `vehicles × N × (N+1) > 1200`.
Configurable time limit (default 5 s).

**OR-Tools VRP**  
Google OR-Tools SCIP, first-solution heuristic + local search. Better for larger
candidate sets where MILP is slow.

Road geometry from OSRM; straight-line fallback when OSRM is unreachable.

---

## Clustering

**HDBSCAN (default)** — density-based, adapts to variable urban density.
`min_cluster_size=15`, `min_samples=5` (configurable). Hourly slice: thresholds
auto-scaled to `max(3, min_cluster_size // 4)`.

**PostGIS ST_ClusterDBSCAN** — fixed `eps=35m` radius (EPSG:3857). Catches
smaller junctions (8–10 violations) that HDBSCAN filters out.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/health` | Liveness check |
| GET | `/api/v1/congestion-zones` | Full pipeline: cluster → score → route |
| POST | `/api/v1/simulate-anomaly` | Inject synthetic violation, re-run pipeline |
| GET | `/api/v1/live-incidents` | Spatial spike detection vs rolling baseline |
| GET | `/api/v1/events` | List all events |
| POST | `/api/v1/events` | Create event + ImpactPrediction |
| GET | `/api/v1/events/{id}` | Event detail |
| POST | `/api/v1/events/{id}/activate` | Inject violations, run pipeline |
| GET | `/api/v1/events/{id}/forecast` | Phase curve timeline (8 horizons) |
| POST | `/api/v1/events/{id}/feedback` | Submit post-event outcome |
| GET | `/api/v1/events/{id}/learning` | Predicted vs actual + peer accuracy |
| GET | `/api/v1/dashboard-summary` | Active events, learning stats |
| POST | `/api/v1/seed-events` | Re-seed 9 sample events |

`/api/v1/congestion-zones` query parameters:

| Parameter | Default | Range | Notes |
|---|---|---|---|
| `min_cluster_size` | 15 | 3–200 | HDBSCAN minimum cluster size |
| `min_samples` | 5 | 1–100 | HDBSCAN minimum samples |
| `patrol_vehicles` | 2 | 1–6 | Patrol cars to route |
| `max_stops` | 5 | 1–12 | Stops per vehicle |
| `candidate_limit` | 18 | 3–30 | Top-N clusters for routing |
| `distance_penalty` | 14.0 | 0–100 | Travel distance weight |
| `solver_time_limit` | 5.0 | 1–15 | MILP time limit (s) |
| `time_hour` | null | 0–23 | Filter to a specific hour |
| `clustering_engine` | `hdbscan` | `hdbscan`, `postgis` | Clustering algorithm |
| `routing_engine` | `pulp` | `pulp`, `ortools` | Route optimizer |
| `route_geometry` | `road` | `road`, `straight` | OSRM vs straight-line |

`/api/v1/live-incidents` query parameters:

| Parameter | Default | Range | Notes |
|---|---|---|---|
| `window_days` | 3 | 1–14 | Recent window size |
| `baseline_days` | 21 | 7–60 | Baseline window size |
| `min_uplift` | 1.4 | 1.1–5.0 | Minimum rate uplift to flag |
| `as_of` | dataset max | ISO date | Simulate detection at a past date |

---

## What Is Simulated vs. Real

| Component | Status |
|---|---|
| Violation dataset | Real — 85,918 peak-hour parking violations, Bangalore Jan–May |
| Phase curve calibration | Empirical — 8,936 rows, Kaggle Bangalore Traffic Pulse, 1.176× TTI uplift |
| IPL spatial validation | Real — 1.98× uplift within 1 km of Chinnaswamy on 2024-03-28 |
| Live incident detection | Real data, real algorithm — window/baseline comparison on the actual violation table |
| Event attendance | Realistic estimates — not live ticketing |
| Synthetic incident injection | Labeled "Synthetic scenario" in UI |
| Patrol routing | Real MILP/VRP on real spatial clusters |
| Road geometry | Real OSRM Bangalore network; straight-line fallback |
| Post-event feedback | 3 seeded historical examples + live submission |

---

## Running Locally

Prerequisites: Python 3.10+, Node 20+, PostgreSQL 14+ with PostGIS, `coinor-cbc`.

```bash
# Backend
cd backend
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/congestion_db
uvicorn main:app --reload --port 8000

# PostGIS (once)
psql $DATABASE_URL -c "CREATE EXTENSION IF NOT EXISTS postgis;"

# Ingest violations
python ingest_data.py --csv-file "jan to may police violation_anonymized791b166.csv"

# Seed events
curl -X POST http://localhost:8000/api/v1/seed-events
```

```bash
# Frontend
cd frontend
npm install
npm run dev   # http://localhost:5173
```

`VITE_API_BASE_URL` defaults to `http://localhost:8000`. In the Docker build it's set to `""`.

---

## GCP Deployment

Cloud Run (single container) + Cloud SQL PostgreSQL 14 (public IP, Cloud SQL Auth Proxy).

Requirements: `gcloud` authenticated, Docker Desktop, Cloud SDK 573.0.0+.
`gcloud auth application-default login` is **not** required — the proxy uses `--gcloud-auth`.

```powershell
gcloud config set project YOUR_PROJECT_ID
.\deploy.ps1
```

`deploy.ps1` handles, in order:

1. Enable APIs (Cloud Run, Cloud SQL, Artifact Registry, Secret Manager, Storage)
2. Create Artifact Registry repo
3. Create Cloud SQL PostgreSQL 14 instance
4. Create DB + user
5. Write `DATABASE_URL` to Secret Manager (no-BOM UTF-8 via `[System.Text.UTF8Encoding]::new($false)`)
6. Build multi-stage Docker image (Node 20 → React; Python 3.10 → FastAPI)
7. Push to Artifact Registry
8. Deploy to Cloud Run with Cloud SQL socket mount
9. Start proxy with `--gcloud-auth`, create PostGIS extension, grant superuser
10. Ingest violations through proxy
11. Seed events

### Manual ingestion (proxy already running)

```powershell
$env:DATABASE_URL = "postgresql://gridlock_user:PASS@localhost:5433/congestion_db"
python backend\ingest_data.py
Invoke-RestMethod -Uri "https://YOUR-URL/api/v1/seed-events" -Method POST
```

---

## Phase Curve Recalibration

```bash
kaggle datasets download preethamgouda/banglore-city-traffic-dataset --unzip

python backend/calibrate_phase_curve.py \
  --kaggle Banglore_traffic_Dataset.csv \
  --violations "jan to may police violation_anonymized791b166.csv"

# Rebuild + redeploy (JSON is baked into the image)
.\deploy.ps1
```

The script handles datasets without a `Special Events` column by proxying
high-activity rows as `Congestion Level > 70%` or `Incident Reports > 0`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI 0.110 + Uvicorn |
| ORM | SQLAlchemy 2.0 + GeoAlchemy2 |
| Database | PostgreSQL 14 + PostGIS |
| Clustering | HDBSCAN · PostGIS ST_ClusterDBSCAN |
| MILP | PuLP 2.x + CBC |
| VRP | Google OR-Tools |
| Road routing | OSRM |
| Frontend | React 18 + Vite |
| Map | deck.gl + MapLibre GL + CARTO dark basemap |
| Icons | Lucide React |
| Container | Docker multi-stage (Node 20 Alpine + Python 3.10 slim-bullseye) |
| Cloud | Cloud Run · Cloud SQL · Artifact Registry · Secret Manager · Cloud Storage |

---

## Limitations

- No live feeds — no cameras, GPS, or sensor ingestion. Pre-event decision support.
- Single city — `DEFAULT_DEPOT` and road calibration are Bangalore-specific.
- HDBSCAN `min_cluster_size=15` suppresses junctions with 8–14 violations; switch to PostGIS engine for finer granularity.
- OSRM falls back to straight lines when the public API is unreachable.
