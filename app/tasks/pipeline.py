import io
import json
import time
import logging
from datetime import datetime
from typing import List, Dict, Any

import pandas as pd
import google.generativeai as genai
from celery import shared_task

from app.config import settings
from app.database import SessionLocal
from app.models import Job, Transaction, JobSummary

logger = logging.getLogger(__name__)


def parse_date(date_str: Any) -> str:
    """Normalizes mixed date formats (DD-MM-YYYY, YYYY/MM/DD) to ISO 8601 (YYYY-MM-DD)."""
    if pd.isna(date_str) or not isinstance(date_str, str) or not date_str.strip():
        return ""
    date_str = date_str.strip()
    
    # Try common formats
    for fmt in ("%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
            
    # Fallback to pandas parsing
    try:
        return pd.to_datetime(date_str).strftime("%Y-%m-%d")
    except Exception:
        return str(date_str)


def clean_amount(val: Any) -> float:
    """Strips currency symbols and converts amount to float, rounded to 2 decimal places."""
    if pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    
    val_str = str(val).strip()
    # Remove currency symbols and common spacing
    for symbol in ('$', 'INR', 'inr', 'USD', 'usd', 'Rs', 'rs', 'Rs.', 'rs.'):
        val_str = val_str.replace(symbol, '')
    val_str = val_str.replace(',', '').strip()
    
    try:
        return round(float(val_str), 2)
    except ValueError:
        return 0.0


def call_gemini_with_retry(prompt: str, retries: int = 3, backoff: float = 2.0) -> Dict[str, Any]:
    """Calls the Gemini API with exponential backoff on failure."""
    if not settings.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is empty. Skipping LLM call and returning empty dictionary.")
        raise ValueError("GEMINI_API_KEY is not configured")
        
    genai.configure(api_key=settings.GEMINI_API_KEY)
    
    # Use gemini-flash-latest
    model = genai.GenerativeModel('gemini-flash-latest')
    
    current_backoff = backoff
    for attempt in range(retries + 1):
        try:
            logger.info(f"Calling Gemini API (attempt {attempt + 1}/{retries + 1})...")
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            # Parse response text as JSON
            result = json.loads(response.text)
            return result
        except Exception as e:
            logger.error(f"Gemini API call failed on attempt {attempt + 1}: {str(e)}")
            if attempt == retries:
                raise e
            logger.info(f"Retrying in {current_backoff} seconds...")
            time.sleep(current_backoff)
            current_backoff *= 2
            
    return {}


@shared_task(name="app.tasks.pipeline.process_transaction_csv")
def process_transaction_csv(job_id: str, csv_bytes_str: str) -> str:
    """Asynchronously cleans, processes, analyzes anomalies, and enriches transactions using Gemini."""
    db = SessionLocal()
    
    # Retrieve job record
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        logger.error(f"Job with ID {job_id} not found.")
        db.close()
        return f"Job {job_id} not found"
        
    try:
        job.status = "PROCESSING"
        db.commit()
        
        # Load CSV into Pandas
        df = pd.read_csv(io.StringIO(csv_bytes_str))
        
        # Strip column whitespace and convert to lowercase for uniform mapping
        df.columns = [c.strip().lower() for c in df.columns]
        job.row_count_raw = len(df)
        db.commit()
        
        # Remove exact duplicate rows
        df = df.drop_duplicates()
        row_count_clean = len(df)
        job.row_count_clean = row_count_clean
        db.commit()
        
        # Apply standard cleaning functions
        # Map back to expected schema columns (with fallback if missing)
        headers = df.columns.tolist()
        
        def get_col_val(row, possible_names: List[str], default_val: Any = "") -> Any:
            for name in possible_names:
                if name in headers:
                    val = row[name]
                    return val if not pd.isna(val) else default_val
            return default_val

        # Create structured cleaned records list
        records = []
        for idx, row in df.iterrows():
            txn_id = str(get_col_val(row, ['txn_id', 'txnid', 'id'], ""))
            date_raw = get_col_val(row, ['date', 'datetime', 'timestamp'], "")
            merchant = str(get_col_val(row, ['merchant', 'vendor', 'payee'], "")).strip()
            amount_raw = get_col_val(row, ['amount', 'value', 'price'], 0.0)
            currency = str(get_col_val(row, ['currency', 'curr'], "INR")).strip().upper()
            status = str(get_col_val(row, ['status', 'state'], "PENDING")).strip().upper()
            category = str(get_col_val(row, ['category', 'type', 'class'], "Uncategorised")).strip()
            account_id = str(get_col_val(row, ['account_id', 'account', 'acc_id'], "")).strip()
            notes = str(get_col_val(row, ['notes', 'note', 'description'], "")).strip()

            if not category or category.lower() in ('nan', 'none', 'null', ''):
                category = "Uncategorised"

            records.append({
                "temp_id": f"row_{idx}",
                "txn_id": txn_id,
                "date": parse_date(date_raw),
                "merchant": merchant,
                "amount": clean_amount(amount_raw),
                "currency": currency,
                "status": status,
                "category": category,
                "account_id": account_id,
                "notes": notes,
                "is_anomaly": False,
                "anomaly_reason": "",
                "llm_category_raw_response": None,
                "llm_failed": False
            })

        # Convert back to temp DataFrame for easy median and anomaly calculation
        temp_df = pd.DataFrame(records)
        
        # Statistical Anomaly Check: Amount > 3x account median
        account_medians = temp_df.groupby('account_id')['amount'].median().to_dict()
        
        for record in records:
            reasons = []
            
            # Anomaly 1: Statistical Outlier
            acc_id = record["account_id"]
            median = account_medians.get(acc_id, 0.0)
            if median > 0 and record["amount"] > 3 * median:
                record["is_anomaly"] = True
                reasons.append(f"Statistical outlier: amount ({record['amount']}) exceeds 3x account median ({median:.2f})")
            
            # Anomaly 2: USD for domestic brand (Swiggy, Ola, IRCTC)
            merchant_lower = record["merchant"].lower()
            is_domestic_brand = any(brand in merchant_lower for brand in ("swiggy", "ola", "irctc"))
            if record["currency"] == "USD" and is_domestic_brand:
                record["is_anomaly"] = True
                reasons.append(f"Domestic brand ({record['merchant']}) transacted in USD")
                
            if reasons:
                record["anomaly_reason"] = " | ".join(reasons)

        # Batched LLM Classification
        uncategorized_records = [r for r in records if r["category"] == "Uncategorised"]
        batch_size = 20
        
        for i in range(0, len(uncategorized_records), batch_size):
            batch = uncategorized_records[i:i + batch_size]
            
            # Create a simple JSON-compatible list for LLM prompt
            prompt_items = [
                {
                    "temp_id": item["temp_id"],
                    "merchant": item["merchant"],
                    "amount": item["amount"],
                    "currency": item["currency"],
                    "notes": item["notes"]
                }
                for item in batch
            ]
            
            prompt = (
                "You are an expert transaction categorization model.\n"
                "Given a list of transactions, classify each into exactly one of the following categories:\n"
                "Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, Other.\n\n"
                "Return ONLY a JSON object where the keys are the `temp_id` values from the input and "
                "the values are the classified category names. Do not include markdown code block syntax (like ```json).\n\n"
                f"Transactions to classify:\n{json.dumps(prompt_items, indent=2)}"
            )
            
            try:
                # Call Gemini with retry
                classification_map = call_gemini_with_retry(prompt, retries=3, backoff=2.0)
                
                # Apply classifications to original records
                for item in batch:
                    temp_id = item["temp_id"]
                    assigned_category = classification_map.get(temp_id, "Other")
                    
                    # Validate categorized output matches valid categories
                    valid_categories = {"Food", "Shopping", "Travel", "Transport", "Utilities", "Cash Withdrawal", "Entertainment", "Other"}
                    if assigned_category not in valid_categories:
                        assigned_category = "Other"
                        
                    item["category"] = assigned_category
                    item["llm_category_raw_response"] = json.dumps(classification_map)
                    
            except Exception as e:
                logger.error(f"Failed to classify batch starting at index {i} after all retries: {str(e)}")
                # Mark as llm_failed but continue job
                for item in batch:
                    item["llm_failed"] = True
                    item["llm_category_raw_response"] = f"Error: {str(e)}"

        # Save Cleaned and Categorized Transactions to Database
        db_transactions = []
        for r in records:
            txn = Transaction(
                job_id=job_id,
                txn_id=r["txn_id"] if r["txn_id"] else None,
                date=r["date"],
                merchant=r["merchant"],
                amount=r["amount"],
                currency=r["currency"],
                status=r["status"],
                category=r["category"],
                account_id=r["account_id"],
                is_anomaly=r["is_anomaly"],
                anomaly_reason=r["anomaly_reason"] if r["is_anomaly"] else None,
                llm_category_raw_response=r["llm_category_raw_response"],
                llm_failed=r["llm_failed"]
            )
            db.add(txn)
            db_transactions.append(txn)
            
        db.commit() # Commit transactions so we can compute final stats from DB or local records

        # Compile Narrative Summary Statistics
        # We compute these based on local records to be fast
        total_spend_inr = round(sum(r["amount"] for r in records if r["currency"] == "INR"), 2)
        total_spend_usd = round(sum(r["amount"] for r in records if r["currency"] == "USD"), 2)
        anomaly_count = sum(1 for r in records if r["is_anomaly"])
        
        # Calculate top merchants
        merchant_spend = {}
        for r in records:
            m = r["merchant"]
            if m:
                merchant_spend[m] = merchant_spend.get(m, 0.0) + r["amount"]
                
        # Sort merchants by spend descending and take top 3
        top_merchants_sorted = sorted(merchant_spend.items(), key=lambda x: x[1], reverse=True)[:3]
        top_merchants_json = [{"merchant": name, "total_spend": round(amt, 2)} for name, amt in top_merchants_sorted]

        # Call LLM for Narrative Summary
        summary_prompt = (
            "You are a senior financial analyst.\n"
            "Review these high-level transaction stats and generate a structured JSON report.\n\n"
            f"Stats:\n"
            f"- Total Row Count: {row_count_clean}\n"
            f"- Total INR Spend: {total_spend_inr:.2f}\n"
            f"- Total USD Spend: {total_spend_usd:.2f}\n"
            f"- Anomaly Count: {anomaly_count}\n"
            f"- Top Merchants: {json.dumps(top_merchants_json)}\n\n"
            "Produce a JSON object with the following keys:\n"
            "- 'narrative': A concise 2-3 sentence explanation of the spending behavior, highlights, and any concerns.\n"
            "- 'risk_level': Classify overall spending risk as either 'low', 'medium', or 'high'.\n"
            "Return ONLY the raw JSON object, without any markdown formatting.\n"
        )
        
        narrative = "No summary generated."
        risk_level = "low"
        
        try:
            summary_res = call_gemini_with_retry(summary_prompt, retries=3, backoff=2.0)
            narrative = summary_res.get("narrative", "Summary narrative missing.")
            risk_level = summary_res.get("risk_level", "low").lower()
            if risk_level not in ("low", "medium", "high"):
                risk_level = "low"
        except Exception as e:
            logger.error(f"Failed to generate narrative summary after all retries: {str(e)}")
            narrative = f"Narrative generation failed. Error: {str(e)}"
            risk_level = "medium"

        # Create JobSummary Record
        job_summary = JobSummary(
            job_id=job_id,
            total_spend_inr=total_spend_inr,
            total_spend_usd=total_spend_usd,
            top_merchants=top_merchants_json,
            anomaly_count=anomaly_count,
            narrative=narrative,
            risk_level=risk_level
        )
        db.add(job_summary)
        
        # Complete the job
        job.status = "COMPLETED"
        job.completed_at = datetime.utcnow()
        db.commit()
        logger.info(f"Job {job_id} successfully completed.")
        
    except Exception as e:
        logger.exception(f"Error processing job {job_id}")
        db.rollback()
        job.status = "FAILED"
        job.completed_at = datetime.utcnow()
        job.error_message = str(e)
        db.commit()
        
    finally:
        db.close()
        
    return f"Job {job_id} processing complete"
