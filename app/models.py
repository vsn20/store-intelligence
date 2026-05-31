import uuid
import enum
import os
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Float, Integer, DateTime, JSON
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from app.database import Base

# ── Dialect-aware JSONB ───────────────────────────────────────────────────────
# Use PostgreSQL JSONB in production, plain JSON for SQLite in tests.
_is_postgres = "postgresql" in os.getenv("DATABASE_URL", "sqlite")

if _is_postgres:
    from sqlalchemy.dialects.postgresql import JSONB
    _json_type = JSONB
else:
    _json_type = JSON


# ── SQLAlchemy ORM model ──────────────────────────────────────────────────────

class EventORM(Base):
    __tablename__ = "events"

    # Use String for UUID — works on both PostgreSQL and SQLite
    # PostgreSQL stores it as a VARCHAR(36), functionally identical for our queries
    event_id   = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    store_id   = Column(String,     nullable=False, index=True)
    camera_id  = Column(String,     nullable=False)
    visitor_id = Column(String,     nullable=False, index=True)
    event_type = Column(String,     nullable=False)
    timestamp  = Column(DateTime(timezone=True), nullable=False, index=True)
    zone_id    = Column(String,     nullable=True)
    dwell_ms   = Column(Integer,    default=0)
    is_staff   = Column(Boolean,    default=False)
    confidence = Column(Float,      nullable=False)
    metadata_  = Column("metadata", _json_type, nullable=True)


# ── Pydantic enums & schema ───────────────────────────────────────────────────

class EventType(str, enum.Enum):
    ENTRY                 = "ENTRY"
    EXIT                  = "EXIT"
    ZONE_ENTER            = "ZONE_ENTER"
    ZONE_EXIT             = "ZONE_EXIT"
    ZONE_DWELL            = "ZONE_DWELL"
    BILLING_QUEUE_JOIN    = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY               = "REENTRY"


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone:    Optional[str] = None
    session_seq: Optional[int] = None


class Event(BaseModel):
    event_id:   str      = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id:   str
    camera_id:  str
    visitor_id: str
    event_type: EventType
    timestamp:  datetime
    zone_id:    Optional[str]           = None
    dwell_ms:   int                     = 0
    is_staff:   bool                    = False
    confidence: float
    metadata:   Optional[EventMetadata] = None

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v

    @field_validator("zone_id")
    @classmethod
    def zone_required_for_zone_events(cls, v, info):
        zone_event_types = {
            EventType.ZONE_ENTER, EventType.ZONE_EXIT,
            EventType.ZONE_DWELL, EventType.BILLING_QUEUE_JOIN,
            EventType.BILLING_QUEUE_ABANDON,
        }
        if info.data.get("event_type") in zone_event_types and v is None:
            raise ValueError(f"zone_id required for {info.data.get('event_type')}")
        return v


class EventBatch(BaseModel):
    events: List[Event] = Field(..., max_length=500)


class IngestError(BaseModel):
    event_id: str
    reason:   str


class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    errors:   List[IngestError] = []