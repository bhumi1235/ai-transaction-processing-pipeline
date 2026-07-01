import time
import requests

API_URL = "http://localhost:8001"
CSV_FILE = "sample_transactions.csv"


def verify_flow():
    print(f"1. Checking if API is online and healthy at {API_URL}...")
    try:
        res = requests.get(f"{API_URL}/health")
        print(f"API health response: {res.json()}")
    except Exception as e:
        print(f"API health check failed: {str(e)}")
        return

    print(f"\n2. Uploading {CSV_FILE}...")
    try:
        with open(CSV_FILE, "rb") as f:
            files = {"file": (CSV_FILE, f, "text/csv")}
            res = requests.post(f"{API_URL}/jobs/upload", files=files)
            
        if res.status_code != 202:
            print(f"Upload failed: {res.status_code} - {res.text}")
            return
            
        data = res.json()
        job_id = data["job_id"]
        print(f"Upload successful! job_id: {job_id}")
    except Exception as e:
        print(f"Failed to upload: {str(e)}")
        return

    print(f"\n3. Polling job {job_id} status...")
    max_retries = 150
    delay = 2
    
    for attempt in range(max_retries):
        try:
            res = requests.get(f"{API_URL}/jobs/{job_id}/status")
            if res.status_code != 200:
                print(f"Status poll failed: {res.status_code} - {res.text}")
                break
                
            status_data = res.json()
            status = status_data["status"]
            print(f"Attempt {attempt+1}: Status = {status}")
            
            if status in ("COMPLETED", "FAILED"):
                if status == "COMPLETED" and status_data.get("summary"):
                    print(f"Job Status Summary Stats: {status_data['summary']}")
                break
        except Exception as e:
            print(f"Failed status poll: {str(e)}")
            
        time.sleep(delay)
    else:
        print("Polling timed out.")
        return

    if status == "FAILED":
        print(f"Job failed! Error: {status_data.get('error_message')}")
        return

    print("\n4. Job completed successfully! Fetching final results...")
    try:
        res = requests.get(f"{API_URL}/jobs/{job_id}/results")
        if res.status_code != 200:
            print(f"Failed to fetch results: {res.status_code} - {res.text}")
            return
            
        results = res.json()
        
        # Color codes
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        RED = "\033[91m"
        CYAN = "\033[96m"
        BOLD = "\033[1m"
        RESET = "\033[0m"

        llm_sum = results.get("llm_summary") or {}
        risk_raw = llm_sum.get("risk_level", "low").upper()
        if "HIGH" in risk_raw:
            risk_color = RED
        elif "MEDIUM" in risk_raw:
            risk_color = YELLOW
        else:
            risk_color = GREEN
            
        # Summary Box
        total_inr = llm_sum.get('total_spend_inr', 0.0)
        total_usd = llm_sum.get('total_spend_usd', 0.0)
        inr_str = f"INR {total_inr:,.2f}"
        usd_str = f"USD {total_usd:,.2f}"
        
        print(f"\n{BOLD}{CYAN}+--------------------------------------------------------+")
        print(f"|                    SUMMARY METRICS                     |")
        print(f"+--------------------------+-----------------------------+")
        print(f"| Raw Rows                 | {results['row_count_raw']:<27} |")
        print(f"| Cleaned Rows             | {results['row_count_clean']:<27} |")
        print(f"| Total INR Spend          | {inr_str:<27} |")
        print(f"| Total USD Spend          | {usd_str:<27} |")
        print(f"| Risk Assessment          | {risk_color}{risk_raw:<27}{RESET}{BOLD}{CYAN} |")
        print(f"+--------------------------+-----------------------------+{RESET}")
        
        # Narrative Box
        narrative = llm_sum.get('narrative', 'N/A')
        print(f"\n{BOLD}{CYAN}+--------------------------------------------------------+")
        print(f"|                   FINANCIAL NARRATIVE                  |")
        print(f"+--------------------------------------------------------+")
        
        # Simple word wrapping for narrative
        words = narrative.split()
        current_line = []
        current_len = 0
        for word in words:
            if current_len + len(word) + 1 > 52:
                line_str = " ".join(current_line)
                print(f"| {line_str:<52} |")
                current_line = [word]
                current_len = len(word)
            else:
                current_line.append(word)
                current_len += len(word) + 1
        if current_line:
            line_str = " ".join(current_line)
            print(f"| {line_str:<52} |")
        print(f"+--------------------------------------------------------+{RESET}")
        
        # Anomalies Table
        print(f"\n{BOLD}{RED}+----------------------------------------------------------------------------------------+")
        print(f"|                                   FLAGGED ANOMALIES                                    |")
        print(f"+------------------+--------------+------------------------------------------------------+")
        print(f"| MERCHANT         | AMOUNT       | ANOMALY REASON                                       |")
        print(f"+------------------+--------------+------------------------------------------------------+")
        
        anomalies = results.get("flagged_anomalies", [])
        if not anomalies:
            print(f"| {GREEN}{'No anomalies detected.':<86}{RESET}{BOLD}{RED} |")
        else:
            for item in anomalies:
                amt_str = f"{item['currency']} {item['amount']:,.2f}"
                merchant = item['merchant'][:16]
                reason = item['anomaly_reason']
                
                # Wrap reason text if too long (max 52 chars per line in cell)
                reason_words = reason.split()
                reason_lines = []
                curr_line = []
                curr_len = 0
                for w in reason_words:
                    if curr_len + len(w) + 1 > 52:
                        reason_lines.append(" ".join(curr_line))
                        curr_line = [w]
                        curr_len = len(w)
                    else:
                        curr_line.append(w)
                        curr_len += len(w) + 1
                if curr_line:
                    reason_lines.append(" ".join(curr_line))
                
                # Print first line of anomaly
                print(f"| {merchant:<16} | {amt_str:<12} | {reason_lines[0]:<52} |")
                # Print subsequent wrapped reason lines
                for extra_line in reason_lines[1:]:
                    print(f"| {'':<16} | {'':<12} | {extra_line:<52} |")
                    
        print(f"+----------------------------------------------------------------------------------------+{RESET}")
        
        # Category Breakdown Table
        print(f"\n{BOLD}{GREEN}+----------------------+----------------------+----------------------+")
        print(f"|                         CATEGORY BREAKDOWN                         |")
        print(f"+----------------------+----------------------+----------------------+")
        print(f"| CATEGORY             | INR SPEND            | USD SPEND            |")
        print(f"+----------------------+----------------------+----------------------+")
        
        breakdown = results.get("spend_breakdown_by_category", {})
        for cat, spends in breakdown.items():
            inr_val = f"INR {spends.get('INR', 0.0):,.2f}"
            usd_val = f"USD {spends.get('USD', 0.0):,.2f}"
            print(f"| {cat:<20} | {inr_val:<20} | {usd_val:<20} |")
            
        print(f"+----------------------+----------------------+----------------------+{RESET}")

    except Exception as e:
        print(f"Failed to show results: {str(e)}")


if __name__ == "__main__":
    verify_flow()
