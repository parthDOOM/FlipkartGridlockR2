import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from database import engine, Base
from models import Event, ImpactPrediction, EventFeedback
from services.events import calculate_event_impact
from geoalchemy2.elements import WKTElement


_EVENTS_DATA = [
    # ── ACTIVE / IMMINENT ──────────────────────────────────────────────────
    {
        "title": "IPL Match: RCB vs MI",
        "event_type": "sports",
        "description": "High-profile T20 at Chinnaswamy Stadium. 35,000 fans expected. "
                       "All access roads to be congested from 5 PM onwards.",
        "latitude": 12.9784, "longitude": 77.5994,
        "start_offset_hours": 4, "end_offset_hours": 9,
        "expected_attendance": 35000, "severity": "critical",
        "is_planned": True, "road_closure_required": True,
    },
    {
        "title": "Political Rally — MG Road",
        "event_type": "political",
        "description": "Public procession and address at MG Road. Multiple road closures. "
                       "Estimated 10,000 attendees.",
        "latitude": 12.9750, "longitude": 77.6070,
        "start_offset_hours": 20, "end_offset_hours": 24,
        "expected_attendance": 10000, "severity": "high",
        "is_planned": True, "road_closure_required": True,
    },
    {
        "title": "Water Main Burst — Indiranagar",
        "event_type": "accident",
        "description": "Sudden pipeline burst causing road flooding at 100 Feet Road. "
                       "One lane blocked, traffic diverted.",
        "latitude": 12.9719, "longitude": 77.6412,
        "start_offset_hours": -1, "end_offset_hours": 4,
        "expected_attendance": 0, "severity": "high",
        "is_planned": False, "road_closure_required": False,
    },
    # ── UPCOMING (24h–72h) ─────────────────────────────────────────────────
    {
        "title": "Flower Show — Lalbagh",
        "event_type": "festival",
        "description": "Annual horticultural exhibition at Lalbagh Botanical Garden. "
                       "Expected 50,000 visitors over 10 days.",
        "latitude": 12.9507, "longitude": 77.5848,
        "start_offset_hours": 48, "end_offset_hours": 288,
        "expected_attendance": 50000, "severity": "medium",
        "is_planned": True, "road_closure_required": False,
    },
    {
        "title": "Metro Construction — Silk Board",
        "event_type": "construction",
        "description": "Phase 2 elevated corridor pile work. Lane reduction on Hosur Road. "
                       "Expected 6 more months of disruption.",
        "latitude": 12.9176, "longitude": 77.6233,
        "start_offset_hours": -720, "end_offset_hours": 4320,
        "expected_attendance": 0, "severity": "medium",
        "is_planned": True, "road_closure_required": False,
    },
    {
        "title": "Diwali Celebrations — Commercial Street",
        "event_type": "festival",
        "description": "Diwali shopping rush and evening fireworks near Commercial Street "
                       "and surrounding market areas.",
        "latitude": 12.9850, "longitude": 77.6101,
        "start_offset_hours": 36, "end_offset_hours": 60,
        "expected_attendance": 80000, "severity": "high",
        "is_planned": True, "road_closure_required": False,
    },
    # ── PAST EVENTS WITH RECORDED OUTCOMES (for learning loop demo) ────────
    {
        "title": "IT Conclave — NIMHANS Convention Centre",
        "event_type": "sports",          # reusing type for similar pattern
        "description": "Annual Bangalore IT summit. Badge-gate entry, 8,000 delegates. "
                       "Bannerghatta Road and Outer Ring Road heavily impacted.",
        "latitude": 12.9398, "longitude": 77.5963,
        "start_offset_hours": -72, "end_offset_hours": -64,
        "expected_attendance": 8000, "severity": "medium",
        "is_planned": True, "road_closure_required": False,
        "seed_feedback": {
            "actual_impact_score": 41.0,
            "actual_severity": "medium",
            "observation_notes": "Actual congestion moderate but lasted 30 min longer than predicted. "
                                 "Parking on service roads underestimated.",
        },
    },
    {
        "title": "Protest March — Town Hall",
        "event_type": "protest",
        "description": "Unannounced protest march from Town Hall to Freedom Park. "
                       "Police deployed 45 minutes after start.",
        "latitude": 12.9716, "longitude": 77.5946,
        "start_offset_hours": -120, "end_offset_hours": -116,
        "expected_attendance": 3000, "severity": "high",
        "is_planned": False, "road_closure_required": True,
        "seed_feedback": {
            "actual_impact_score": 78.0,
            "actual_severity": "critical",
            "observation_notes": "Impact severely underestimated — march extended to Brigade Road. "
                                 "Three intersections blocked for 90 minutes.",
        },
    },
    {
        "title": "Marathon — Cubbon Park",
        "event_type": "sports",
        "description": "Bengaluru Marathon start/finish at Cubbon Park. Route across MG Road, "
                       "Brigade Road, Residency Road. Road closures 5–10 AM.",
        "latitude": 12.9763, "longitude": 77.5929,
        "start_offset_hours": -168, "end_offset_hours": -163,
        "expected_attendance": 15000, "severity": "high",
        "is_planned": True, "road_closure_required": True,
        "seed_feedback": {
            "actual_impact_score": 62.0,
            "actual_severity": "high",
            "observation_notes": "Prediction accurate. Pre-deployment at T-1h worked well. "
                                 "Traffic cleared within 45 min of event end.",
        },
    },
]


def run_seed_events(session: Session):
    now = datetime.now(timezone.utc)

    for data in _EVENTS_DATA:
        event_id = str(uuid.uuid4())
        start = now + timedelta(hours=data["start_offset_hours"])
        end   = now + timedelta(hours=data["end_offset_hours"])

        new_event = Event(
            id=event_id,
            title=data["title"],
            event_type=data["event_type"],
            description=data["description"],
            latitude=data["latitude"],
            longitude=data["longitude"],
            start_time=start,
            end_time=end,
            expected_attendance=data["expected_attendance"],
            severity=data["severity"],
            is_planned=data["is_planned"],
            road_closure_required=data["road_closure_required"],
            geom=WKTElement(f"POINT({data['longitude']} {data['latitude']})", srid=4326),
        )
        session.add(new_event)
        session.flush()

        pred_data = calculate_event_impact(new_event)
        prediction = ImpactPrediction(
            id=str(uuid.uuid4()),
            event_id=event_id,
            prediction_time=now,
            impact_score=pred_data["impact_score"],
            confidence_score=pred_data["confidence_score"],
            affected_zones=pred_data["affected_zones"],
            recommendations=pred_data["recommendations"],
        )
        session.add(prediction)

        # Seed historical outcomes for past events
        fb = data.get("seed_feedback")
        if fb:
            err = abs(fb["actual_impact_score"] - pred_data["impact_score"])
            denom = max(pred_data["impact_score"], fb["actual_impact_score"])
            effectiveness = round(100.0 * (1.0 - err / denom), 1) if denom > 0 else None
            session.add(EventFeedback(
                id=str(uuid.uuid4()),
                event_id=event_id,
                actual_impact_score=fb["actual_impact_score"],
                actual_severity=fb["actual_severity"],
                observation_notes=fb["observation_notes"],
                prediction_error=round(err, 1),
                effectiveness_score=effectiveness,
            ))

    session.commit()


def seed_events():
    from sqlalchemy import text
    print("Seeding events …")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.execute(text("TRUNCATE TABLE events CASCADE"))
        run_seed_events(session)
    print("Done.")


if __name__ == "__main__":
    seed_events()
