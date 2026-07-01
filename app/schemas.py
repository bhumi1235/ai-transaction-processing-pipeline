from pydantic import BaseModel
from typing import List, Dict, Optional, Any
from datetime import datetime


class TransactionResponse(BaseModel):
    id: str
    txn_id: Optional[str] = None
    date: Optional[str] = None
    merchant: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None
    account_id: Optional[str] = None
    is_anomaly: bool
    anomaly_reason: Optional[str] = None
    llm_failed: bool

    class Config:
        from_attributes = True


class JobSummaryResponse(BaseModel):
    total_spend_inr: float
    total_spend_usd: float
    top_merchants: Optional[List[Dict[str, Any]]] = None
    anomaly_count: int
    narrative: Optional[str] = None
    risk_level: Optional[str] = None

    class Config:
        from_attributes = True


class JobStatusSummaryResponse(BaseModel):
    row_count: int
    anomalies: int
    categories: int
    total_spend_inr: float
    total_spend_usd: float


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    filename: str
    row_count_raw: int
    row_count_clean: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    summary: Optional[JobStatusSummaryResponse] = None


class JobResultsResponse(BaseModel):
    job_id: str
    filename: str
    status: str
    row_count_raw: int
    row_count_clean: int
    cleaned_transactions: List[TransactionResponse]
    flagged_anomalies: List[TransactionResponse]
    spend_breakdown_by_category: Dict[str, Dict[str, float]]
    llm_summary: Optional[JobSummaryResponse] = None


class JobListItem(BaseModel):
    id: str
    filename: str
    status: str
    row_count_raw: int
    created_at: datetime

    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    jobs: List[JobListItem]
