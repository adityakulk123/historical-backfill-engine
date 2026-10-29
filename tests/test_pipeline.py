import csv
from pathlib import Path
import pytest
from src.generator import generate_events
from src.backfill import run_backfill
from src.storage import connect, initialize
from src.validation import validate_schema, validate_record

def test_generator_is_reproducible(tmp_path):
    a = generate_events(str(tmp_path / "a.csv"), 20, 42)
    b = generate_events(str(tmp_path / "b.csv"), 20, 42)
    # source_file differs by output filename, compare all other fields.
    assert a.drop(columns=["source_file"]).equals(b.drop(columns=["source_file"]))

def test_schema_rejects_unexpected_columns():
    with pytest.raises(ValueError):
        validate_schema(["event_id", "unexpected"])

def test_validation_detects_malformed_date():
    record = {
        "event_id": "EVT-1", "event_date": "not-a-date",
        "customer_id": "CUST-00001", "event_type": "PURCHASE",
        "amount": "10.00", "source_file": "x.csv"
    }
    reasons = validate_record(record, {"CUST-00001"})
    assert "invalid_event_date" in reasons

def test_validation_detects_invalid_customer():
    record = {
        "event_id": "EVT-1", "event_date": "2026-01-01",
        "customer_id": "CUST-99999", "event_type": "PURCHASE",
        "amount": "10.00", "source_file": "x.csv"
    }
    reasons = validate_record(record, {"CUST-00001"})
    assert "invalid_customer_reference" in reasons

def test_backfill_idempotent_and_quarantines(tmp_path):
    source = tmp_path / "events.csv"
    db = tmp_path / "test.db"
    generate_events(str(source), 100, 42)
    first = run_backfill(str(source), str(db), "2026-01-01", "2026-01-30", 3, False, "test-run-1")
    assert first["status"] == "COMPLETED"
    with connect(str(db)) as conn:
        before = conn.execute("SELECT COUNT(*) FROM target_events").fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM rejected_records").fetchone()[0]
    assert rejected > 0
    # Reuse same run: completed partitions skipped, no extra target rows.
    second = run_backfill(str(source), str(db), "2026-01-01", "2026-01-30", 3, False, "test-run-1")
    with connect(str(db)) as conn:
        after = conn.execute("SELECT COUNT(*) FROM target_events").fetchone()[0]
    assert second["status"] == "COMPLETED"
    assert before == after

def test_dry_run_does_not_create_database(tmp_path):
    source = tmp_path / "events.csv"
    db = tmp_path / "no-write.db"
    generate_events(str(source), 20, 42)
    result = run_backfill(str(source), str(db), "2026-01-01", "2026-01-30", 2, True)
    assert result["dry_run"] is True
    assert not db.exists()

def test_empty_input_has_clear_failure(tmp_path):
    source = tmp_path / "empty.csv"
    source.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty|header"):
        run_backfill(str(source), str(tmp_path / "empty.db"), "2026-01-01", "2026-01-30")

def test_invalid_date_is_quarantined(tmp_path):
    source = tmp_path / "events.csv"
    db = tmp_path / "test.db"

    with source.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "event_id",
                "event_date",
                "customer_id",
                "event_type",
                "amount",
                "source_file",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "event_id": "EVT-INVALID",
            "event_date": "not-a-date",
            "customer_id": "CUST-00001",
            "event_type": "PURCHASE",
            "amount": "100.00",
            "source_file": "events.csv",
        })

    run_backfill(
        str(source),
        str(db),
        "2026-01-01",
        "2026-01-30",
        1,
        False,
        "invalid-date-test",
    )

    with connect(str(db)) as conn:
        rejected = conn.execute(
            """
            SELECT reject_reason
            FROM rejected_records
            WHERE run_id = ?
            """,
            ("invalid-date-test",),
        ).fetchall()

    assert len(rejected) == 1
    assert "invalid_event_date" in rejected[0]["reject_reason"]

def test_restart_report_separates_new_and_cumulative_counts(tmp_path):
    source = tmp_path / "events.csv"
    db = tmp_path / "test.db"

    generate_events(str(source), 100, 42)

    first = run_backfill(
        str(source),
        str(db),
        "2026-01-01",
        "2026-01-30",
        1,
        False,
        "restart-report-test",
    )

    second = run_backfill(
        str(source),
        str(db),
        "2026-01-01",
        "2026-01-30",
        1,
        False,
        "restart-report-test",
    )

    # These assertions will pass after the reporting fix.
    assert second["new_target_rows_this_run"] == 0
    assert second["input_count_for_run"] == first["input_count_for_run"]
