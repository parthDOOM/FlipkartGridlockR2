import math
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from geoalchemy2.elements import WKTElement
from models import Event, ImpactPrediction

EVENT_TYPE_WEIGHTS = {
    "political": 1.5,
    "sports": 1.3,
    "festival": 1.4,
    "construction": 1.1,
    "accident": 1.6,
    "rally": 1.5,
    "protest": 1.7,
    "concert": 1.4,
    "standard": 1.0
}

SEVERITY_MAPPING = {
    "low": 0.5,
    "medium": 1.0,
    "high": 1.5,
    "critical": 2.0
}

def calculate_event_impact(event: Event) -> Dict[str, Any]:
    """
    Heuristic impact model. All factors are documented and transparent.
    Outputs score_breakdown and why_risky for UI explainability.
    """
    base_weight = EVENT_TYPE_WEIGHTS.get(event.event_type.lower(), 1.0)
    severity_mult = SEVERITY_MAPPING.get(event.severity.lower(), 1.0)

    attendance_factor = 1.0
    if event.expected_attendance > 0:
        attendance_factor = math.log10(max(10, event.expected_attendance))

    # Base components
    type_contribution    = round(15.0 * base_weight, 1)
    severity_contribution = round(type_contribution * (severity_mult - 1.0), 1)
    attendance_contribution = round(15.0 * base_weight * severity_mult * max(0, attendance_factor - 1.0), 1)
    closure_contribution  = 20.0 if event.road_closure_required else 0.0

    impact_score = min(100.0, type_contribution + severity_contribution + attendance_contribution + closure_contribution)

    impact_radius_km = 0.5 * base_weight * severity_mult * (attendance_factor / 2.0)

    manpower = max(2, int(impact_score / 10 * base_weight))
    barricades = max(0, int(impact_score / 5 * (2.0 if event.road_closure_required else 1.0)))

    # Build plain-language explanation
    why_risky = []
    if event.event_type.lower() in ("protest", "political", "rally", "accident"):
        why_risky.append(f"{event.event_type.capitalize()} events historically cause severe congestion")
    elif event.event_type.lower() in ("sports", "festival", "concert"):
        why_risky.append(f"Mass-attendance {event.event_type} events generate peak parking pressure")
    if event.severity in ("high", "critical"):
        why_risky.append(f"{event.severity.capitalize()} severity — expect significant road disruption")
    if event.expected_attendance >= 10000:
        why_risky.append(f"Large crowd ({event.expected_attendance:,} expected) will saturate nearby parking")
    elif event.expected_attendance >= 1000:
        why_risky.append(f"Moderate attendance ({event.expected_attendance:,}) will increase local congestion")
    if event.road_closure_required:
        why_risky.append("Road closure required — traffic must be actively diverted")
    if not event.is_planned:
        why_risky.append("Unplanned incident — no advance deployment possible, response is reactive")
    if not why_risky:
        why_risky.append("Standard event with moderate expected congestion impact")

    score_breakdown = {
        "event_type_base":          type_contribution,
        "severity_contribution":    severity_contribution,
        "attendance_contribution":  attendance_contribution,
        "road_closure_bonus":       closure_contribution,
        "final_score":              round(impact_score, 1),
    }

    # Confidence: higher for planned events with good data
    confidence = 0.85 if event.is_planned else 0.60
    if event.expected_attendance > 0:
        confidence = min(0.95, confidence + 0.05)
    if event.road_closure_required:
        confidence = min(0.95, confidence + 0.03)
    confidence = round(confidence, 2)

    recommendations = {
        "manpower_count": manpower,
        "barricade_count": barricades,
        "priority_level": "High" if impact_score > 70 else "Medium" if impact_score > 40 else "Low",
        "suggested_diversions": _generate_diversions(event, impact_radius_km) if (event.road_closure_required or event.severity in ("high", "critical")) else [],
        "monitoring_frequency_mins": 15 if impact_score > 70 else 30 if impact_score > 40 else 60,
        "score_breakdown": score_breakdown,
        "why_risky": why_risky,
    }

    affected_zones = [
        {
            "latitude": event.latitude,
            "longitude": event.longitude,
            "radius_km": round(impact_radius_km, 2),
            "severity_score": round(impact_score, 2),
        }
    ]

    return {
        "impact_score": round(impact_score, 2),
        "affected_zones": affected_zones,
        "recommendations": recommendations,
        "confidence_score": confidence,
    }

# (lat, lon, [primary_road, alternate1, alternate2])
_BANGALORE_ROAD_GRID = [
    (12.9784, 77.5994, ["Cubbon Road", "Queen's Road", "Kasturba Road"]),
    (12.9716, 77.5946, ["MG Road", "Brigade Road", "Residency Road"]),
    (12.9750, 77.6070, ["Richmond Road", "Langford Road", "Primrose Road"]),
    (12.9719, 77.6412, ["CMH Road", "100 Feet Road", "Old Madras Road"]),
    (12.9507, 77.5848, ["Hosur Road", "Bull Temple Road", "Kanakapura Road"]),
    (12.9176, 77.6233, ["NICE Road", "Hosur Road", "Bannerghatta Road"]),
    (12.9352, 77.6245, ["Koramangala 80ft Road", "Sarjapur Road", "Intermediate Ring Road"]),
    (12.9898, 77.5502, ["Tumkur Road", "Chord Road", "Rajajinagar Main Road"]),
    (13.0297, 77.5857, ["Bellary Road", "Hebbal Flyover", "Airport Road"]),
    (12.9254, 77.5468, ["JP Nagar 5th Phase Road", "Banashankari Main Road", "Kanakapura Road"]),
]

def _nearest_roads(lat: float, lon: float):
    best = min(_BANGALORE_ROAD_GRID, key=lambda r: (r[0]-lat)**2 + (r[1]-lon)**2)
    return best[2]

def _generate_diversions(event: Event, radius_km: float) -> List[str]:
    roads = _nearest_roads(event.latitude, event.longitude)
    return [
        f"Divert inbound traffic via {roads[1]} (parallel corridor, ~{round(radius_km*1.2, 1)} km detour)",
        f"Restrict turns at {roads[0]} junction — use {roads[2]} for cross-city movement",
        f"Flag {roads[0]} entry points; deploy dynamic message signs for pre-event rerouting",
    ]

def create_event_with_prediction(db: Session, event_data: Dict[str, Any]) -> Event:
    event_id = str(uuid.uuid4())
    new_event = Event(
        id=event_id,
        title=event_data["title"],
        event_type=event_data["event_type"],
        description=event_data.get("description"),
        latitude=event_data["latitude"],
        longitude=event_data["longitude"],
        start_time=event_data["start_time"],
        end_time=event_data.get("end_time"),
        expected_attendance=event_data.get("expected_attendance", 0),
        severity=event_data.get("severity", "medium"),
        is_planned=event_data.get("is_planned", True),
        road_closure_required=event_data.get("road_closure_required", False),
        geom=WKTElement(f"POINT({event_data['longitude']} {event_data['latitude']})", srid=4326)
    )
    
    db.add(new_event)
    db.flush()
    
    prediction_data = calculate_event_impact(new_event)
    prediction = ImpactPrediction(
        id=str(uuid.uuid4()),
        event_id=event_id,
        prediction_time=datetime.now(timezone.utc),
        impact_score=prediction_data["impact_score"],
        confidence_score=prediction_data["confidence_score"],
        affected_zones=prediction_data["affected_zones"],
        recommendations=prediction_data["recommendations"]
    )
    
    db.add(prediction)
    db.commit()
    db.refresh(new_event)
    return new_event
