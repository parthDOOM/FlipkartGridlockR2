import os
import time
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from geoalchemy2.elements import WKTElement
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db, engine, Base
from models import PoliceViolation, Event, ImpactPrediction, EventFeedback
from services.events import create_event_with_prediction, calculate_event_impact
from seed_events import run_seed_events
from services.impact import calculate_impact_scores
from services.routing import (
    DEFAULT_DEPOT,
    optimize_patrol_routes,
    optimize_patrol_routes_ortools,
)
from services.spatial import (
    ACTIVE_PARKING_VIOLATIONS,
    active_parking_filter,
    get_congestion_clusters,
    get_postgis_clusters,
)

def _db_init_sync():
    from sqlalchemy import text as _text
    with engine.begin() as conn:
        conn.execute(_text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(bind=engine)


def _warmup_cache():
    """Run the default pipeline once at startup to populate the in-process cache."""
    try:
        from database import SessionLocal
        db = SessionLocal()
        try:
            run_congestion_pipeline(
                db,
                min_cluster_size=15, min_samples=5,
                patrol_vehicles=2, max_stops=5,
                candidate_limit=18, distance_penalty=14.0,
                map_cluster_limit=300, route_geometry="road",
                solver_time_limit=2.0,
            )
            print("[startup] pipeline cache warmed")
        finally:
            db.close()
    except Exception as exc:
        print(f"[startup] warmup skipped: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    try:
        await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _db_init_sync),
            timeout=20.0,
        )
    except Exception as exc:
        print(f"[startup] DB init skipped: {exc}")
    # Warm the pipeline cache in a background thread — first HTTP request
    # hits the cache instead of waiting 20-30 s for clustering + routing.
    asyncio.get_event_loop().run_in_executor(None, _warmup_cache)
    yield


app = FastAPI(title="Gridlock Intelligence", version="3.0.0", lifespan=lifespan)
PIPELINE_CACHE_TTL_SECONDS = 1800
_PIPELINE_CACHE = {}

_ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


# Starlette 0.27 does not inject CORS headers into unhandled-exception 500 responses.
# This handler ensures the frontend always gets a readable error, not a CORS block.
from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
        headers={"Access-Control-Allow-Origin": "*"},
    )


class AnomalyRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    vehicle_type: str = Field(..., min_length=1, max_length=80)
    violation_type: str = Field(default="WRONG PARKING", min_length=1, max_length=160)
    event_type: str = Field(default="SINGLE", min_length=1, max_length=50)

class EventCreate(BaseModel):
    title: str
    event_type: str
    description: str = None
    latitude: float
    longitude: float
    start_time: datetime
    end_time: datetime = None
    expected_attendance: int = 0
    severity: str = "medium"
    is_planned: bool = True
    road_closure_required: bool = False

class EventFeedbackSubmit(BaseModel):
    actual_impact_score: float
    actual_severity: str
    observation_notes: str = None


def _is_active_violation(violation_type: str):
    normalized = violation_type.upper()
    return any(violation in normalized for violation in ACTIVE_PARKING_VIOLATIONS)


def _pipeline_parameters(
    min_cluster_size: int,
    min_samples: int,
    patrol_vehicles: int,
    max_stops: int,
    candidate_limit: int,
    distance_penalty: float,
    map_cluster_limit: int,
    route_geometry: str,
    solver_time_limit: float,
    time_hour: int = None,
    clustering_engine: str = "hdbscan",
    routing_engine: str = "pulp",
):
    return {
        "min_cluster_size": min_cluster_size,
        "min_samples": min_samples,
        "patrol_vehicles": patrol_vehicles,
        "max_stops": max_stops,
        "candidate_limit": candidate_limit,
        "distance_penalty": distance_penalty,
        "map_cluster_limit": map_cluster_limit,
        "route_geometry": route_geometry,
        "solver_time_limit": solver_time_limit,
        "time_hour": time_hour,
        "clustering_engine": clustering_engine,
        "routing_engine": routing_engine,
        "depot": DEFAULT_DEPOT,
    }


def _data_signature(db: Session, time_hour: int = None):
    query = db.query(func.count(PoliceViolation.id), func.max(PoliceViolation.created_datetime)) \
              .filter(active_parking_filter())

    if time_hour is not None:
        query = query.filter(func.extract('hour', PoliceViolation.created_datetime) == time_hour)

    count, latest = query.one()
    return int(count or 0), latest.isoformat() if latest else None


def _observation_hours(db: Session, time_hour: int = None) -> float:
    """
    Returns the effective observation window (hours) for N_m normalization.
    Excludes SIM- injected violations so that simulate-anomaly / activate-event
    calls don't extend the baseline and collapse real impact scores.
    """
    min_dt, max_dt = db.query(
        func.min(PoliceViolation.created_datetime),
        func.max(PoliceViolation.created_datetime),
    ).filter(
        active_parking_filter(),
        ~PoliceViolation.id.like("SIM-%"),
    ).one()

    if not min_dt or not max_dt or min_dt == max_dt:
        return 24.0  # single-day fallback

    total_hours = (max_dt - min_dt).total_seconds() / 3600.0
    if time_hour is not None:
        # Each day contributes exactly 1 hour for this slot.
        return max(1.0, total_hours / 24.0)
    return max(1.0, total_hours)


def _compact_cluster(cluster, routed_cluster_ids):
    compact = {
        key: value
        for key, value in cluster.items()
        if key not in {"vehicle_types", "violation_types"}
    }
    compact["is_routed"] = compact["cluster_id"] in routed_cluster_ids
    return compact


def _cache_get(cache_key):
    cached = _PIPELINE_CACHE.get(cache_key)
    if not cached:
        return None

    created_at, payload = cached
    if time.time() - created_at > PIPELINE_CACHE_TTL_SECONDS:
        _PIPELINE_CACHE.pop(cache_key, None)
        return None

    response = deepcopy(payload)
    response["metrics"]["cache_hit"] = True
    return response


def _cache_set(cache_key, payload):
    if len(_PIPELINE_CACHE) > 50:
        oldest_key = min(_PIPELINE_CACHE, key=lambda key: _PIPELINE_CACHE[key][0])
        _PIPELINE_CACHE.pop(oldest_key, None)
    _PIPELINE_CACHE[cache_key] = (time.time(), deepcopy(payload))


def run_congestion_pipeline(
    db: Session,
    min_cluster_size: int,
    min_samples: int,
    patrol_vehicles: int,
    max_stops: int,
    candidate_limit: int,
    distance_penalty: float,
    map_cluster_limit: int,
    route_geometry: str,
    solver_time_limit: float,
    time_hour: int = None,
    clustering_engine: str = "hdbscan",
    routing_engine: str = "pulp",
    use_cache: bool = True,
    obs_hours_override: float = None,
):
    route_geometry = route_geometry.lower()
    cache_key = (
        _data_signature(db, time_hour),
        min_cluster_size,
        min_samples,
        patrol_vehicles,
        max_stops,
        candidate_limit,
        round(distance_penalty, 3),
        map_cluster_limit,
        route_geometry,
        round(solver_time_limit, 2),
        time_hour,
        clustering_engine,
        routing_engine,
    )

    if use_cache:
        cached = _cache_get(cache_key)
        if cached:
            return cached

    t0 = time.perf_counter()
    
    # Hourly slices are ~1/24 of total data; scale thresholds so sparse hours still cluster.
    if time_hour is not None:
        effective_min_cluster_size = max(3, min_cluster_size // 4)
        effective_min_samples = max(2, min_samples // 2)
    else:
        effective_min_cluster_size = min_cluster_size
        effective_min_samples = min_samples

    if clustering_engine == "postgis":
        # Map min_cluster_size → eps_meters: larger clusters require wider search radius.
        # Range: min_cluster_size 3 → 20m, 200 → 80m, default 15 → ~35m.
        postgis_eps = max(20.0, min(80.0, 15.0 + min_cluster_size * 1.35))
        clusters = get_postgis_clusters(
            db,
            eps_meters=postgis_eps,
            min_samples=effective_min_samples,
            time_hour=time_hour
        )
    else:
        clusters = get_congestion_clusters(
            db,
            min_cluster_size=effective_min_cluster_size,
            min_samples=effective_min_samples,
            time_hour=time_hour
        )
    t1 = time.perf_counter()

    obs_hours = obs_hours_override if obs_hours_override is not None else _observation_hours(db, time_hour)
    scored_clusters = calculate_impact_scores(clusters, observation_hours=obs_hours)
    t2 = time.perf_counter()

    if routing_engine == "ortools":
        routes = optimize_patrol_routes_ortools(
            scored_clusters,
            vehicle_count=patrol_vehicles,
            max_stops=max_stops,
            distance_penalty=distance_penalty,
            candidate_limit=candidate_limit,
            time_limit_seconds=solver_time_limit,
            route_geometry=route_geometry,
        )
    else:
        routes = optimize_patrol_routes(
            scored_clusters,
            vehicle_count=patrol_vehicles,
            max_stops=max_stops,
            distance_penalty=distance_penalty,
            candidate_limit=candidate_limit,
            time_limit_seconds=solver_time_limit,
            route_geometry=route_geometry,
        )
    t3 = time.perf_counter()

    critical_clusters = sum(1 for cluster in scored_clusters if cluster["impact_score"] >= 45)
    total_impact = round(sum(cluster["impact_score"] for cluster in scored_clusters), 2)
    total_capacity_gain = sum(cluster.get("capacity_recovery_vph", 0) for cluster in scored_clusters)
    
    total_recovery_benefit = sum(
        cluster.get("intervention_benefit", {}).get("recovery_metrics", {}).get("estimated_capacity_recovered_vph", 0)
        for cluster in scored_clusters
    )
    
    routed_cluster_ids = {
        stop["cluster_id"]
        for route in routes
        for stop in route["stops"]
    }
    visible_clusters = [
        _compact_cluster(cluster, routed_cluster_ids)
        for cluster in scored_clusters[:map_cluster_limit]
    ]

    response = {
        "clusters": visible_clusters,
        "routes": routes,
        "summary": {
            "active_hotspots": len(scored_clusters),
            "returned_hotspots": len(visible_clusters),
            "critical_hotspots": critical_clusters,
            "total_network_impact": total_impact,
            "total_capacity_gain_vph": total_capacity_gain,
            "total_expected_recovery_vph": round(total_recovery_benefit, 0),
            "routed_patrols": len(routes),
            "routed_stops": sum(route["stop_count"] for route in routes),
            "road_routed_patrols": sum(1 for route in routes if route.get("geometry_source") == "road"),
            "patrol_coverage_percent": round((sum(stop["impact_score"] for route in routes for stop in route["stops"]) / max(1, total_impact)) * 100, 1),
        },
        "metrics": {
            "spatial_clustering_ms": round((t1 - t0) * 1000, 2),
            "impact_scoring_ms": round((t2 - t1) * 1000, 2),
            "milp_routing_ms": round((t3 - t2) * 1000, 2),
            "total_pipeline_ms": round((t3 - t0) * 1000, 2),
            "cache_hit": False,
        },
        "parameters": _pipeline_parameters(
            min_cluster_size,
            min_samples,
            patrol_vehicles,
            max_stops,
            candidate_limit,
            distance_penalty,
            map_cluster_limit,
            route_geometry,
            solver_time_limit,
            time_hour,
            clustering_engine,
            routing_engine,
        ),
    }

    if use_cache:
        _cache_set(cache_key, response)

    return response


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/congestion-zones")
def get_zones(
    min_cluster_size: int = Query(default=15, ge=3, le=200),
    min_samples: int = Query(default=5, ge=1, le=100),
    patrol_vehicles: int = Query(default=2, ge=1, le=6),
    max_stops: int = Query(default=5, ge=1, le=12),
    candidate_limit: int = Query(default=18, ge=3, le=30),
    distance_penalty: float = Query(default=14.0, ge=0.0, le=100.0),
    map_cluster_limit: int = Query(default=300, ge=50, le=1500),
    route_geometry: str = Query(default="road", pattern="^(road|straight)$"),
    solver_time_limit: float = Query(default=2.0, ge=1.0, le=15.0),
    time_hour: int = Query(default=None, ge=0, le=23),
    clustering_engine: str = Query(default="hdbscan", pattern="^(hdbscan|postgis)$"),
    routing_engine: str = Query(default="pulp", pattern="^(pulp|ortools)$"),
    db: Session = Depends(get_db),
):
    return run_congestion_pipeline(
        db,
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        patrol_vehicles=patrol_vehicles,
        max_stops=max_stops,
        candidate_limit=candidate_limit,
        distance_penalty=distance_penalty,
        map_cluster_limit=map_cluster_limit,
        route_geometry=route_geometry,
        solver_time_limit=solver_time_limit,
        time_hour=time_hour,
        clustering_engine=clustering_engine,
        routing_engine=routing_engine,
    )


@app.post("/api/v1/simulate-anomaly")
def simulate_anomaly(
    req: AnomalyRequest,
    min_cluster_size: int = Query(default=15, ge=3, le=200),
    min_samples: int = Query(default=5, ge=1, le=100),
    patrol_vehicles: int = Query(default=2, ge=1, le=6),
    max_stops: int = Query(default=5, ge=1, le=12),
    candidate_limit: int = Query(default=18, ge=3, le=30),
    distance_penalty: float = Query(default=14.0, ge=0.0, le=100.0),
    map_cluster_limit: int = Query(default=300, ge=50, le=1500),
    route_geometry: str = Query(default="road", pattern="^(road|straight)$"),
    solver_time_limit: float = Query(default=2.0, ge=1.0, le=15.0),
    time_hour: int = Query(default=None, ge=0, le=23),
    clustering_engine: str = Query(default="hdbscan", pattern="^(hdbscan|postgis)$"),
    routing_engine: str = Query(default="pulp", pattern="^(pulp|ortools)$"),
    db: Session = Depends(get_db),
):
    if not _is_active_violation(req.violation_type):
        raise HTTPException(
            status_code=400,
            detail="violation_type must include WRONG PARKING, DOUBLE PARKING, or NO PARKING",
        )

    t_insert_start = time.perf_counter()
    
    violations_to_insert = []
    
    if req.event_type == "TANKER_SPILL":
        # Insert a cluster of heavy vehicles
        base_id = str(uuid.uuid4())[:8]
        for i in range(12):
            violations_to_insert.append(PoliceViolation(
                id=f"SIM-TANKER-{base_id}-{i}",
                latitude=req.latitude + (np.random.random() - 0.5) * 0.002,
                longitude=req.longitude + (np.random.random() - 0.5) * 0.002,
                violation_type="WRONG PARKING",
                vehicle_type="TANKER",
                created_datetime=datetime.now(timezone.utc),
                geom=WKTElement(f"POINT({req.longitude} {req.latitude})", srid=4326),
            ))
    elif req.event_type == "STADIUM_SURGE":
        base_id = str(uuid.uuid4())[:8]
        for i in range(40):
            violations_to_insert.append(PoliceViolation(
                id=f"SIM-STADIUM-{base_id}-{i}",
                latitude=req.latitude + (np.random.random() - 0.5) * 0.005,
                longitude=req.longitude + (np.random.random() - 0.5) * 0.005,
                violation_type="NO PARKING",
                vehicle_type="CAR",
                created_datetime=datetime.now(timezone.utc),
                geom=WKTElement(f"POINT({req.longitude} {req.latitude})", srid=4326),
            ))
    else:
        # Default single anomaly
        violations_to_insert.append(PoliceViolation(
            id=f"SIM-{uuid.uuid4()}",
            latitude=req.latitude,
            longitude=req.longitude,
            violation_type=req.violation_type.upper(),
            vehicle_type=req.vehicle_type.upper(),
            created_datetime=datetime.now(timezone.utc),
            geom=WKTElement(f"POINT({req.longitude} {req.latitude})", srid=4326),
        ))

    db.add_all(violations_to_insert)
    db.commit()
    t_insert_end = time.perf_counter()
    _PIPELINE_CACHE.clear()

    response = run_congestion_pipeline(
        db,
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        patrol_vehicles=patrol_vehicles,
        max_stops=max_stops,
        candidate_limit=candidate_limit,
        distance_penalty=distance_penalty,
        map_cluster_limit=map_cluster_limit,
        route_geometry=route_geometry,
        solver_time_limit=solver_time_limit,
        time_hour=time_hour,
        clustering_engine=clustering_engine,
        routing_engine=routing_engine,
        use_cache=False,
    )
    response["status"] = "success"
    response["inserted_count"] = len(violations_to_insert)
    response["metrics"]["db_insert_ms"] = round((t_insert_end - t_insert_start) * 1000, 2)
    response["metrics"]["total_with_insert_ms"] = round(
        response["metrics"]["total_pipeline_ms"] + response["metrics"]["db_insert_ms"],
        2,
    )
    return response


_EVENT_VIOLATION_COUNTS = {
    "sports": 45, "concert": 40, "festival": 35, "political": 30,
    "accident": 25, "construction": 20, "rally": 30, "protest": 28, "standard": 15,
}
_SEVERITY_SCALE = {"low": 0.3, "medium": 0.6, "high": 1.0, "critical": 1.5}


@app.post("/api/v1/events/{event_id}/activate")
def activate_event(
    event_id: str,
    patrol_vehicles: int = Query(default=2, ge=1, le=6),
    max_stops: int = Query(default=5, ge=1, le=12),
    candidate_limit: int = Query(default=18, ge=3, le=30),
    distance_penalty: float = Query(default=14.0, ge=0.0, le=100.0),
    map_cluster_limit: int = Query(default=300, ge=50, le=1500),
    route_geometry: str = Query(default="road", pattern="^(road|straight)$"),
    solver_time_limit: float = Query(default=2.0, ge=1.0, le=15.0),
    routing_engine: str = Query(default="pulp", pattern="^(pulp|ortools)$"),
    db: Session = Depends(get_db),
):
    import math as _math
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    base_count = _EVENT_VIOLATION_COUNTS.get(event.event_type.lower(), 20)
    severity_scale = _SEVERITY_SCALE.get(event.severity.lower(), 1.0)
    attendance_factor = 1.0
    if event.expected_attendance > 0:
        attendance_factor = max(1.0, _math.log10(max(10, event.expected_attendance)) / 3.0)

    n_violations = max(5, int(base_count * severity_scale * attendance_factor))
    spread = {"sports": 0.006, "concert": 0.005, "festival": 0.008, "construction": 0.003, "accident": 0.002}.get(event.event_type.lower(), 0.004)

    base_id = str(uuid.uuid4())[:8]
    violations = [
        PoliceViolation(
            id=f"SIM-EVT-{base_id}-{i}",
            latitude=event.latitude + (np.random.random() - 0.5) * spread,
            longitude=event.longitude + (np.random.random() - 0.5) * spread,
            violation_type="WRONG PARKING",
            vehicle_type="CAR",
            created_datetime=datetime.now(timezone.utc),
            geom=WKTElement(f"POINT({event.longitude} {event.latitude})", srid=4326),
        )
        for i in range(n_violations)
    ]
    db.add_all(violations)
    db.commit()
    _PIPELINE_CACHE.clear()

    # obs_hours_override: treat injected violations as if they span 20 days rather than
    # the full dataset window (~150 days). This makes acute event clusters score correctly
    # (Critical/High) instead of being diluted to Low by the long baseline.
    response = run_congestion_pipeline(
        db,
        min_cluster_size=5, min_samples=3,
        patrol_vehicles=patrol_vehicles, max_stops=max_stops,
        candidate_limit=min(candidate_limit, 8), distance_penalty=distance_penalty,
        map_cluster_limit=map_cluster_limit, route_geometry=route_geometry,
        solver_time_limit=solver_time_limit, routing_engine=routing_engine,
        use_cache=False, obs_hours_override=480.0,
    )
    response["status"] = "activated"
    response["injected_violations"] = n_violations
    response["event_id"] = event_id
    return response


def _event_dict(e: Event):
    return {
        "id": e.id, "title": e.title, "event_type": e.event_type,
        "description": e.description, "latitude": e.latitude, "longitude": e.longitude,
        "start_time": e.start_time.isoformat() if e.start_time else None,
        "end_time": e.end_time.isoformat() if e.end_time else None,
        "expected_attendance": e.expected_attendance, "severity": e.severity,
        "is_planned": e.is_planned, "road_closure_required": e.road_closure_required,
        "source": "planned" if e.is_planned else "unplanned_incident",
    }

def _prediction_dict(p):
    if not p:
        return None
    return {
        "id": p.id, "event_id": p.event_id,
        "prediction_time": p.prediction_time.isoformat() if p.prediction_time else None,
        "impact_score": p.impact_score, "confidence_score": p.confidence_score,
        "affected_zones": p.affected_zones, "recommendations": p.recommendations,
    }

def _feedback_dict(f):
    if not f:
        return None
    return {
        "id": f.id, "event_id": f.event_id,
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "actual_impact_score": f.actual_impact_score, "actual_severity": f.actual_severity,
        "observation_notes": f.observation_notes, "prediction_error": f.prediction_error,
        "effectiveness_score": f.effectiveness_score,
    }


@app.get("/api/v1/events")
def list_events(db: Session = Depends(get_db)):
    events = db.query(Event).order_by(Event.start_time.desc()).all()
    return [_event_dict(e) for e in events]

@app.post("/api/v1/events")
def create_event(req: EventCreate, db: Session = Depends(get_db)):
    event = create_event_with_prediction(db, req.dict())
    return _event_dict(event)

@app.get("/api/v1/events/{event_id}")
def get_event(event_id: str, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    prediction = db.query(ImpactPrediction).filter(ImpactPrediction.event_id == event_id).order_by(ImpactPrediction.prediction_time.desc()).first()
    feedback = db.query(EventFeedback).filter(EventFeedback.event_id == event_id).first()
    p = _prediction_dict(prediction)
    if p and prediction and prediction.recommendations:
        p["score_breakdown"] = prediction.recommendations.get("score_breakdown")
        p["why_risky"] = prediction.recommendations.get("why_risky")
    return {"event": _event_dict(event), "prediction": p, "feedback": _feedback_dict(feedback)}

@app.post("/api/v1/events/{event_id}/feedback")
def submit_feedback(event_id: str, req: EventFeedbackSubmit, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    prediction = db.query(ImpactPrediction).filter(ImpactPrediction.event_id == event_id).order_by(ImpactPrediction.prediction_time.desc()).first()
    prediction_error = abs(req.actual_impact_score - prediction.impact_score) if prediction else None
    # Effectiveness: percentage of predicted score that was correct.
    # 100% = exact prediction, 0% = prediction was off by the full predicted value.
    if prediction_error is not None and prediction and prediction.impact_score > 0:
        effectiveness = max(0.0, round(100.0 * (1.0 - prediction_error / max(prediction.impact_score, req.actual_impact_score)), 1))
    else:
        effectiveness = None
    feedback = EventFeedback(
        id=str(uuid.uuid4()), event_id=event_id,
        actual_impact_score=req.actual_impact_score, actual_severity=req.actual_severity,
        observation_notes=req.observation_notes, prediction_error=prediction_error,
        effectiveness_score=effectiveness,
    )
    db.add(feedback)
    db.commit()
    return _feedback_dict(feedback)

@app.post("/api/v1/seed-events")
def seed_events_endpoint(db: Session = Depends(get_db)):
    from sqlalchemy import text
    db.execute(text("TRUNCATE TABLE events CASCADE"))
    run_seed_events(db)
    return {"status": "success", "message": "Events seeded successfully"}


@app.get("/api/v1/events/{event_id}/forecast")
def event_forecast(event_id: str, db: Session = Depends(get_db)):
    from services.forecast import generate_event_forecast
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    prediction = (
        db.query(ImpactPrediction)
        .filter(ImpactPrediction.event_id == event_id)
        .order_by(ImpactPrediction.prediction_time.desc())
        .first()
    )
    if not prediction:
        raise HTTPException(status_code=404, detail="No prediction found for this event")
    return generate_event_forecast(
        event,
        prediction.impact_score,
        prediction.recommendations.get("manpower_count", 5) if prediction.recommendations else 5,
        prediction.recommendations.get("barricade_count", 10) if prediction.recommendations else 10,
    )


@app.get("/api/v1/dashboard-summary")
def dashboard_summary(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    from sqlalchemy import or_ as sql_or

    all_events = db.query(Event).order_by(Event.start_time).all()
    all_dicts = [_event_dict(e) for e in all_events]

    def _parse(ts):
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except Exception:
            return None

    active = [e for e in all_dicts if (st := _parse(e["start_time"])) and st <= now and
              (_parse(e["end_time"]) is None or _parse(e["end_time"]) >= now)]
    upcoming = [e for e in all_dicts if (st := _parse(e["start_time"])) and
                now < st <= now + timedelta(hours=24)]
    imminent = [e for e in all_dicts if (st := _parse(e["start_time"])) and
                now < st <= now + timedelta(hours=2)]

    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    highest = max(
        [e["severity"] for e in active + imminent if e.get("severity")],
        key=lambda s: severity_rank.get(s, 0),
        default=None,
    )

    feedbacks = db.query(EventFeedback).all()
    errors = [f.prediction_error for f in feedbacks if f.prediction_error is not None]
    scores = [f.effectiveness_score for f in feedbacks if f.effectiveness_score is not None]

    learning_stats = {
        "total_outcomes_recorded": len(feedbacks),
        "mean_prediction_error": round(sum(errors) / len(errors), 1) if errors else None,
        "mean_accuracy_pct": round(sum(scores) / len(scores), 1) if scores else None,
        "recent_feedback": [_feedback_dict(f) for f in sorted(feedbacks, key=lambda x: x.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:3]],
    }

    return {
        "now": now.isoformat(),
        "active_events": active,
        "upcoming_events_24h": upcoming,
        "imminent_events_2h": imminent,
        "total_events": len(all_dicts),
        "highest_severity_active": highest,
        "alert_count": len(active) + len(imminent),
        "learning_stats": learning_stats,
        "system_status": "operational",
    }


@app.get("/api/v1/events/{event_id}/learning")
def event_learning(event_id: str, db: Session = Depends(get_db)):
    """Returns prediction vs actual comparison and peer-event accuracy for post-event learning."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    prediction = (
        db.query(ImpactPrediction)
        .filter(ImpactPrediction.event_id == event_id)
        .order_by(ImpactPrediction.prediction_time.desc())
        .first()
    )
    feedback = db.query(EventFeedback).filter(EventFeedback.event_id == event_id).first()

    # Peer events: same event type with feedback
    peer_feedbacks = (
        db.query(EventFeedback)
        .join(Event, EventFeedback.event_id == Event.id)
        .filter(Event.event_type == event.event_type, EventFeedback.prediction_error.isnot(None))
        .all()
    )
    peer_errors = [f.prediction_error for f in peer_feedbacks if f.prediction_error is not None]

    return {
        "event_id": event_id,
        "event_type": event.event_type,
        "predicted_score": prediction.impact_score if prediction else None,
        "actual_score": feedback.actual_impact_score if feedback else None,
        "prediction_error": feedback.prediction_error if feedback else None,
        "effectiveness_score": feedback.effectiveness_score if feedback else None,
        "observation_notes": feedback.observation_notes if feedback else None,
        "has_outcome": feedback is not None,
        "peer_accuracy": {
            "sample_size": len(peer_errors),
            "mean_error": round(sum(peer_errors) / len(peer_errors), 1) if peer_errors else None,
            "event_type": event.event_type,
        },
        "insight": _learning_insight(feedback, peer_errors, prediction),
    }


def _learning_insight(feedback, peer_errors, prediction) -> str:
    if not feedback:
        return "Outcome not yet recorded. Submit post-event feedback to improve future predictions."
    err = feedback.prediction_error or 0
    if err < 5:
        return "Excellent prediction accuracy. Model calibration confirmed for this event type."
    if err < 15:
        peer_avg = round(sum(peer_errors) / len(peer_errors), 1) if peer_errors else None
        if peer_avg and err < peer_avg:
            return f"Good accuracy (error {err:.1f}). Better than {peer_avg:.1f} average for similar events."
        return f"Good accuracy (error {err:.1f}). Within acceptable operational margin."
    if feedback.actual_impact_score > (prediction.impact_score if prediction else 0):
        return f"Impact was underestimated by {err:.1f} points. Consider increasing weight for {feedback.actual_severity} severity events."
    return f"Impact was overestimated by {err:.1f} points. Attendance or crowd density may have been lower than expected."


@app.get("/api/v1/live-incidents")
def live_incidents(
    window_days: int = Query(default=3, ge=1, le=14),
    baseline_days: int = Query(default=21, ge=7, le=60),
    min_uplift: float = Query(default=1.4, ge=1.1, le=5.0),
    as_of: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """Spatial-temporal anomaly detection: compares recent violation density vs rolling baseline.

    Buckets violations into 0.01° grid cells (~1 km), computes daily rates for
    recent window vs baseline window, and returns cells where the rate ratio
    exceeds min_uplift. Pass as_of=YYYY-MM-DD to simulate detection on a specific
    date (e.g. as_of=2024-03-28 to detect the RCB vs CSK match-day spike).
    """
    from sqlalchemy import text as _text
    from datetime import timedelta

    # Exclude synthetic records created by simulate-anomaly (which use datetime.now()).
    # Historical CSV data is Jan–May 2024; anything after 2025-01-01 is synthetic.
    max_ts_row = db.execute(_text(
        "SELECT MAX(created_datetime) FROM police_violations WHERE created_datetime < '2025-01-01'"
    )).scalar()
    if not max_ts_row:
        return {"hotspots": [], "as_of": None, "total_detected": 0, "method": "no_data"}

    max_ts = max_ts_row if hasattr(max_ts_row, "tzinfo") else datetime.fromisoformat(str(max_ts_row))
    if max_ts.tzinfo is None:
        max_ts = max_ts.replace(tzinfo=timezone.utc)

    if as_of:
        try:
            ref_dt = datetime.fromisoformat(as_of)
            ref_dt = ref_dt.replace(tzinfo=timezone.utc) if ref_dt.tzinfo is None else ref_dt
        except Exception:
            ref_dt = max_ts
    else:
        ref_dt = max_ts

    recent_start = ref_dt - timedelta(days=window_days)
    baseline_start = ref_dt - timedelta(days=window_days + baseline_days)
    baseline_end = recent_start

    bucket_sql = _text("""
        SELECT
            ROUND(CAST(ST_Y(geom) AS NUMERIC), 2) AS lat_b,
            ROUND(CAST(ST_X(geom) AS NUMERIC), 2) AS lon_b,
            COUNT(*) AS cnt
        FROM police_violations
        WHERE created_datetime >= :t_start AND created_datetime < :t_end
        GROUP BY lat_b, lon_b
        HAVING COUNT(*) >= 3
    """)

    recent_rows = db.execute(bucket_sql, {"t_start": recent_start, "t_end": ref_dt}).fetchall()
    baseline_rows = db.execute(bucket_sql, {"t_start": baseline_start, "t_end": baseline_end}).fetchall()

    baseline_map = {(float(r.lat_b), float(r.lon_b)): int(r.cnt) for r in baseline_rows}

    hotspots = []
    for row in recent_rows:
        lat, lon = float(row.lat_b), float(row.lon_b)
        recent_cnt = int(row.cnt)
        baseline_cnt = baseline_map.get((lat, lon), 0)

        recent_rate = recent_cnt / window_days
        if baseline_cnt > 0:
            uplift = recent_rate / (baseline_cnt / baseline_days)
        elif recent_cnt >= 5:
            uplift = min_uplift  # new hotspot with no baseline — flag at threshold
        else:
            continue

        if uplift < min_uplift:
            continue

        hotspots.append({
            "lat": lat,
            "lon": lon,
            "recent_count": recent_cnt,
            "baseline_count": baseline_cnt,
            "uplift_ratio": round(uplift, 2),
            "severity": "critical" if uplift >= 3.0 else "high" if uplift >= 2.0 else "medium",
        })

    hotspots.sort(key=lambda x: x["uplift_ratio"], reverse=True)

    return {
        "hotspots": hotspots[:20],
        "as_of": ref_dt.isoformat(),
        "window_days": window_days,
        "baseline_days": baseline_days,
        "min_uplift": min_uplift,
        "total_detected": len(hotspots),
    }


# ---------------------------------------------------------------------------
# Static frontend — must be last (wildcard mount would shadow API routes)
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
