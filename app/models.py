import uuid
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String, nullable=False)
    status = Column(String, nullable=False, default="PENDING")  # PENDING, PROCESSING, COMPLETED, FAILED
    row_count_raw = Column(Integer, nullable=False, default=0)
    row_count_clean = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(String, nullable=True)

    # Relationships
    transactions = relationship("Transaction", back_populates="job", cascade="all, delete-orphan")
    summary = relationship("JobSummary", uselist=False, back_populates="job", cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    txn_id = Column(String, nullable=True)  # Can be blank in dirty CSV
    date = Column(String, nullable=True)     # Normalized to YYYY-MM-DD or standard ISO 8601
    merchant = Column(String, nullable=True)
    amount = Column(Float, nullable=True)
    currency = Column(String, nullable=True)
    status = Column(String, nullable=True)
    category = Column(String, nullable=True)
    account_id = Column(String, nullable=True)
    
    # Anomaly tracking
    is_anomaly = Column(Boolean, default=False)
    anomaly_reason = Column(String, nullable=True)
    
    # LLM classification metadata
    llm_category_raw_response = Column(String, nullable=True)
    llm_failed = Column(Boolean, default=False)

    # Relationship
    job = relationship("Job", back_populates="transactions")


class JobSummary(Base):
    __tablename__ = "job_summaries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False)
    total_spend_inr = Column(Float, default=0.0)
    total_spend_usd = Column(Float, default=0.0)
    top_merchants = Column(JSON, nullable=True)  # List of dicts: [{"merchant": name, "amount": total}]
    anomaly_count = Column(Integer, default=0)
    narrative = Column(String, nullable=True)
    risk_level = Column(String, nullable=True)   # low, medium, high

    # Relationship
    job = relationship("Job", back_populates="summary")
