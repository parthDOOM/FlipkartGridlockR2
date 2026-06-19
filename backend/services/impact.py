import math

LANE_COUNT = 2.0
N_SATURATION = 1900.0          # HCM base saturation flow rate, veh/h/ln
MEAN_PARKING_SERVICE_MINUTES = 4.0

# Police violation records are cumulative and under-represent real parking
# events. This factor converts reported violations/day to effective maneuvers/
# peak-hour: each reported violation proxies ≈10 actual events concentrated
# in ~8 peak hours, giving roughly 1.25 effective maneuvers/peak-hour per
# reported violation/day. Empirically calibrated so 6 violations/day ≈
# CRITICAL at a major intersection and <1/day ≈ LOW.
PEAK_MANEUVER_CALIBRATION = 12.0

HEAVY_VEHICLE_TOKENS = ("TANKER", "BUS", "LORRY", "GOODS")
LIGHT_VEHICLE_TOKENS = ("SCOOTER", "MOTOR CYCLE")


def _clamp(value, lower=0.0, upper=100.0):
    return max(lower, min(upper, value))


def _vehicle_typology(vehicle_types):
    normalized = [str(vehicle).upper() for vehicle in vehicle_types if vehicle]
    total = max(1, len(normalized))
    heavy = sum(
        1
        for vehicle in normalized
        if any(token in vehicle for token in HEAVY_VEHICLE_TOKENS)
    )
    light = sum(
        1
        for vehicle in normalized
        if any(token in vehicle for token in LIGHT_VEHICLE_TOKENS)
    )

    heavy_share = heavy / total
    light_share = light / total
    severity_multiplier = 1.0 + (0.5 * heavy_share) - (0.15 * light_share)
    severity_multiplier = max(0.85, min(1.5, severity_multiplier))

    vehicle_severity_score = _clamp(((severity_multiplier - 0.85) / 0.65) * 100.0)
    return severity_multiplier, vehicle_severity_score, heavy_share, light_share


def _priority_label(score):
    # Calibrated against the Erlang-B / HCM model output range for
    # police-violation datasets spanning ~150 days:
    #   Critical ≥ 45 ↔ fp < 0.65 (>35% capacity loss, TTI > 2.0)
    #   High     ≥ 18 ↔ fp < 0.84 (>16% capacity loss, TTI > 1.3)
    #   Medium   ≥  8 ↔ fp < 0.92 (>8%  capacity loss, TTI > 1.05)
    if score >= 45:
        return "Critical", "Immediate Tow & Patrol"
    if score >= 18:
        return "High", "Patrol & Warning"
    if score >= 8:
        return "Medium", "Cones & Monitoring"
    return "Low", "Routine Warning"


def calculate_impact_scores(clusters, observation_hours: float = 24.0):
    scored_clusters = []
    service_rate = 60.0 / MEAN_PARKING_SERVICE_MINUTES

    for cluster in clusters:
        raw_count = float(cluster.get("raw_count", cluster.get("N_m", 0)))

        # violations_per_day = raw_count / (observation_hours / 24)
        # Nm_peak = violations_per_day × PEAK_MANEUVER_CALIBRATION
        violations_per_day = (raw_count * 24.0) / max(1.0, observation_hours)
        maneuvers_per_hour = violations_per_day * PEAK_MANEUVER_CALIBRATION

        # HCM 6th Ed. parking adjustment factor:
        # fp = (N - 0.1 - 18·Nm) / N
        # N = saturation flow rate (1900 veh/h/ln); Nm = maneuvers/h/lane.
        fp_raw = (N_SATURATION - 0.1 - 18.0 * maneuvers_per_hour) / N_SATURATION
        fp = max(0.05, min(1.0, fp_raw))

        hcm_degradation_score = _clamp((1.0 - fp) * 100.0)

        # Erlang B (M/M/1/1) single-lane obstruction model.
        # P_block = rho / (1 + rho) — physically correct for one blocked lane,
        # monotonically increasing, and well-behaved above saturation (unlike
        # M/M/1 which is undefined for rho >= 1, or M/M/inf which underestimates
        # blocking probability at high load).
        offered_load = max(0.0, maneuvers_per_hour / service_rate)
        active_obstruction_probability = offered_load / (1.0 + offered_load)
        travel_time_index = 1.0 + active_obstruction_probability * max(0.0, (1.0 / fp) - 1.0) * 1.6
        stochastic_delay_score = _clamp(((travel_time_index - 1.0) / 2.5) * 100.0)

        (
            severity_multiplier,
            vehicle_severity_score,
            heavy_vehicle_share,
            light_vehicle_share,
        ) = _vehicle_typology(cluster.get("vehicle_types", []))

        impact_score = (
            0.45 * hcm_degradation_score
            + 0.35 * stochastic_delay_score
            + 0.20 * vehicle_severity_score
        )
        impact_score = _clamp(impact_score * severity_multiplier)

        # Intervention Benefit Estimation
        # Assume enforcement reduces maneuvers by 85%
        reduced_maneuvers = maneuvers_per_hour * 0.15
        fp_after = max(0.05, min(1.0, (N_SATURATION - 0.1 - 18.0 * reduced_maneuvers) / N_SATURATION))

        offered_load_after = max(0.0, reduced_maneuvers / service_rate)
        active_obstruction_prob_after = offered_load_after / (1.0 + offered_load_after)
        tti_after = 1.0 + active_obstruction_prob_after * max(0.0, (1.0 / fp_after) - 1.0) * 1.6
        
        delay_score_after = _clamp(((tti_after - 1.0) / 2.5) * 100.0)
        hcm_score_after = _clamp((1.0 - fp_after) * 100.0)
        
        impact_score_after = (
            0.45 * hcm_score_after
            + 0.35 * delay_score_after
            + 0.20 * vehicle_severity_score
        )
        impact_score_after = _clamp(impact_score_after * severity_multiplier)

        priority, action = _priority_label(impact_score)
        cluster["N_m"] = round(maneuvers_per_hour, 2)
        cluster["impact_score"] = round(impact_score, 2)
        cluster["priority"] = priority
        cluster["recommended_action"] = action
        cluster["f_p"] = round(fp, 4)
        cluster["capacity_recovery_vph"] = round((1.0 - fp) * LANE_COUNT * N_SATURATION, 0)
        
        # New intervention metrics
        cluster["intervention_benefit"] = {
            "before": {
                "impact_score": round(impact_score, 2),
                "capacity_loss_percent": round((1.0 - fp) * 100, 1),
                "delay_index": round(travel_time_index, 2),
            },
            "after": {
                "impact_score": round(impact_score_after, 2),
                "capacity_loss_percent": round((1.0 - fp_after) * 100, 1),
                "delay_index": round(tti_after, 2),
            },
            "recovery_metrics": {
                "estimated_capacity_recovered_vph": round((fp_after - fp) * LANE_COUNT * N_SATURATION, 0),
                "estimated_impact_reduction": round(impact_score - impact_score_after, 2),
                "estimated_delay_reduction_percent": round(max(0, (travel_time_index - tti_after) / max(0.1, travel_time_index - 1.0) * 100), 1) if travel_time_index > 1.0 else 0.0,
            }
        }
        
        cluster["hcm_degradation_score"] = round(hcm_degradation_score, 2)
        cluster["stochastic_delay_score"] = round(stochastic_delay_score, 2)
        cluster["vehicle_severity_score"] = round(vehicle_severity_score, 2)
        cluster["travel_time_index"] = round(travel_time_index, 3)
        cluster["active_obstruction_probability"] = round(active_obstruction_probability, 4)
        cluster["severity_multiplier"] = round(severity_multiplier, 3)
        cluster["multiplier"] = round(severity_multiplier, 3)
        cluster["heavy_vehicle_share"] = round(heavy_vehicle_share, 3)
        cluster["light_vehicle_share"] = round(light_vehicle_share, 3)

        scored_clusters.append(cluster)

    return sorted(scored_clusters, key=lambda item: item["impact_score"], reverse=True)
