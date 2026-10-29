"""Schema and record-level validation."""
import json
from datetime import date

REQUIRED_COLUMNS = {
    "event_id", "event_date", "customer_id", "event_type", "amount", "source_file"
}
ALLOWED_EVENT_TYPES = {"PURCHASE", "REFUND", "PAYMENT"}

def validate_schema(columns):
    missing = REQUIRED_COLUMNS - set(columns)
    unexpected = set(columns) - REQUIRED_COLUMNS
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing columns: {sorted(missing)}")
        if unexpected:
            details.append(f"unexpected columns: {sorted(unexpected)}")
        raise ValueError("Unexpected CSV schema (" + "; ".join(details) + ")")

def validate_record(record: dict, known_customers: set[str]) -> list[str]:
    reasons = []
    for field in ("event_id", "event_date", "customer_id", "event_type", "amount", "source_file"):
        value = record.get(field)
        if value is None or str(value).strip() == "":
            reasons.append(f"missing_required_field:{field}")
    if record.get("event_date") and str(record["event_date"]).strip():
        try:
            date.fromisoformat(str(record["event_date"]))
        except ValueError:
            reasons.append("invalid_event_date")
    if record.get("event_type") and record["event_type"] not in ALLOWED_EVENT_TYPES:
        reasons.append("invalid_event_type")
    if record.get("amount") is not None and str(record["amount"]).strip():
        try:
            amount = float(record["amount"])
            if amount < 0:
                reasons.append("negative_amount")
        except (ValueError, TypeError):
            reasons.append("invalid_amount")
    customer = str(record.get("customer_id") or "").strip()
    if customer and customer not in known_customers:
        reasons.append("invalid_customer_reference")
    return reasons

def raw_json(record: dict) -> str:
    return json.dumps(record, sort_keys=True, default=str)
