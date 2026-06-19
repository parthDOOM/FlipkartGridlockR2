"""
Unit tests for calculate_event_impact (services.events).

No database required — a lightweight MockEvent dataclass stands in for the ORM model.
The function only reads attribute values and never calls DB methods, so this is safe.
"""

import math
import pytest
from dataclasses import dataclass
from typing import Optional

from services.events import calculate_event_impact


@dataclass
class MockEvent:
    event_type: str
    severity: str
    expected_attendance: int
    is_planned: bool
    road_closure_required: bool
    latitude: float = 12.9716
    longitude: float = 77.5946


# ---------------------------------------------------------------------------
# 1. Critical sports event — should hit the 100.0 cap
# ---------------------------------------------------------------------------

def test_critical_sports_event():
    event = MockEvent(
        event_type="sports",
        severity="critical",
        expected_attendance=35000,
        is_planned=True,
        road_closure_required=True,
    )
    result = calculate_event_impact(event)

    assert result["impact_score"] == 100.0, (
        f"Expected capped score of 100.0, got {result['impact_score']}"
    )
    assert result["confidence_score"] >= 0.90, (
        f"Expected confidence >= 0.90, got {result['confidence_score']}"
    )

    why_risky = result["recommendations"]["why_risky"]
    assert len(why_risky) >= 2, (
        f"Expected at least 2 risk reasons, got {len(why_risky)}: {why_risky}"
    )

    breakdown = result["recommendations"]["score_breakdown"]
    required_keys = {
        "event_type_base",
        "severity_contribution",
        "attendance_contribution",
        "road_closure_bonus",
    }
    assert required_keys.issubset(breakdown.keys()), (
        f"Missing breakdown keys: {required_keys - breakdown.keys()}"
    )


# ---------------------------------------------------------------------------
# 2. Low-impact construction event
# ---------------------------------------------------------------------------

def test_low_impact_event():
    event = MockEvent(
        event_type="construction",
        severity="low",
        expected_attendance=0,
        is_planned=True,
        road_closure_required=False,
    )
    result = calculate_event_impact(event)

    assert result["impact_score"] < 50, (
        f"Expected impact_score < 50 for a minimal event, got {result['impact_score']}"
    )


# ---------------------------------------------------------------------------
# 3. Unplanned events should have lower confidence than planned equivalents
# ---------------------------------------------------------------------------

def test_unplanned_lower_confidence():
    planned = MockEvent(
        event_type="accident",
        severity="high",
        expected_attendance=500,
        is_planned=True,
        road_closure_required=False,
    )
    unplanned = MockEvent(
        event_type="accident",
        severity="high",
        expected_attendance=500,
        is_planned=False,
        road_closure_required=False,
    )

    planned_result = calculate_event_impact(planned)
    unplanned_result = calculate_event_impact(unplanned)

    # Planned baseline is 0.85; unplanned is 0.60 — unplanned must be strictly lower
    assert unplanned_result["confidence_score"] < planned_result["confidence_score"], (
        f"Unplanned confidence ({unplanned_result['confidence_score']}) should be less than "
        f"planned confidence ({planned_result['confidence_score']})"
    )
    assert unplanned_result["confidence_score"] < 0.85, (
        f"Unplanned confidence should be below 0.85, got {unplanned_result['confidence_score']}"
    )


# ---------------------------------------------------------------------------
# 4. Road closure adds exactly 20 points
# ---------------------------------------------------------------------------

def test_road_closure_adds_20():
    base = dict(
        event_type="festival",
        severity="medium",
        expected_attendance=5000,
        is_planned=True,
    )
    with_closure = MockEvent(**base, road_closure_required=True)
    without_closure = MockEvent(**base, road_closure_required=False)

    score_with = calculate_event_impact(with_closure)["impact_score"]
    score_without = calculate_event_impact(without_closure)["impact_score"]

    # The closure bonus is 20.0; both raw scores must be below 100 for the
    # difference to be exactly 20 (i.e. the cap doesn't absorb the bonus).
    # If the capped score for "with_closure" equals 100, we only check >= 20.
    if score_with < 100.0:
        assert abs((score_with - score_without) - 20.0) < 1e-6, (
            f"Expected 20.0 point difference, got {score_with - score_without}"
        )
    else:
        # Cap was hit — difference must be at least 0 (closure never hurts)
        assert score_with >= score_without


# ---------------------------------------------------------------------------
# 5. score_breakdown components sum to final_score
# ---------------------------------------------------------------------------

def test_score_breakdown_sums_to_final():
    event = MockEvent(
        event_type="concert",
        severity="high",
        expected_attendance=8000,
        is_planned=True,
        road_closure_required=True,
    )
    result = calculate_event_impact(event)
    breakdown = result["recommendations"]["score_breakdown"]

    component_sum = (
        breakdown["event_type_base"]
        + breakdown["severity_contribution"]
        + breakdown["attendance_contribution"]
        + breakdown["road_closure_bonus"]
    )
    # The final score is min(100, component_sum); both are stored in breakdown
    expected_final = min(100.0, component_sum)
    assert abs(breakdown["final_score"] - expected_final) < 1e-4, (
        f"Breakdown components {component_sum} do not match final_score {breakdown['final_score']}"
    )


# ---------------------------------------------------------------------------
# 6. why_risky is always non-empty
# ---------------------------------------------------------------------------

def test_why_risky_not_empty():
    """Even a minimal standard event should produce at least one risk reason."""
    event = MockEvent(
        event_type="standard",
        severity="low",
        expected_attendance=0,
        is_planned=True,
        road_closure_required=False,
    )
    result = calculate_event_impact(event)
    why_risky = result["recommendations"]["why_risky"]

    assert isinstance(why_risky, list) and len(why_risky) >= 1, (
        f"why_risky should be a non-empty list, got: {why_risky}"
    )


# ---------------------------------------------------------------------------
# 7. Zero attendance means zero attendance contribution
# ---------------------------------------------------------------------------

def test_attendance_zero_no_contribution():
    event = MockEvent(
        event_type="rally",
        severity="medium",
        expected_attendance=0,
        is_planned=True,
        road_closure_required=False,
    )
    result = calculate_event_impact(event)
    attendance_contribution = result["recommendations"]["score_breakdown"]["attendance_contribution"]

    assert attendance_contribution == 0, (
        f"Expected attendance_contribution == 0 when attendance is 0, "
        f"got {attendance_contribution}"
    )
