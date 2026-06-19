import argparse
import os
import json
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from database import Base, SQLALCHEMY_DATABASE_URL
from models import PoliceViolation

ACTIVE_PARKING_VIOLATIONS = {"WRONG PARKING", "DOUBLE PARKING", "NO PARKING"}
PEAK_HOURS_LOCAL = {8, 9, 10, 17, 18, 19}
BATCH_SIZE = 5000


def get_active_violation(value):
    if pd.isna(value):
        return None

    text_value = str(value).upper()
    
    # Try parsing as JSON list first
    try:
        import json
        parsed = json.loads(text_value)
        if isinstance(parsed, list):
            for v in parsed:
                v_clean = str(v).upper().strip()
                if v_clean in ACTIVE_PARKING_VIOLATIONS:
                    return v_clean
    except:
        pass

    # Fallback to substring check for exact types
    for violation in ACTIVE_PARKING_VIOLATIONS:
        if violation in text_value:
            return violation
            
    return None


def run_ingestion(csv_path: str = None, db_url: str = None):
    # Resolve CSV path: argument > env var > default sibling path
    if not csv_path:
        csv_path = os.getenv("CSV_FILE") or str(
            Path(__file__).resolve().parents[1] / "jan to may police violation_anonymized791b166.csv"
        )
    csv_file = Path(csv_path)
    if not csv_file.exists():
        print(f"CSV file not found: {csv_file}")
        print("Set CSV_FILE env var or pass --csv-file argument.")
        return

    # Allow DATABASE_URL override for Cloud SQL Auth Proxy connections
    url = db_url or os.getenv("DATABASE_URL") or SQLALCHEMY_DATABASE_URL
    engine = create_engine(url)

    print("Creating PostGIS extension and tables...")
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(engine)

    print(f"Loading {csv_file}...")
    df = pd.read_csv(csv_file, low_memory=False)

    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["id", "latitude", "longitude", "violation_type", "created_datetime"])
    df = df[df["latitude"].between(-90, 90) & df["longitude"].between(-180, 180)]
    
    df["normalized_violation"] = df["violation_type"].apply(get_active_violation)
    df = df.dropna(subset=["normalized_violation"])

    df["created_datetime"] = pd.to_datetime(df["created_datetime"], errors="coerce", utc=True)
    df = df.dropna(subset=["created_datetime"])
    df["local_time"] = df["created_datetime"].dt.tz_convert("Asia/Kolkata")
    df = df[df["local_time"].dt.hour.isin(PEAK_HOURS_LOCAL)]

    print(f"Filtered down to {len(df)} peak-hour parking violations.")
    if df.empty:
        return

    print("Inserting data into database...")
    with Session(engine) as session:
        session.execute(text("TRUNCATE TABLE police_violations"))

        objects = []
        for _, row in df.iterrows():
            longitude = float(row["longitude"])
            latitude = float(row["latitude"])
            vehicle_type = row.get("vehicle_type")

            objects.append(
                PoliceViolation(
                    id=str(row["id"]),
                    latitude=latitude,
                    longitude=longitude,
                    violation_type=row["normalized_violation"],
                    vehicle_type=str(vehicle_type).upper() if pd.notnull(vehicle_type) else None,
                    created_datetime=row["created_datetime"].to_pydatetime(),
                    geom=f"SRID=4326;POINT({longitude} {latitude})",
                )
            )

            if len(objects) >= BATCH_SIZE:
                session.bulk_save_objects(objects)
                session.commit()
                objects = []

        if objects:
            session.bulk_save_objects(objects)
            session.commit()

    print("Data ingestion complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Bangalore police violation CSV into PostgreSQL.")
    parser.add_argument("--csv-file", metavar="PATH", help="Path to violation CSV (overrides CSV_FILE env var)")
    parser.add_argument("--db-url", metavar="URL", help="Database URL (overrides DATABASE_URL env var)")
    args = parser.parse_args()
    run_ingestion(csv_path=args.csv_file, db_url=args.db_url)
