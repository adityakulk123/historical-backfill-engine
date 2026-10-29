"""Controlled historical backfill CLI."""

import argparse
import csv
import json
import logging
import time
import uuid

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from src.storage import connect, initialize
from src.validation import raw_json, validate_record, validate_schema


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

LOGGER = logging.getLogger("backfill")


def read_source(input_path: str) -> tuple[list[dict], list[str]]:
    """Read and validate the input CSV schema."""

    with open(
        input_path,
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as handle:

        reader = csv.DictReader(handle)

        if reader.fieldnames is None:
            raise ValueError("Input CSV is empty or has no header")

        validate_schema(reader.fieldnames)

        rows = []

        for row_number, row in enumerate(reader, start=2):
            row["_source_row"] = row_number
            rows.append(row)

        return rows, reader.fieldnames


def iso_date(value: str, label: str) -> str:
    """Validate and normalize a date."""

    try:
        parsed = date.fromisoformat(value)

        if parsed.isoformat() != value:
            raise ValueError("Date must use YYYY-MM-DD format")

        return parsed.isoformat()

    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{label} must be YYYY-MM-DD"
        ) from exc


def run_backfill(
    input_path: str,
    db_path: str,
    start_date: str,
    end_date: str,
    workers: int = 2,
    dry_run: bool = False,
    run_id: str | None = None,
) -> dict:
    """Run a controlled historical backfill."""

    start_date = iso_date(start_date, "start-date")
    end_date = iso_date(end_date, "end-date")

    if start_date > end_date:
        raise ValueError(
            "start-date must be on or before end-date"
        )

    if workers < 1 or workers > 16:
        raise ValueError("workers must be between 1 and 16")

    source_path = str(Path(input_path).resolve())

    rows, _ = read_source(source_path)

    # Group valid records by date and malformed dates separately.
    by_partition = defaultdict(list)
    invalid_date_count = 0

    for row in rows:

        raw_date = str(row.get("event_date", "")).strip()

        try:
            row_date = date.fromisoformat(raw_date).isoformat()

        except ValueError:
            # Invalid dates cannot be assigned to normal date partitions.
            # A sentinel partition allows these records to be quarantined.
            by_partition["INVALID_DATE"].append(row)
            invalid_date_count += 1
            continue

        if start_date <= row_date <= end_date:
            row["event_date"] = row_date
            by_partition[row_date].append(row)

    partitions = sorted(by_partition)

    estimated_records = sum(
        len(partition_rows)
        for partition_rows in by_partition.values()
    )

    estimate = {
        "input_path": source_path,
        "start_date": start_date,
        "end_date": end_date,
        "partition_count": len(partitions),
        "estimated_records": estimated_records,
        "invalid_date_records": invalid_date_count,
        "records_by_partition": {
            partition: len(by_partition[partition])
            for partition in partitions
        },
        "workers": workers,
        "dry_run": dry_run,
    }

    # Dry runs must not initialize or modify the database.
    if dry_run:
        print(json.dumps(estimate, indent=2))
        return estimate

    initialize(db_path)

    run_id = run_id or str(uuid.uuid4())

    # Register the run and its partitions.
    with connect(db_path) as conn:

        existing = conn.execute(
            "SELECT * FROM backfill_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

        if existing is None:

            conn.execute(
                """
                INSERT INTO backfill_runs
                    (run_id, input_path, start_date, end_date, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    source_path,
                    start_date,
                    end_date,
                    "RUNNING",
                ),
            )

        else:

            existing_config = (
                existing["input_path"],
                existing["start_date"],
                existing["end_date"],
            )

            requested_config = (
                source_path,
                start_date,
                end_date,
            )

            if existing_config != requested_config:
                raise ValueError(
                    "The supplied run_id already exists with "
                    "different input or date range"
                )

            conn.execute(
                """
                UPDATE backfill_runs
                SET status = 'RUNNING',
                    ended_at = NULL,
                    error_message = NULL
                WHERE run_id = ?
                """,
                (run_id,),
            )

        for partition in partitions:

            conn.execute(
                """
                INSERT OR IGNORE INTO backfill_partitions
                    (run_id, partition_date, status)
                VALUES (?, ?, ?)
                """,
                (run_id, partition, "PENDING"),
            )

    known_customers = {
        f"CUST-{i:05d}"
        for i in range(1, 501)
    }

    t0 = time.perf_counter()

    def process_partition(partition: str) -> dict:
        """Process one partition in its own database connection."""

        partition_rows = by_partition[partition]

        conn = connect(db_path)

        inserted = 0
        rejected = 0

        try:

            status_row = conn.execute(
                """
                SELECT status
                FROM backfill_partitions
                WHERE run_id = ? AND partition_date = ?
                """,
                (run_id, partition),
            ).fetchone()

            # Completed partitions are not processed again.
            if status_row and status_row["status"] == "COMPLETED":

                return {
                    "partition": partition,
                    "status": "SKIPPED",
                    "input": 0,
                    "output": 0,
                    "rejected": 0,
                }

            conn.execute(
                """
                UPDATE backfill_partitions
                SET status = 'RUNNING',
                    started_at = CURRENT_TIMESTAMP,
                    ended_at = NULL,
                    error_message = NULL
                WHERE run_id = ? AND partition_date = ?
                """,
                (run_id, partition),
            )

            conn.commit()

            # Target writes, rejected records, and completion status
            # are committed together.
            conn.execute("BEGIN IMMEDIATE")

            seen_ids = set()

            for row in partition_rows:

                clean = {
                    key: value
                    for key, value in row.items()
                    if key != "_source_row"
                }

                source_row = int(row["_source_row"])

                reasons = validate_record(
                    clean,
                    known_customers,
                )

                event_id = str(clean.get("event_id") or "")

                if event_id and event_id in seen_ids:
                    reasons.append("duplicate_event_id_in_input")

                elif event_id:
                    seen_ids.add(event_id)

                # Quarantine invalid records.
                if reasons:

                    reason = ";".join(sorted(set(reasons)))

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO rejected_records
                        (
                            run_id,
                            partition_date,
                            source_file,
                            source_row,
                            event_id,
                            raw_record,
                            reject_reason
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            partition,
                            str(
                                clean.get("source_file")
                                or source_path
                            ),
                            source_row,
                            event_id or None,
                            raw_json(clean),
                            reason,
                        ),
                    )

                    rejected += 1
                    continue

                # Check whether the event already exists.
                existing_target = conn.execute(
                    """
                    SELECT event_date, customer_id, event_type, amount
                    FROM target_events
                    WHERE event_id = ?
                    """,
                    (event_id,),
                ).fetchone()

                values = (
                    event_id,
                    clean["event_date"],
                    clean["customer_id"],
                    clean["event_type"],
                    float(clean["amount"]),
                    clean["source_file"],
                    source_row,
                )

                if existing_target is None:

                    conn.execute(
                        """
                        INSERT INTO target_events
                        (
                            event_id,
                            event_date,
                            customer_id,
                            event_type,
                            amount,
                            source_file,
                            source_row
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )

                    inserted += 1

                else:

                    expected = (
                        clean["event_date"],
                        clean["customer_id"],
                        clean["event_type"],
                        float(clean["amount"]),
                    )

                    actual = tuple(existing_target)

                    # Do not silently accept conflicting event IDs.
                    if actual != expected:

                        reason = (
                            "event_id_conflicts_with_existing_target"
                        )

                        conn.execute(
                            """
                            INSERT OR IGNORE INTO rejected_records
                            (
                                run_id,
                                partition_date,
                                source_file,
                                source_row,
                                event_id,
                                raw_record,
                                reject_reason
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run_id,
                                partition,
                                clean["source_file"],
                                source_row,
                                event_id,
                                raw_json(clean),
                                reason,
                            ),
                        )

                        rejected += 1

                # Identical existing records are treated as already processed.

            conn.execute(
                """
                UPDATE backfill_partitions
                SET status = 'COMPLETED',
                    input_count = ?,
                    output_count = ?,
                    rejected_count = ?,
                    ended_at = CURRENT_TIMESTAMP,
                    error_message = NULL
                WHERE run_id = ? AND partition_date = ?
                """,
                (
                    len(partition_rows),
                    inserted,
                    rejected,
                    run_id,
                    partition,
                ),
            )

            conn.commit()

            return {
                "partition": partition,
                "status": "COMPLETED",
                "input": len(partition_rows),
                "output": inserted,
                "rejected": rejected,
            }

        except Exception as exc:

            conn.rollback()

            conn.execute(
                """
                UPDATE backfill_partitions
                SET status = 'FAILED',
                    ended_at = CURRENT_TIMESTAMP,
                    error_message = ?
                WHERE run_id = ? AND partition_date = ?
                """,
                (str(exc), run_id, partition),
            )

            conn.commit()

            LOGGER.exception(
                "Partition %s failed",
                partition,
            )

            return {
                "partition": partition,
                "status": "FAILED",
                "input": len(partition_rows),
                "output": 0,
                "rejected": 0,
                "error": str(exc),
            }

        finally:
            conn.close()

    # Execute partitions with bounded parallelism.
    results = []

    with ThreadPoolExecutor(max_workers=workers) as executor:

        futures = {
            executor.submit(process_partition, partition): partition
            for partition in partitions
        }

        for future in as_completed(futures):

            result = future.result()
            results.append(result)

            LOGGER.info(
                "Partition result: %s",
                result,
            )

    # Calculate metrics for this execution only.
    current_input = sum(
        result.get("input", 0)
        for result in results
    )

    current_output = sum(
        result.get("output", 0)
        for result in results
    )

    current_rejected = sum(
        result.get("rejected", 0)
        for result in results
    )

    # Calculate cumulative metrics for this run ID.
    with connect(db_path) as conn:

        totals = conn.execute(
            """
            SELECT
                COALESCE(SUM(input_count), 0),
                COALESCE(SUM(output_count), 0),
                COALESCE(SUM(rejected_count), 0)
            FROM backfill_partitions
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

        failed = conn.execute(
            """
            SELECT COUNT(*)
            FROM backfill_partitions
            WHERE run_id = ? AND status = 'FAILED'
            """,
            (run_id,),
        ).fetchone()[0]

        status = "FAILED" if failed else "COMPLETED"

        conn.execute(
            """
            UPDATE backfill_runs
            SET status = ?,
                ended_at = CURRENT_TIMESTAMP,
                input_count = ?,
                output_count = ?,
                rejected_count = ?
            WHERE run_id = ?
            """,
            (
                status,
                totals[0],
                totals[1],
                totals[2],
                run_id,
            ),
        )

    elapsed = time.perf_counter() - t0

    report = {
        "run_id": run_id,
        "status": status,
        "partitions": len(partitions),

        # Backward-compatible metrics.
        "input_count_for_run": totals[0],
        "new_target_rows_this_run": current_output,
        "rejected_count_for_run": totals[2],

        # Metrics for the current invocation.
        "current_execution_input_count": current_input,
        "current_execution_new_target_rows": current_output,
        "current_execution_rejected_count": current_rejected,

        # Cumulative metrics for this run ID.
        "cumulative_input_count": totals[0],
        "cumulative_target_rows_inserted": totals[1],
        "cumulative_rejected_count": totals[2],

        "failed_partitions": failed,
        "elapsed_seconds": round(elapsed, 3),

        "throughput_input_records_per_second": (
            round(current_input / elapsed, 2)
            if elapsed
            else 0
        ),

        "database": db_path,

        "partition_results": sorted(
            results,
            key=lambda result: result["partition"],
        ),
    }

    Path("outputs").mkdir(exist_ok=True)

    Path("outputs/last_run_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(report, indent=2))

    return report


def main():
    """Command-line entry point."""

    parser = argparse.ArgumentParser(
        description="Controlled historical partition backfill"
    )

    parser.add_argument(
        "--input",
        default="sample_data/events.csv",
    )

    parser.add_argument(
        "--db",
        default="outputs/backfill.db",
    )

    parser.add_argument(
        "--start-date",
        default="2026-01-01",
    )

    parser.add_argument(
        "--end-date",
        default="2026-01-30",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=3,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--run-id",
        help="Reuse a run ID to safely resume incomplete partitions",
    )

    args = parser.parse_args()

    run_backfill(
        args.input,
        args.db,
        args.start_date,
        args.end_date,
        args.workers,
        args.dry_run,
        args.run_id,
    )


if __name__ == "__main__":
    main()