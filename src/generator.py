"""Create reproducible synthetic event CSV files."""
import argparse
import random
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

EVENT_TYPES = ["PURCHASE", "REFUND", "PAYMENT"]

def generate_events(output_path: str, record_count: int = 1000,
                    seed: int = 42, start_date: str = "2026-01-01") -> pd.DataFrame:
    if record_count < 0:
        raise ValueError("record_count cannot be negative")
    rng = random.Random(seed)
    first_date = date.fromisoformat(start_date)
    rows = []
    for i in range(1, record_count + 1):
        event_day = first_date + timedelta(days=rng.randint(0, 29))
        rows.append({
            "event_id": f"EVT-{i:08d}",
            "event_date": event_day.isoformat(),
            "customer_id": f"CUST-{rng.randint(1, 500):05d}",
            "event_type": rng.choice(EVENT_TYPES),
            "amount": round(rng.uniform(10, 5000), 2),
            "source_file": Path(output_path).name,
        })
    df = pd.DataFrame(rows, columns=[
        "event_id", "event_date", "customer_id", "event_type", "amount", "source_file"
    ])
    # Deterministic bad records used to prove validation and quarantine.
    if record_count >= 10:
        df.loc[0, "customer_id"] = ""
        df.loc[1, "amount"] = -100.00
        df.loc[2, "event_id"] = df.loc[3, "event_id"]
        df.loc[4, "event_type"] = "UNKNOWN"
        df["amount"] = df["amount"].astype(str)
        df.loc[5, "amount" ] = "not-a-number"
        df.loc[6, "customer_id"] = "CUST-99999"  # invalid reference
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df

def main():
    parser = argparse.ArgumentParser(description="Generate synthetic event data")
    parser.add_argument("--output", default="sample_data/events.csv")
    parser.add_argument("--records", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-date", default="2026-01-01")
    args = parser.parse_args()
    df = generate_events(args.output, args.records, args.seed, args.start_date)
    print(f"Generated {len(df)} records -> {args.output}")

if __name__ == "__main__":
    main()
