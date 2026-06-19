"""
Unit tests for the effectiveness score formula used in main.py's submit_feedback.

The formula is extracted verbatim from main.py rather than importing the FastAPI
app (which would pull in DB connections, GeoAlchemy2, etc.).

Source (main.py, submit_feedback):
    prediction_error = abs(actual - predicted)
    if prediction_error is not None and predicted > 0:
        effectiveness = max(0.0, round(100.0 * (1.0 - prediction_error / max(predicted, actual)), 1))
    else:
        effectiveness = None

The helper below replicates that logic so the tests remain self-contained.
"""

import pytest


def compute_effectiveness(predicted: float, actual: float):
    """
    Mirrors the inline effectiveness formula from main.py submit_feedback.

    Returns None when the predicted score is 0 (no baseline to compare against).
    Returns a float in [0.0, 100.0] otherwise.
    """
    prediction_error = abs(actual - predicted)
    denom = max(predicted, actual)
    if predicted <= 0:
        return None
    return max(0.0, round(100.0 * (1.0 - prediction_error / denom), 1))


# ---------------------------------------------------------------------------
# 1. Perfect prediction → 100.0
# ---------------------------------------------------------------------------

def test_perfect_prediction():
    assert compute_effectiveness(50.0, 50.0) == 100.0


# ---------------------------------------------------------------------------
# 2. Complete miss (actual=0 vs predicted=100) → 0.0
# ---------------------------------------------------------------------------

def test_complete_miss():
    result = compute_effectiveness(100.0, 0.0)
    assert result == 0.0, f"Expected 0.0 for complete miss, got {result}"


# ---------------------------------------------------------------------------
# 3. Underestimate (predicted=40, actual=80) → 50.0
# ---------------------------------------------------------------------------

def test_underestimate():
    # error = 40, denom = max(40, 80) = 80  →  1 - 40/80 = 0.50  →  50.0
    result = compute_effectiveness(40.0, 80.0)
    assert result == 50.0, f"Expected 50.0, got {result}"


# ---------------------------------------------------------------------------
# 4. Overestimate (predicted=80, actual=40) — symmetric result → 50.0
# ---------------------------------------------------------------------------

def test_overestimate():
    # error = 40, denom = max(80, 40) = 80  →  1 - 40/80 = 0.50  →  50.0
    result = compute_effectiveness(80.0, 40.0)
    assert result == 50.0, f"Expected 50.0, got {result}"


# ---------------------------------------------------------------------------
# 5. Predicted=0 → None (guard: no baseline, cannot compute effectiveness)
# ---------------------------------------------------------------------------

def test_zero_denominator():
    # Both zero: predicted=0 triggers the guard
    assert compute_effectiveness(0.0, 0.0) is None
    # Predicted=0 with non-zero actual also returns None
    assert compute_effectiveness(0.0, 50.0) is None


# ---------------------------------------------------------------------------
# 6. Result is never negative
# ---------------------------------------------------------------------------

def test_never_negative():
    # Large actual vs small predicted — raw formula would give negative without max()
    result = compute_effectiveness(10.0, 100.0)
    assert result is not None
    assert result >= 0.0, f"Effectiveness must be >= 0.0, got {result}"


# ---------------------------------------------------------------------------
# 7. Close prediction → high accuracy (>= 90.0)
# ---------------------------------------------------------------------------

def test_close_prediction():
    # predicted=62, actual=65: error=3, denom=65 → 1 - 3/65 ≈ 0.9538 → 95.4
    result = compute_effectiveness(62.0, 65.0)
    assert result is not None
    assert result >= 90.0, (
        f"A small prediction error (3/65) should yield >= 90.0 accuracy, got {result}"
    )
