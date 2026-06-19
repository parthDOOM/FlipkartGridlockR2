import math
import json
import os
from functools import lru_cache
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pulp
from ortools.linear_solver import pywraplp

DEFAULT_DEPOT = [77.5946, 12.9716]
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
ROUTE_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ROUTE_REQUEST_TIMEOUT_SECONDS", "2.5"))
# Fixed reference for distance normalisation: distance_penalty=14 means
# travelling 1 km costs 14/REFERENCE_KM impact points — consistent across
# all problem instances regardless of how spread-out the candidates are.
DISTANCE_REFERENCE_KM = 5.0
ROUTE_COLORS = [
    [56, 189, 248],
    [250, 204, 21],
    [52, 211, 153],
    [248, 113, 113],
    [167, 139, 250],
]


def haversine_km(point_a, point_b):
    lon1, lat1 = point_a
    lon2, lat2 = point_b
    radius_km = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _round_point(point):
    return (round(float(point[0]), 6), round(float(point[1]), 6))


@lru_cache(maxsize=128)
def _cached_distance_matrix(point_tuple):
    points = np.array(point_tuple)
    size = len(points)
    matrix = np.zeros((size, size), dtype=float)
    for i in range(size):
        for j in range(size):
            if i != j:
                matrix[i, j] = haversine_km(points[i], points[j])
    return matrix


def _route_distance(path):
    return round(sum(haversine_km(path[i], path[i + 1]) for i in range(len(path) - 1)), 3)


def _prefilter_candidates(clusters, depot, candidate_limit, distance_penalty):
    """
    Select the top-N candidates using the same impact-vs-distance tradeoff
    as the MILP objective.  Pure impact-score ranking (the previous approach)
    could select far-away high-scoring clusters while ignoring adjacent
    medium-scoring ones that the solver would actually prefer.
    """
    def composite(cluster):
        dist_km = haversine_km(depot, cluster["centroid"])
        return cluster["impact_score"] - distance_penalty * (dist_km / DISTANCE_REFERENCE_KM)

    return sorted(clusters, key=composite, reverse=True)[:candidate_limit]


@lru_cache(maxsize=256)
def _fetch_osrm_route(point_key):
    coordinates = ";".join(f"{lon},{lat}" for lon, lat in point_key)
    query = urlencode(
        {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }
    )
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coordinates}?{query}"
    request = Request(url, headers={"User-Agent": "parking-congestion-optimizer/2.1"})

    with urlopen(request, timeout=ROUTE_REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ValueError(payload.get("message") or "No road route returned")

    route = payload["routes"][0]
    geometry = route.get("geometry", {}).get("coordinates") or []
    if len(geometry) < 2:
        raise ValueError("Road route geometry was empty")

    return {
        "path": [[float(lon), float(lat)] for lon, lat in geometry],
        "distance_km": round(float(route.get("distance", 0.0)) / 1000.0, 3),
        "duration_minutes": round(float(route.get("duration", 0.0)) / 60.0, 1),
    }


def _route_geometry(straight_path, route_geometry):
    straight_distance = _route_distance(straight_path) if len(straight_path) > 1 else 0.0
    if route_geometry == "straight" or len(straight_path) < 2:
        return {
            "path": straight_path,
            "distance_km": straight_distance,
            "duration_minutes": None,
            "geometry_source": "straight",
            "geometry_warning": None,
        }

    try:
        road_route = _fetch_osrm_route(tuple(_round_point(point) for point in straight_path))
        return {
            **road_route,
            "geometry_source": "road",
            "geometry_warning": None,
        }
    except (TimeoutError, URLError, ValueError, OSError, json.JSONDecodeError):
        return {
            "path": straight_path,
            "distance_km": straight_distance,
            "duration_minutes": None,
            "geometry_source": "straight-fallback",
            "geometry_warning": "Road routing unavailable; showing direct patrol legs.",
        }


def _build_route(vehicle_index, straight_path, stops, solver_status, fallback=False, route_geometry="road"):
    geometry = _route_geometry(straight_path, route_geometry)
    return {
        "vehicle_id": f"patrol-{vehicle_index + 1}",
        "path": geometry["path"],
        "straight_path": straight_path,
        "stops": stops,
        "stop_count": len(stops),
        "total_impact": round(sum(stop["impact_score"] for stop in stops), 2),
        "distance_km": geometry["distance_km"],
        "straight_distance_km": _route_distance(straight_path) if len(straight_path) > 1 else 0.0,
        "duration_minutes": geometry["duration_minutes"],
        "geometry_source": geometry["geometry_source"],
        "geometry_warning": geometry["geometry_warning"],
        "solver_status": solver_status,
        "fallback": fallback,
        "color": ROUTE_COLORS[vehicle_index % len(ROUTE_COLORS)],
    }


def _greedy_routes(candidates, vehicle_count, max_stops, depot, solver_status, route_geometry):
    remaining = candidates[:]
    routes = []

    for vehicle_index in range(vehicle_count):
        if not remaining:
            break

        path = [depot]
        stops = []
        current = depot

        while remaining and len(stops) < max_stops:
            next_cluster = max(
                remaining,
                key=lambda cluster: cluster["impact_score"]
                / max(0.75, haversine_km(current, cluster["centroid"])),
            )
            remaining.remove(next_cluster)
            stops.append(
                {
                    "cluster_id": next_cluster["cluster_id"],
                    "centroid": next_cluster["centroid"],
                    "impact_score": next_cluster["impact_score"],
                }
            )
            path.append(next_cluster["centroid"])
            current = next_cluster["centroid"]

        path.append(depot)
        routes.append(_build_route(vehicle_index, path, stops, solver_status, fallback=True, route_geometry=route_geometry))

    return routes


def optimize_patrol_routes_ortools(
    clusters,
    vehicle_count=2,
    max_stops=5,
    distance_penalty=14.0,
    candidate_limit=18,
    depot=None,
    time_limit_seconds=12,
    route_geometry="road",
):
    if not clusters:
        return []

    depot = depot or DEFAULT_DEPOT
    candidates = _prefilter_candidates(clusters, depot, candidate_limit, distance_penalty)
    n = len(candidates)
    vehicle_count = max(1, int(vehicle_count))
    max_stops = max(1, min(int(max_stops), n))

    if n <= 2:
        path = [depot] + [cluster["centroid"] for cluster in candidates] + [depot]
        stops = [
            {
                "cluster_id": cluster["cluster_id"],
                "centroid": cluster["centroid"],
                "impact_score": cluster["impact_score"],
            }
            for cluster in candidates
        ]
        return [_build_route(0, path, stops, "Trivial", route_geometry=route_geometry)]

    all_points = [depot] + [cluster["centroid"] for cluster in candidates]
    point_tuple = tuple(tuple(p) for p in all_points)
    distances = _cached_distance_matrix(point_tuple)
    normalized_distances = distances / DISTANCE_REFERENCE_KM

    cluster_nodes = range(1, n + 1)
    all_nodes = range(n + 1)
    vehicles = range(vehicle_count)

    # Use SCIP or GLOP/CBC through OR-Tools. SCIP is generally better for MILP.
    solver = pywraplp.Solver.CreateSolver("SCIP")
    if not solver:
        # Fallback to PuLP if SCIP not available in ortools build
        return optimize_patrol_routes(
            clusters, vehicle_count, max_stops, distance_penalty, 
            candidate_limit, depot, time_limit_seconds, route_geometry
        )

    solver.SetTimeLimit(int(time_limit_seconds * 1000))

    x = {}
    for v in vehicles:
        for i in all_nodes:
            for j in all_nodes:
                if i != j:
                    x[v, i, j] = solver.BoolVar(f"x_{v}_{i}_{j}")

    y = {}
    for v in vehicles:
        for node in cluster_nodes:
            y[v, node] = solver.BoolVar(f"y_{v}_{node}")

    use_vehicle = [solver.BoolVar(f"use_v_{v}") for v in vehicles]
    
    order = {}
    for v in vehicles:
        for node in cluster_nodes:
            order[v, node] = solver.NumVar(0, max_stops, f"order_{v}_{node}")

    # Objective
    objective = solver.Objective()
    for v in vehicles:
        for node in cluster_nodes:
            objective.SetCoefficient(y[v, node], float(candidates[node - 1]["impact_score"]))
        for i in all_nodes:
            for j in all_nodes:
                if i != j:
                    objective.SetCoefficient(x[v, i, j], -float(distance_penalty * normalized_distances[i, j]))
    objective.SetMaximization()

    # Constraints
    for node in cluster_nodes:
        solver.Add(solver.Sum(y[v, node] for v in vehicles) <= 1)

    for v in vehicles:
        solver.Add(solver.Sum(x[v, 0, j] for j in cluster_nodes) == use_vehicle[v])
        solver.Add(solver.Sum(x[v, i, 0] for i in cluster_nodes) == use_vehicle[v])
        solver.Add(solver.Sum(y[v, node] for node in cluster_nodes) <= max_stops * use_vehicle[v])
        solver.Add(solver.Sum(y[v, node] for node in cluster_nodes) >= use_vehicle[v])

        for node in cluster_nodes:
            solver.Add(solver.Sum(x[v, i, node] for i in all_nodes if i != node) == y[v, node])
            solver.Add(solver.Sum(x[v, node, j] for j in all_nodes if j != node) == y[v, node])
            solver.Add(order[v, node] <= max_stops * y[v, node])
            solver.Add(order[v, node] >= y[v, node])

        for i in cluster_nodes:
            for j in cluster_nodes:
                if i != j:
                    solver.Add(order[v, i] - order[v, j] + max_stops * x[v, i, j] <= max_stops - 1)

    solver.Add(solver.Sum(use_vehicle) >= min(vehicle_count, n))

    status = solver.Solve()

    if status != pywraplp.Solver.OPTIMAL and status != pywraplp.Solver.FEASIBLE:
        return _greedy_routes(candidates, vehicle_count, max_stops, depot, "OR-Tools-Failed", route_geometry)

    routes = []
    for v in vehicles:
        if use_vehicle[v].solution_value() < 0.5:
            continue

        path = [depot]
        stops = []
        current = 0
        seen = set()

        for _ in range(max_stops + 1):
            next_node = None
            for node in all_nodes:
                if node != current and (v, current, node) in x and x[v, current, node].solution_value() > 0.5:
                    next_node = node
                    break

            if next_node is None or next_node == 0:
                break
            if next_node in seen:
                break

            seen.add(next_node)
            cluster = candidates[next_node - 1]
            stops.append(
                {
                    "cluster_id": cluster["cluster_id"],
                    "centroid": cluster["centroid"],
                    "impact_score": cluster["impact_score"],
                }
            )
            path.append(cluster["centroid"])
            current = next_node

        path.append(depot)
        if stops:
            routes.append(_build_route(v, path, stops, "OR-Tools-Optimal", route_geometry=route_geometry))

    return routes


def optimize_patrol_routes(
    clusters,
    vehicle_count=2,
    max_stops=5,
    distance_penalty=14.0,
    candidate_limit=18,
    depot=None,
    time_limit_seconds=12,
    route_geometry="road",
):
    if not clusters:
        return []

    depot = depot or DEFAULT_DEPOT
    vehicle_count = max(1, int(vehicle_count))

    # MILP binary var count ≈ vehicle_count × n × (n+1). CBC/Windows becomes
    # unreliable above ~1200 binaries → use greedy for large instances.
    # Greedy is O(n) so give it a full pool (vehicle_count × max_stops candidates).
    milp_n = min(candidate_limit, 18)
    milp_feasible = vehicle_count * milp_n * (milp_n + 1) <= 1200

    if milp_feasible:
        candidates = _prefilter_candidates(clusters, depot, milp_n, distance_penalty)
    else:
        greedy_limit = max(candidate_limit, vehicle_count * max_stops)
        candidates = _prefilter_candidates(clusters, depot, greedy_limit, distance_penalty)

    n = len(candidates)
    max_stops = max(1, min(int(max_stops), n))

    if not milp_feasible:
        return _greedy_routes(candidates, vehicle_count, max_stops, depot, "Greedy-LargeInstance", route_geometry)

    if n <= 2:
        path = [depot] + [cluster["centroid"] for cluster in candidates] + [depot]
        stops = [
            {
                "cluster_id": cluster["cluster_id"],
                "centroid": cluster["centroid"],
                "impact_score": cluster["impact_score"],
            }
            for cluster in candidates
        ]
        return [_build_route(0, path, stops, "Trivial", route_geometry=route_geometry)]

    all_points = [depot] + [cluster["centroid"] for cluster in candidates]
    point_tuple = tuple(tuple(p) for p in all_points)
    distances = _cached_distance_matrix(point_tuple)
    # Normalise by a fixed city-scale reference so distance_penalty has the
    # same meaning across all problem instances (dense urban vs city-wide).
    normalized_distances = distances / DISTANCE_REFERENCE_KM

    cluster_nodes = range(1, n + 1)
    all_nodes = range(n + 1)
    vehicles = range(vehicle_count)

    problem = pulp.LpProblem("ParkingInducedCongestionPatrolRouting", pulp.LpMaximize)

    x = pulp.LpVariable.dicts(
        "x",
        [
            (vehicle, i, j)
            for vehicle in vehicles
            for i in all_nodes
            for j in all_nodes
            if i != j
        ],
        cat="Binary",
    )
    y = pulp.LpVariable.dicts(
        "y",
        [(vehicle, node) for vehicle in vehicles for node in cluster_nodes],
        cat="Binary",
    )
    use_vehicle = pulp.LpVariable.dicts("use_vehicle", vehicles, cat="Binary")
    order = pulp.LpVariable.dicts(
        "order",
        [(vehicle, node) for vehicle in vehicles for node in cluster_nodes],
        lowBound=0,
        upBound=max_stops,
        cat="Continuous",
    )

    problem += (
        pulp.lpSum(
            candidates[node - 1]["impact_score"] * y[(vehicle, node)]
            for vehicle in vehicles
            for node in cluster_nodes
        )
        - pulp.lpSum(
            distance_penalty * normalized_distances[i, j] * x[(vehicle, i, j)]
            for vehicle in vehicles
            for i in all_nodes
            for j in all_nodes
            if i != j
        )
    )

    for node in cluster_nodes:
        problem += pulp.lpSum(y[(vehicle, node)] for vehicle in vehicles) <= 1

    for vehicle in vehicles:
        problem += pulp.lpSum(x[(vehicle, 0, j)] for j in cluster_nodes) == use_vehicle[vehicle]
        problem += pulp.lpSum(x[(vehicle, i, 0)] for i in cluster_nodes) == use_vehicle[vehicle]
        problem += pulp.lpSum(y[(vehicle, node)] for node in cluster_nodes) <= max_stops * use_vehicle[vehicle]
        problem += pulp.lpSum(y[(vehicle, node)] for node in cluster_nodes) >= use_vehicle[vehicle]

        for node in cluster_nodes:
            problem += (
                pulp.lpSum(x[(vehicle, i, node)] for i in all_nodes if i != node)
                == y[(vehicle, node)]
            )
            problem += (
                pulp.lpSum(x[(vehicle, node, j)] for j in all_nodes if j != node)
                == y[(vehicle, node)]
            )
            problem += order[(vehicle, node)] <= max_stops * y[(vehicle, node)]
            problem += order[(vehicle, node)] >= y[(vehicle, node)]

        for i in cluster_nodes:
            for j in cluster_nodes:
                if i != j:
                    problem += (
                        order[(vehicle, i)]
                        - order[(vehicle, j)]
                        + max_stops * x[(vehicle, i, j)]
                        <= max_stops - 1
                    )

    # Use at least as many vehicles as requested, capped by available candidates.
    problem += pulp.lpSum(use_vehicle[vehicle] for vehicle in vehicles) >= min(vehicle_count, n)

    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit_seconds)
    problem.solve(solver)
    solver_status = pulp.LpStatus[problem.status]

    if solver_status != "Optimal":
        return _greedy_routes(candidates, vehicle_count, max_stops, depot, solver_status, route_geometry)

    routes = []
    for vehicle in vehicles:
        if pulp.value(use_vehicle[vehicle]) < 0.5:
            continue

        path = [depot]
        stops = []
        current = 0
        seen = set()

        for _ in range(max_stops + 1):
            next_node = None
            for node in all_nodes:
                if node != current and (vehicle, current, node) in x and pulp.value(x[(vehicle, current, node)]) > 0.5:
                    next_node = node
                    break

            if next_node is None or next_node == 0:
                break
            if next_node in seen:
                break

            seen.add(next_node)
            cluster = candidates[next_node - 1]
            stops.append(
                {
                    "cluster_id": cluster["cluster_id"],
                    "centroid": cluster["centroid"],
                    "impact_score": cluster["impact_score"],
                }
            )
            path.append(cluster["centroid"])
            current = next_node

        path.append(depot)
        if stops:
            routes.append(_build_route(vehicle, path, stops, solver_status, route_geometry=route_geometry))

    if not routes:
        return _greedy_routes(candidates, vehicle_count, max_stops, depot, solver_status, route_geometry)

    return routes
