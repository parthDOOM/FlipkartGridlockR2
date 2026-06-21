# Gridlock Intelligence

Event-driven congestion forecasting and patrol deployment for Bangalore traffic police.

**Live demo:** `https://gridlock-intelligence-575035862586.us-central1.run.app`  
**Repository:** `https://github.com/parthDOOM/FlipkartGridlockR2`

---

## Quickstart for evaluators

1. **Open the app** — Risk Zones tab loads automatically. 349 clusters, 2 patrol routes, derived from 85,918 real Bangalore violation records. First load ~8 s; cached after that.

2. **Events tab → IPL Match: RCB vs MI** — click it. You get a predicted risk score, an 8-point hour-by-hour phase curve with wall-clock times on each bar, officer/barricade counts, and diversion suggestions. The green badge means the phase curve came from the Kaggle Bangalore Traffic Pulse dataset. Click **Export Deployment Order** to download a printable HTML briefing with the full forecast table and resource plan.

3. **Hit "Run Impact Simulation"** — synthetic violations are injected near the venue and patrol routing reruns. Watch the map clusters shift.

4. **Spike Detection panel** (Events tab, above the event list) — grid cells where enforcement density in the last 3 days exceeds the 21-day rolling baseline by ≥1.4×. Pass `?as_of=2024-03-28` to `/api/v1/live-incidents` to replay the RCB vs CSK match-night spike.

5. **System tab → switch Routing to OR-Tools VRP** → Apply & Recalculate. Both solvers (PuLP MILP, OR-Tools VRP) and both clustering engines (HDBSCAN, PostGIS ST_ClusterDBSCAN) are live.

**What's real:** 85,918 violation records, phase curve calibration, patrol routing math, 1.98× spatial uplift validated on the RCB vs CSK match date (2024-03-28).  
**What's synthetic:** event attendance figures, violations injected when you hit "Run Impact Simulation".

---

## Problem

Planned and unplanned events create predictable congestion spikes that traffic police currently respond to reactively. This tool flips the posture: given an event, tell the officer where violations will concentrate, by how much, when the peak hits, and exactly how many personnel and barricades to send where.

| Requirement | What's built |
|---|---|
| Predict congestion from a planned event | 4-factor impact score: type weight × severity × log(attendance) + road closure penalty |
| Forecast hour-by-hour timeline | 8-point phase curve (T−2h → T+4h) with wall-clock timestamps, calibrated from Kaggle Bangalore Traffic Pulse |
| Detect unplanned spikes automatically | Spatial anomaly scan: 0.01° grid cells, recent vs rolling baseline, configurable uplift threshold |
| Inject unplanned incidents | Synthetic violation insertion at any map coordinate via Scenario Simulation |
| Patrol routing | MILP (PuLP/CBC) or OR-Tools VRP; OSRM road-geometry routes |
| Learn from outcomes | `EventFeedback` stores actual vs predicted impact; effectiveness score; peer-event accuracy; `observation_notes`; auto-generated insight text |
| Export Deployment Order | One-click printable HTML briefing: forecast table, resource counts, diversion plan |
| Cluster capacity metrics | Map tooltip shows HCM f_p (parking adjustment factor) and Travel Time Index; hotspot list shows dominant vehicle type and heavy-vehicle count |
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

Single container: FastAPI serves `/api/v1/*` and the compiled React bundle from `StaticFiles`. No cross-origin complexity, no separate frontend host.

---

## Data

### Police Violation Records

- Source: Bangalore police enforcement CSV (Jan–May 2024)
- Raw: ~298,000 rows
- Filtered: **85,918** peak-hour (08–10h, 17–19h IST) parking violations with valid coordinates
- Table: `police_violations` with `GEOMETRY(Point, 4326)` PostGIS column

### Kaggle Bangalore Traffic Pulse

- [kaggle.com/datasets/preethamgouda/banglore-city-traffic-dataset](https://www.kaggle.com/datasets/preethamgouda/banglore-city-traffic-dataset)
- 8,936 rows across Bangalore roads (2022+)
- Used to calibrate the phase curve risk thresholds
- Key: baseline TTI = 1.201, high-activity TTI = 1.412 → **1.176× travel-time uplift**; congestion level **1.812×** on high-activity days

### IPL Spatial Validation

Violation density within 1 km of M. Chinnaswamy Stadium on RCB 2024 home match nights vs the prior 7-day rolling average:

| Date | Fixture | Match-night violations | 7-day baseline | Uplift |
|---|---|---|---|---|
| 2024-03-22 | RCB vs PBKS | 48 | 108.8 | 0.44× |
| 2024-03-25 | RCB vs GT | 109 | 126.6 | 0.86× |
| **2024-03-28** | **RCB vs CSK** | **247** | **124.8** | **1.98×** |
| 2024-04-07 | RCB vs SRH | 83 | 98.9 | 0.84× |

The RCB vs CSK date shows **2× overnight parking violations** vs baseline. Timestamps are enforcement patrol logs (02–06h IST) — uplift is observed the morning after the match.

---

## Impact Score Formula

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

---

## Event Impact Prediction

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

Thresholds are set above generic HCM defaults because Bangalore's baseline TTI is already 1.2 — the city is congested on a normal day:

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

Demo mode: `?as_of=2024-03-28` reproduces the RCB vs CSK spike from the violation dataset.

---

## Patrol Routing

Two solvers, switchable from the System tab:

**PuLP MILP (default)** — MTZ subtour elimination. Falls back to greedy when `vehicles × N × (N+1) > 1200`. Default time limit 2 s.

**OR-Tools VRP** — Google OR-Tools SCIP, first-solution heuristic + local search. Better for larger candidate sets.

Road geometry from OSRM; straight-line fallback when OSRM is unreachable.

---

## Clustering

**HDBSCAN (default)** — density-based, adapts to variable urban density. Coordinates are pre-aggregated to a 0.0001° grid (~10 m) in SQL before clustering, reducing HDBSCAN input from 85k raw rows to ~10–15k unique spatial cells with no loss of cluster quality.

**PostGIS ST_ClusterDBSCAN** — fixed `eps=35m` radius (EPSG:3857). Catches smaller junctions that HDBSCAN filters out.

---

## Performance

| Stage | Cold (no cache) | Warm (cached) |
|---|---|---|
| Spatial clustering | ~5.5 s | — |
| Impact scoring | ~60 ms | — |
| MILP routing | ~2.5 s | — |
| **Total pipeline** | **~8–10 s** | **<2 s** |

The pipeline result is cached for 30 minutes. A background warmup task runs at startup so the first visitor typically hits the cache.

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
| POST | `/api/v1/events/{id}/feedback` | Submit post-event outcome (`actual_impact_score`, `actual_severity`, `observation_notes`) |
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
| `solver_time_limit` | 2.0 | 1–15 | MILP time limit (s) |
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
| Violation dataset | Real — 85,918 peak-hour parking violations, Bangalore Jan–May 2024 |
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

`VITE_API_BASE_URL` defaults to `http://localhost:8000`. In the Docker build it is set to `""`.

---

## GCP Deployment

Cloud Run (single container) + Cloud SQL PostgreSQL 14 (public IP, Cloud SQL Auth Proxy).

```powershell
gcloud config set project YOUR_PROJECT_ID
.\deploy.ps1
```

`deploy.ps1` handles APIs, Artifact Registry, Cloud SQL instance, Secret Manager, Docker build, push, deploy, PostGIS extension, and data ingestion in sequence.

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
| Cloud | Cloud Run · Cloud SQL · Artifact Registry · Secret Manager |

---

## Limitations

- No live feeds — no cameras, GPS, or sensor ingestion. Pre-event decision support only.
- Single city — `DEFAULT_DEPOT` and road calibration are Bangalore-specific.
- OSRM falls back to straight lines when the public API is unreachable.
