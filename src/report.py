"""Print reconciliation and operational summary."""
import argparse
import json
from src.storage import connect

def build_report(db_path: str) -> dict:
    with connect(db_path) as conn:
        run_rows = conn.execute("""
            SELECT run_id,status,start_date,end_date,input_count,output_count,rejected_count,
                   started_at,ended_at,error_message
            FROM backfill_runs ORDER BY started_at DESC
        """).fetchall()
        target_count = conn.execute("SELECT COUNT(*) FROM target_events").fetchone()[0]
        target_by_date = conn.execute("""
            SELECT event_date,COUNT(*) AS row_count,ROUND(SUM(amount),2) AS total_amount
            FROM target_events GROUP BY event_date ORDER BY event_date
        """).fetchall()
        reject_by_reason = conn.execute("""
            SELECT reject_reason,COUNT(*) AS row_count
            FROM rejected_records GROUP BY reject_reason ORDER BY row_count DESC
        """).fetchall()
        partition_rows = conn.execute("""
            SELECT run_id,partition_date,status,input_count,output_count,rejected_count,error_message
            FROM backfill_partitions ORDER BY run_id,partition_date
        """).fetchall()
    return {
        "runs": [dict(r) for r in run_rows],
        "target_total_rows": target_count,
        "target_by_date": [dict(r) for r in target_by_date],
        "rejected_by_reason": [dict(r) for r in reject_by_reason],
        "partitions": [dict(r) for r in partition_rows],
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="outputs/backfill.db")
    args = parser.parse_args()
    report = build_report(args.db)
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
