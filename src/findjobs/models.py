"""SQLAlchemy ORM models for FindJobs."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    """Return current UTC time as a naive datetime (avoids deprecated utcnow)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Company(Base):
    """A company whose jobs we track."""

    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    homepage_url = Column(String(500), default="")
    careers_url = Column(String(500), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    sources = relationship("Source", back_populates="company")
    jobs = relationship("Job", back_populates="company")


class Source(Base):
    """A job data source (company careers page, RSS, etc.)."""

    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    slug = Column(String(100), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    source_type = Column(String(50), nullable=False, default="official_careers")
    base_url = Column(String(500), default="")
    is_active = Column(Boolean, default=True)
    config_yaml = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    company = relationship("Company", back_populates="sources")
    jobs = relationship("Job", back_populates="source")
    collect_runs = relationship("CollectRun", back_populates="source")


class Job(Base):
    """A single job posting discovered from a source."""

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    external_id = Column(String(200), default="")
    title = Column(String(300), nullable=False)
    url = Column(String(500), default="")
    description = Column(Text, default="")

    # Salary
    salary_text = Column(Text, default="")
    salary_min = Column(Float, nullable=True)
    salary_max = Column(Float, nullable=True)
    salary_currency = Column(String(10), default="CNY")
    salary_period = Column(String(20), default="yearly")
    salary_disclosed = Column(Boolean, default=False)

    # Location & type
    location = Column(String(200), default="")
    job_type = Column(String(50), default="")

    # Timing
    published_at = Column(DateTime, nullable=True)
    first_seen_at = Column(DateTime, default=_utcnow)
    last_seen_at = Column(DateTime, default=_utcnow)

    # Status & tags
    status = Column(String(20), default="active", index=True)
    matched_tags = Column(Text, default="")

    # Metadata
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    company = relationship("Company", back_populates="jobs")
    source = relationship("Source", back_populates="jobs")
    observations = relationship("JobObservation", back_populates="job")
    user_marks = relationship("UserMark", back_populates="job")


class JobObservation(Base):
    """Record that a job was seen during a collection run, with optional change tracking."""

    __tablename__ = "job_observations"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    collect_run_id = Column(Integer, ForeignKey("collect_runs.id"), nullable=True)
    seen_at = Column(DateTime, default=_utcnow, nullable=False)
    raw_payload = Column(Text, nullable=True)
    field_name = Column(String(100), nullable=True)
    old_value = Column(Text, default="")
    new_value = Column(Text, default="")

    job = relationship("Job", back_populates="observations")


class CollectRun(Base):
    """Log of a single collection cycle for a source."""

    __tablename__ = "collect_runs"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    status = Column(String(20), nullable=False, default="running")
    started_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)
    jobs_found = Column(Integer, default=0)
    jobs_new = Column(Integer, default=0)
    errors = Column(Text, default="")

    source = relationship("Source", back_populates="collect_runs")


class UserMark(Base):
    """User bookmark / hide / apply note for a job."""

    __tablename__ = "user_marks"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    mark_type = Column(String(20), nullable=False, default="bookmark")
    note = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    job = relationship("Job", back_populates="user_marks")
