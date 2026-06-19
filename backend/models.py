from sqlalchemy import Column, DateTime, Float, Index, String, JSON, Integer, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy import func
from geoalchemy2 import Geometry
from database import Base

class PoliceViolation(Base):
    __tablename__ = "police_violations"
    __table_args__ = (
        Index("ix_police_violations_geom_gist", "geom", postgresql_using="gist"),
        Index("ix_police_violations_created_datetime", "created_datetime"),
        Index("ix_police_violations_violation_type", "violation_type"),
    )

    id = Column(String, primary_key=True, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    violation_type = Column(String, nullable=False)
    vehicle_type = Column(String, nullable=True)
    created_datetime = Column(DateTime(timezone=True), nullable=False)
    
    # PostGIS geometry column for fast spatial queries
    geom = Column(Geometry(geometry_type='POINT', srid=4326), nullable=False)

class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_geom_gist", "geom", postgresql_using="gist"),
        Index("ix_events_start_time", "start_time"),
    )

    id = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    event_type = Column(String, nullable=False)  # 'political', 'sports', 'festival', 'construction', 'accident', etc.
    description = Column(String)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    expected_attendance = Column(Integer, default=0)
    severity = Column(String, default="medium")  # 'low', 'medium', 'high', 'critical'
    is_planned = Column(Boolean, default=True)
    road_closure_required = Column(Boolean, default=False)
    
    geom = Column(Geometry(geometry_type='POINT', srid=4326), nullable=False)
    
    predictions = relationship("ImpactPrediction", back_populates="event")
    feedback = relationship("EventFeedback", back_populates="event", uselist=False)

class ImpactPrediction(Base):
    __tablename__ = "impact_predictions"
    
    id = Column(String, primary_key=True, index=True)
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    prediction_time = Column(DateTime(timezone=True), nullable=False)
    
    # Aggregated scores
    impact_score = Column(Float, nullable=False)  # 0-100
    confidence_score = Column(Float, default=0.8)
    
    # Spatial impact
    affected_zones = Column(JSON)  # List of {lat, lon, radius, severity}
    
    # Recommendations
    recommendations = Column(JSON)  # {manpower: 10, barricades: 20, diversions: [...]}
    
    event = relationship("Event", back_populates="predictions")

class EventFeedback(Base):
    __tablename__ = "event_feedback"
    
    id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    event_id = Column(String, ForeignKey("events.id"), nullable=False)

    actual_impact_score = Column(Float)
    actual_severity = Column(String)
    observation_notes = Column(String)
    
    # Comparative metrics
    prediction_error = Column(Float)
    effectiveness_score = Column(Float) # 0-100, how well the recommendations worked
    
    event = relationship("Event", back_populates="feedback")
