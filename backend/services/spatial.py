from collections import Counter
import os

import hdbscan
import numpy as np
from shapely.geometry import MultiPoint
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from models import PoliceViolation

ACTIVE_PARKING_VIOLATIONS = ("WRONG PARKING", "DOUBLE PARKING", "NO PARKING")
MAX_HULL_POINTS = 180


def active_parking_filter():
    return PoliceViolation.violation_type.in_(ACTIVE_PARKING_VIOLATIONS)


def get_postgis_clusters(db: Session, eps_meters: float = 35.0, min_samples: int = 5, time_hour: int = None):
    """
    Experimental clustering using PostGIS ST_ClusterDBSCAN.
    Faster for massive datasets but less robust than HDBSCAN on density variations.
    """
    time_filter = ""
    if time_hour is not None:
        time_filter = f"AND EXTRACT(HOUR FROM created_datetime) = {int(time_hour)}"

    # Using 3857 for meter-based epsilon
    sql = text(f"""
        WITH clustered_points AS (
            SELECT 
                id, latitude, longitude, vehicle_type, violation_type,
                ST_ClusterDBSCAN(ST_Transform(geom, 3857), eps := :eps, minpoints := :min_samples) over () as cid
            FROM police_violations
            WHERE violation_type IN :active_violations
              AND latitude BETWEEN -90 AND 90
              AND longitude BETWEEN -180 AND 180
              {time_filter}
        )
        SELECT id, latitude, longitude, vehicle_type, violation_type, cid
        FROM clustered_points
        WHERE cid IS NOT NULL
        ORDER BY cid;
    """)
    
    result = db.execute(sql, {
        "eps": eps_meters, 
        "min_samples": min_samples, 
        "active_violations": ACTIVE_PARKING_VIOLATIONS
    }).all()
    
    if not result:
        return []

    # Group by cid
    groups = {}
    for row in result:
        cid = row.cid
        if cid not in groups:
            groups[cid] = []
        groups[cid].append(row)

    clusters = []
    for cid, points in groups.items():
        raw_count = len(points)
        latitudes = [p.latitude for p in points]
        longitudes = [p.longitude for p in points]
        vehicle_types = [p.vehicle_type for p in points]
        vehicle_summary = _vehicle_summary(vehicle_types)

        clusters.append({
            "cluster_id": int(cid),
            "centroid": [float(np.mean(longitudes)), float(np.mean(latitudes))],
            "N_m": float(raw_count),
            "raw_count": raw_count,
            "vehicle_types": vehicle_types,
            "violation_types": [p.violation_type for p in points],
            "polygon": _polygon_from_points(points),
            "bbox": [
                float(min(longitudes)),
                float(min(latitudes)),
                float(max(longitudes)),
                float(max(latitudes)),
            ],
            "avg_membership_probability": 1.0,  # DBSCAN is hard assignment
            **vehicle_summary,
        })
    
    return clusters


def _polygon_from_points(points):
    if len(points) > MAX_HULL_POINTS:
        step = max(1, len(points) // MAX_HULL_POINTS)
        points = points[::step]

    multipoint = MultiPoint([(point.longitude, point.latitude) for point in points])
    hull = multipoint.convex_hull

    if hull.geom_type != "Polygon":
        hull = multipoint.buffer(0.00045)

    if hull.geom_type == "Polygon":
        return [[float(lon), float(lat)] for lon, lat in hull.exterior.coords]

    return []


def _vehicle_summary(vehicle_types):
    normalized = [str(vehicle).upper() for vehicle in vehicle_types if vehicle]
    counts = Counter(normalized)
    return {
        "top_vehicle_type": counts.most_common(1)[0][0] if counts else "UNKNOWN",
        "vehicle_mix": dict(counts.most_common(6)),
        "heavy_vehicle_count": sum(
            count
            for vehicle, count in counts.items()
            if any(token in vehicle for token in ("TANKER", "BUS", "LORRY", "GOODS"))
        ),
        "scooter_count": sum(
            count
            for vehicle, count in counts.items()
            if "SCOOTER" in vehicle or "MOTOR CYCLE" in vehicle
        ),
    }


def _project_coordinates(violations):
    latitudes = np.array([violation.latitude for violation in violations], dtype=float)
    longitudes = np.array([violation.longitude for violation in violations], dtype=float)
    mean_latitude = float(np.mean(latitudes))
    x = longitudes * math_cos_degrees(mean_latitude) * 111.320
    y = latitudes * 110.574
    return np.column_stack((x, y))


def math_cos_degrees(value):
    return float(np.cos(np.radians(value)))


def get_congestion_clusters(db: Session, min_cluster_size: int = 15, min_samples: int = 5, time_hour: int = None):
    query = db.query(
        PoliceViolation.id,
        PoliceViolation.latitude,
        PoliceViolation.longitude,
        PoliceViolation.vehicle_type,
        PoliceViolation.violation_type,
    ).filter(active_parking_filter())
    
    if time_hour is not None:
        query = query.filter(func.extract('hour', PoliceViolation.created_datetime) == time_hour)
        
    violations = query.filter(PoliceViolation.latitude.between(-90, 90)) \
        .filter(PoliceViolation.longitude.between(-180, 180)) \
        .all()

    if len(violations) < min_cluster_size:
        return []

    coords = _project_coordinates(violations)

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="euclidean",
        core_dist_n_jobs=max(1, (os.cpu_count() or 2) - 1),
    )
    cluster_labels = clusterer.fit_predict(coords)
    probabilities = getattr(clusterer, "probabilities_", np.ones(len(violations)))

    clusters = []
    for label in sorted(set(cluster_labels)):
        if label == -1:
            continue

        member_indexes = np.where(cluster_labels == label)[0]
        cluster_points = [violations[index] for index in member_indexes]
        raw_count = len(cluster_points)
        if raw_count == 0:
            continue

        latitudes = [point.latitude for point in cluster_points]
        longitudes = [point.longitude for point in cluster_points]
        vehicle_types = [point.vehicle_type for point in cluster_points]
        vehicle_summary = _vehicle_summary(vehicle_types)

        clusters.append(
            {
                "cluster_id": int(label),
                "centroid": [float(np.mean(longitudes)), float(np.mean(latitudes))],
                "N_m": float(raw_count),
                "raw_count": raw_count,
                "vehicle_types": vehicle_types,
                "violation_types": [point.violation_type for point in cluster_points],
                "polygon": _polygon_from_points(cluster_points),
                "bbox": [
                    float(min(longitudes)),
                    float(min(latitudes)),
                    float(max(longitudes)),
                    float(max(latitudes)),
                ],
                "avg_membership_probability": float(np.mean(probabilities[member_indexes])),
                **vehicle_summary,
            }
        )

    return clusters
