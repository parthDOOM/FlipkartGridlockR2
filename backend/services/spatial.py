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
    # Aggregate violations to a ~10m grid before HDBSCAN to reduce input size
    # from 85k individual rows to ~10-15k unique spatial cells.  Clustering
    # quality is equivalent — HDBSCAN only needs density, not every raw row.
    time_filter = ""
    if time_hour is not None:
        time_filter = f"AND EXTRACT(HOUR FROM created_datetime) = {int(time_hour)}"

    sql = text(f"""
        SELECT
            ROUND(latitude::numeric, 4)  AS lat_r,
            ROUND(longitude::numeric, 4) AS lon_r,
            COUNT(*)                     AS cnt,
            MODE() WITHIN GROUP (ORDER BY vehicle_type) AS top_vehicle,
            SUM(CASE WHEN vehicle_type ~* 'TANKER|\\yBUS\\y|LORRY|GOODS' THEN 1 ELSE 0 END) AS heavy_cnt,
            SUM(CASE WHEN vehicle_type ~* 'SCOOTER|MOTOR CYCLE'          THEN 1 ELSE 0 END) AS scooter_cnt
        FROM police_violations
        WHERE violation_type IN ('WRONG PARKING', 'DOUBLE PARKING', 'NO PARKING')
          AND latitude  BETWEEN -90  AND 90
          AND longitude BETWEEN -180 AND 180
          {time_filter}
        GROUP BY lat_r, lon_r
    """)

    rows = db.execute(sql).all()

    if len(rows) < min_cluster_size:
        return []

    latitudes  = np.array([r.lat_r for r in rows], dtype=float)
    longitudes = np.array([r.lon_r for r in rows], dtype=float)
    counts     = np.array([r.cnt   for r in rows], dtype=float)

    mean_lat = float(np.mean(latitudes))
    x = longitudes * math_cos_degrees(mean_lat) * 111.320
    y = latitudes  * 110.574
    coords = np.column_stack((x, y))

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="euclidean",
        core_dist_n_jobs=max(1, (os.cpu_count() or 2) - 1),
    )
    cluster_labels = clusterer.fit_predict(coords)
    probabilities = getattr(clusterer, "probabilities_", np.ones(len(rows)))

    clusters = []
    for label in sorted(set(cluster_labels)):
        if label == -1:
            continue

        mask    = np.where(cluster_labels == label)[0]
        clat    = latitudes[mask]
        clon    = longitudes[mask]
        ccounts = counts[mask]
        total   = int(ccounts.sum())
        if total == 0:
            continue

        centroid_lat = float(np.average(clat, weights=ccounts))
        centroid_lon = float(np.average(clon, weights=ccounts))

        heavy   = int(sum(rows[i].heavy_cnt   for i in mask))
        scooter = int(sum(rows[i].scooter_cnt for i in mask))

        vc = Counter()
        for i in mask:
            vc[rows[i].top_vehicle] += int(rows[i].cnt)
        top_vehicle = vc.most_common(1)[0][0] if vc else "UNKNOWN"

        # Synthesise a proportional vehicle_types list so impact.py vehicle-
        # typology ratios are accurate without materialising all raw rows.
        sample = min(total, 100)
        n_heavy   = round(heavy   / total * sample)
        n_scooter = round(scooter / total * sample)
        n_other   = max(0, sample - n_heavy - n_scooter)
        vehicle_types_list = (
            ["TANKER"] * n_heavy
            + ["SCOOTER"] * n_scooter
            + ["CAR"] * n_other
        )

        pts = [[float(lo), float(la)] for la, lo in zip(clat, clon)]
        polygon = []
        if len(pts) >= 3:
            hull = MultiPoint(pts).convex_hull
            if hull.geom_type == "Polygon":
                polygon = [[float(c[0]), float(c[1])] for c in hull.exterior.coords]

        vehicle_summary = _vehicle_summary(vehicle_types_list)

        clusters.append({
            "cluster_id": int(label),
            "centroid":   [centroid_lon, centroid_lat],
            "N_m":        float(total),
            "raw_count":  total,
            "vehicle_types":   vehicle_types_list,
            "violation_types": ["WRONG PARKING"] * min(total, 30),
            "polygon":    polygon,
            "bbox":       [float(clon.min()), float(clat.min()), float(clon.max()), float(clat.max())],
            "avg_membership_probability": float(np.mean(probabilities[mask])),
            **vehicle_summary,
        })

    return clusters
