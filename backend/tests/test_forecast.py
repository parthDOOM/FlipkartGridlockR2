"""
Unit tests for generate_event_forecast (services.forecast).

No database required — MockEvent supplies all fields the function reads:
  start_time, end_time, id, event_type, severity, expected_attendance,
  is_planned, road_closure_required, latitude, longitude.
"""

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from services.forecast import generate_event_forecast


@dataclass
class MockEvent:
    event_type: str
    severity: str
    expected_attendance: int
    is_planned: bool
    road_closure_required: bool
    start_time: datetime = None
    end_time: datetime = None
    id: str = "mock-event-id-001"
    latitude: float = 12.9716
    longitude: float = 77.5946


def _scheduled_event(**kwargs) -> MockEvent:
    """Returns a planned event starting 48 h from now (status: 'scheduled')."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        event_type="sports",
        severity="high",
        expected_attendance=10000,
        is_planned=True,
        road_closure_required=False,
        start_time=now + timedelta(hours=48),
        end_time=now + timedelta(hours=51),
    )
    defaults.update(kwargs)
    return MockEvent(**defaults)


# ---------------------------------------------------------------------------
# 1. Forecast always returns exactly 8 horizon points
# ---------------------------------------------------------------------------

def test_forecast_returns_8_points():
    event = _scheduled_event()
    result = generate_event_forecast(event, base_impact_score=70.0, base_manpower=8, base_barricades=15)

    assert len(result["forecast_points"]) == 8, (
        f"Expected 8 forecast points, got {len(result['forecast_points'])}"
    )


# ---------------------------------------------------------------------------
# 2. Peak is at Event Start (hours_from_start == 0.0)
# ---------------------------------------------------------------------------

def test_forecast_peak_at_event_start():
    """For a short event the phase curve peaks at 0.0 (event start)."""
    event = _scheduled_event(
        start_time=datetime.now(timezone.utc) + timedelta(hours=48),
        end_time=datetime.now(timezone.utc) + timedelta(hours=49),  # 1-hour event
    )
    result = generate_event_forecast(event, base_impact_score=80.0, base_manpower=10, base_barricades=20)

    points = result["forecast_points"]
    peak = max(points, key=lambda p: p["risk_score"])
    assert peak["label"] == "Event Start", (
        f"Expected peak at 'Event Start', got '{peak['label']}' "
        f"(risk_score={peak['risk_score']})"
    )


# ---------------------------------------------------------------------------
# 3. All risk_scores are bounded in [0, 110]
# ---------------------------------------------------------------------------

def test_forecast_scores_bounded():
    """Scores should stay within a sane range (slight overshoot from dispersal curve is allowed)."""
    event = _scheduled_event()
    result = generate_event_forecast(event, base_impact_score=100.0, base_manpower=15, base_barricades=30)

    for pt in result["forecast_points"]:
        assert 0.0 <= pt["risk_score"] <= 110.0, (
            f"risk_score {pt['risk_score']} out of [0, 110] at horizon '{pt['label']}'"
        )


# ---------------------------------------------------------------------------
# 4. Every forecast point has all required keys
# ---------------------------------------------------------------------------

def test_forecast_has_required_keys():
    required = {
        "label",
        "hours_from_start",
        "risk_score",
        "congestion_level",
        "recommended_action",
        "manpower_required",
        "barricades_count",
    }
    event = _scheduled_event()
    result = generate_event_forecast(event, base_impact_score=60.0, base_manpower=6, base_barricades=12)

    for pt in result["forecast_points"]:
        missing = required - pt.keys()
        assert not missing, f"Forecast point '{pt.get('label')}' missing keys: {missing}"


# ---------------------------------------------------------------------------
# 5. Top-level result contains a "summary" with required sub-keys
# ---------------------------------------------------------------------------

def test_summary_present():
    event = _scheduled_event()
    result = generate_event_forecast(event, base_impact_score=75.0, base_manpower=9, base_barricades=18)

    assert "summary" in result, "Result must contain a 'summary' key"
    summary = result["summary"]
    for key in ("peak_manpower", "peak_barricades", "peak_risk_score"):
        assert key in summary, f"summary missing required key '{key}'"


# ---------------------------------------------------------------------------
# 6. Zero base score → all risk_scores are 0.0
# ---------------------------------------------------------------------------

def test_zero_base_score_gives_zero_risk():
    event = _scheduled_event()
    result = generate_event_forecast(event, base_impact_score=0.0, base_manpower=5, base_barricades=10)

    for pt in result["forecast_points"]:
        assert pt["risk_score"] == 0.0, (
            f"Expected 0.0 at '{pt['label']}' when base_impact_score=0, "
            f"got {pt['risk_score']}"
        )


# ---------------------------------------------------------------------------
# 7. "status" field is one of the known lifecycle values
# ---------------------------------------------------------------------------

def test_status_field_present():
    valid_statuses = {"scheduled", "upcoming", "imminent", "active", "concluded"}

    # scheduled: > 24 h away
    scheduled_event = _scheduled_event(
        start_time=datetime.now(timezone.utc) + timedelta(hours=48),
        end_time=datetime.now(timezone.utc) + timedelta(hours=50),
    )
    result = generate_event_forecast(scheduled_event, base_impact_score=50.0, base_manpower=5, base_barricades=10)
    assert result["status"] in valid_statuses, (
        f"Unexpected status '{result['status']}', expected one of {valid_statuses}"
    )
    assert result["status"] == "scheduled", (
        f"Event starting in 48 h should be 'scheduled', got '{result['status']}'"
    )
