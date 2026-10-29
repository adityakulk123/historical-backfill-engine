# Historical Backfill Engine

A local, reproducible data-engineering demo for controlled historical backfills.

## Features
- Date-partition selection and dry-run estimates (dry runs do not write to SQLite).
- Configurable bounded parallelism.
- CSV schema and business-rule validation with a quarantine table.
- Idempotent target writes using a stable `event_id` primary key.
- Per-run and per-partition status/count tracking.
- Safe retries: completed partitions in a reused run ID are skipped; failed/incomplete partitions can be retried.
- Reproducible synthetic data, tests, reconciliation report, and throughput measurement.

## Requirements
Python 3.11+ recommended. The project also works with Python 3.14. SQLite is included with Python.

## Setup (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```
If your virtual environment already exists and is active, skip the first two commands.

## Generate sample data
```powershell
python -m src.generator --output sample_data/events.csv --records 1000 --seed 42
```
Generate a larger dataset:
```powershell
python -m src.generator --output sample_data/events_100k.csv --records 100000 --seed 42
```
The generator deliberately injects a few invalid records when the requested record count is at least 10. It creates synthetic customers 1–500; the pipeline creates those reference customers in SQLite.

## Run a dry run
```powershell
python -m src.backfill --input sample_data/events.csv --db outputs/backfill.db --start-date 2026-01-01 --end-date 2026-01-30 --workers 3 --dry-run
```
The date range is inclusive. Dry run reads and estimates records but does not create or modify the target database.

## Execute a backfill
```powershell
python -m src.backfill --input sample_data/events.csv --db outputs/backfill.db --start-date 2026-01-01 --end-date 2026-01-30 --workers 3
```
Use the same `--run-id` to resume a previous run:
```powershell
python -m src.backfill --input sample_data/events.csv --db outputs/backfill.db --start-date 2026-01-01 --end-date 2026-01-30 --workers 3 --run-id YOUR_EXISTING_RUN_ID
```
Completed partitions for that run are skipped. Failed or pending partitions are retried. Without an explicit run ID, each invocation creates a new run; target writes remain idempotent.

## Tests
```powershell
python -m pytest -q
```

## Reconciliation / report
```powershell
python -m src.report --db outputs/backfill.db
```

## Data dictionary
- `event_id`: stable event key; primary key in target.
- `event_date`: ISO-8601 date and partition key (`YYYY-MM-DD`).
- `customer_id`: reference to a synthetic customer.
- `event_type`: `PURCHASE`, `REFUND`, or `PAYMENT`.
- `amount`: non-negative decimal amount in this illustrative domain.
- `source_file`: source file label.
- `source_row`: 1-based CSV data row number used for traceability.

## Assumptions and trade-offs
- Synthetic transaction-like events are used; no real customer data is included.
- For this demonstration, negative amounts are rejected. Real refund models may require different rules.
- SQLite is suitable for a local review demo. Concurrent readers/processors are supported, but SQLite serializes writes; worker count is bounded and each partition commits in a transaction.
- A row is accepted only if required fields, ISO date, event type, amount, and customer reference are valid.
- Duplicate event IDs within the same input batch are rejected after the first occurrence. Replaying a previously published event does not create a duplicate target row.
- Unexpected/missing CSV columns fail the run clearly rather than being silently ignored.
- A partition's target writes, rejects, and completion status are committed together.
- This demo processes date partitions from one CSV source. An API/object-store connector, distributed orchestration, schema evolution policy, and production-grade secret management are out of scope.

## Design notes
- `event_id` is the stable idempotency key.
- A `ThreadPoolExecutor` limits concurrent partition workers.
- Dry-run is deliberately read-only.
- SQLite transactions keep target writes and partition completion atomic.
- `source_file` and `source_row` allow invalid records to be traced back to input.
