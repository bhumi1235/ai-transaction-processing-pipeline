from typing import Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas
from app.tasks.pipeline import process_transaction_csv

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.post("/upload", response_model=Dict[str, str], status_code=202)
async def upload_transactions(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Accepts a CSV file upload, initializes a pending job in the database,
    and enqueues the processing pipeline asynchronously.
    """
    # Simple file type check
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=400,
            detail="Invalid file format. Only CSV files are allowed."
        )

    try:
        # Read the file content
        content_bytes = await file.read()
        content_str = content_bytes.decode("utf-8")
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not parse file: {str(e)}"
        )

    # Create job entry
    job = models.Job(
        filename=file.filename,
        status="PENDING"
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Enqueue background task
    process_transaction_csv.delay(job.id, content_str)

    return {
        "job_id": job.id,
        "status": "pending",
        "message": "File uploaded and queued for processing"
    }


@router.get("/{job_id}/status", response_model=schemas.JobStatusResponse)
def get_job_status(job_id: str, db: Session = Depends(get_db)):
    """
    Returns the current status of a job. If the job is completed,
    it also returns a high-level summary.
    """
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    summary_data = None
    if job.status == "COMPLETED" and job.summary:
        # Calculate distinct categories count in database for this job
        from sqlalchemy import func
        distinct_categories = db.query(func.count(models.Transaction.category.distinct()))\
            .filter(models.Transaction.job_id == job_id)\
            .scalar() or 0

        summary_data = schemas.JobStatusSummaryResponse(
            row_count=job.row_count_clean,
            anomalies=job.summary.anomaly_count,
            categories=distinct_categories,
            total_spend_inr=job.summary.total_spend_inr,
            total_spend_usd=job.summary.total_spend_usd
        )

    return schemas.JobStatusResponse(
        job_id=job.id,
        status=job.status,
        filename=job.filename,
        row_count_raw=job.row_count_raw,
        row_count_clean=job.row_count_clean,
        created_at=job.created_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        summary=summary_data
    )


@router.get("/{job_id}/results", response_model=schemas.JobResultsResponse)
def get_job_results(job_id: str, db: Session = Depends(get_db)):
    """
    Returns the full structured output of the job, including cleaned transactions,
    flagged anomalies, spend breakdown by category, and the LLM-generated narrative summary.
    """
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "COMPLETED":
        raise HTTPException(
            status_code=400,
            detail=f"Job is in '{job.status}' status. Results are only available for completed jobs."
        )

    # Fetch transactions
    transactions = db.query(models.Transaction).filter(models.Transaction.job_id == job_id).all()
    
    # Split into cleaned and anomalies
    cleaned_txns = []
    anomalies = []
    
    # Spend breakdown structure: {"CategoryName": {"INR": total_inr, "USD": total_usd}}
    breakdown: Dict[str, Dict[str, float]] = {}
    
    for t in transactions:
        txn_resp = schemas.TransactionResponse.model_validate(t)
        cleaned_txns.append(txn_resp)
        if t.is_anomaly:
            anomalies.append(txn_resp)
            
        # Calculate breakdown
        cat = t.category or "Uncategorised"
        curr = t.currency or "INR"
        amount = t.amount or 0.0
        
        if cat not in breakdown:
            breakdown[cat] = {"INR": 0.0, "USD": 0.0}
        if curr not in breakdown[cat]:
            breakdown[cat][curr] = 0.0
            
        breakdown[cat][curr] += amount

    # Round breakdown values to 2 decimal places to prevent floating-point artifacts
    for cat in breakdown:
        for curr in list(breakdown[cat].keys()):
            breakdown[cat][curr] = round(breakdown[cat][curr], 2)

    # Fetch summary
    summary_data = None
    if job.summary:
        summary_data = schemas.JobSummaryResponse(
            total_spend_inr=job.summary.total_spend_inr,
            total_spend_usd=job.summary.total_spend_usd,
            top_merchants=job.summary.top_merchants,
            anomaly_count=job.summary.anomaly_count,
            narrative=job.summary.narrative,
            risk_level=job.summary.risk_level
        )

    return schemas.JobResultsResponse(
        job_id=job.id,
        filename=job.filename,
        status=job.status,
        row_count_raw=job.row_count_raw,
        row_count_clean=job.row_count_clean,
        cleaned_transactions=cleaned_txns,
        flagged_anomalies=anomalies,
        spend_breakdown_by_category=breakdown,
        llm_summary=summary_data
    )


@router.get("", response_model=schemas.JobListResponse)
def list_jobs(
    status: Optional[str] = Query(None, description="Filter jobs by status"),
    db: Session = Depends(get_db)
):
    """
    Lists all jobs, with optional filtering by status (pending, processing, completed, failed).
    """
    query = db.query(models.Job)
    if status:
        query = query.filter(models.Job.status == status.upper())
        
    jobs = query.order_by(models.Job.created_at.desc()).all()
    
    job_items = [
        schemas.JobListItem(
            id=j.id,
            filename=j.filename,
            status=j.status,
            row_count_raw=j.row_count_raw,
            created_at=j.created_at
        )
        for j in jobs
    ]
    
    return schemas.JobListResponse(jobs=job_items)
