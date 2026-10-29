import sqlite3

db_path = "sample_data/backfill_100k.db"
connection = sqlite3.connect(db_path)

tables = [
    "target_events",
    "rejected_records",
    "backfill_runs",
    "backfill_partitions"
]

print("\n100,000-RECORD BACKFILL VERIFICATION")
print("=" * 45)

for table in tables:
    count = connection.execute(
        f'SELECT COUNT(*) FROM "{table}"'
    ).fetchone()[0]
    print(f"{table}: {count}")

print("\nPARTITION STATUS SUMMARY")
print("=" * 45)

rows = connection.execute(
    "SELECT status, COUNT(*) "
    "FROM backfill_partitions GROUP BY status"
).fetchall()

for status, count in rows:
    print(f"{status}: {count}")

connection.close()
