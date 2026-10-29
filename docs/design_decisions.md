# Design decisions and trade-offs

1. **Python + SQLite:** minimal local setup and reproducible review. SQLite serializes writes, so worker count is bounded and writes are transactional.
2. **CSV source:** easy to inspect and regenerate. Source rows are preserved as JSON in quarantine with file and row references.
3. **Stable event key:** `event_id` is the idempotency key and target primary key.
4. **Partition-level transaction:** target writes, rejects, and `COMPLETED` state commit together, avoiding a completed status without its writes.
5. **Run IDs:** an explicit reused run ID resumes incomplete partitions; completed partitions are skipped.
6. **Dry run:** validates the CSV schema and counts records by date but does not initialize or write the database.
7. **Validation:** required values, date format, event type, amount, and customer reference are checked.
8. **Limitations:** one CSV source, synthetic reference customers, single-node SQLite, no distributed orchestration, and simple retry policy. Production deployment would need a durable orchestrator, richer schema evolution handling, access controls, and alerting.
