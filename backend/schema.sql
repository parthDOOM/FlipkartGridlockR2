CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS police_violations (
    id TEXT PRIMARY KEY,
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    violation_type TEXT NOT NULL,
    vehicle_type TEXT,
    created_datetime TIMESTAMPTZ NOT NULL,
    geom GEOMETRY(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_police_violations_geom_gist
    ON police_violations
    USING GIST (geom);

CREATE INDEX IF NOT EXISTS ix_police_violations_created_datetime
    ON police_violations (created_datetime);

CREATE INDEX IF NOT EXISTS ix_police_violations_violation_type
    ON police_violations (violation_type);

CREATE INDEX IF NOT EXISTS ix_police_violations_active_parking_geom
    ON police_violations
    USING GIST (geom)
    WHERE violation_type IN ('WRONG PARKING', 'DOUBLE PARKING', 'NO PARKING');

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    event_type TEXT NOT NULL,
    description TEXT,
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    expected_attendance INTEGER DEFAULT 0,
    severity TEXT DEFAULT 'medium',
    is_planned BOOLEAN DEFAULT TRUE,
    road_closure_required BOOLEAN DEFAULT FALSE,
    geom GEOMETRY(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_events_geom_gist ON events USING GIST (geom);
CREATE INDEX IF NOT EXISTS ix_events_start_time ON events (start_time);

CREATE TABLE IF NOT EXISTS impact_predictions (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    prediction_time TIMESTAMPTZ NOT NULL,
    impact_score DOUBLE PRECISION NOT NULL,
    confidence_score DOUBLE PRECISION DEFAULT 0.8,
    affected_zones JSONB,
    recommendations JSONB
);

CREATE TABLE IF NOT EXISTS event_feedback (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    actual_impact_score DOUBLE PRECISION,
    actual_severity TEXT,
    observation_notes TEXT,
    prediction_error DOUBLE PRECISION,
    effectiveness_score DOUBLE PRECISION
);
