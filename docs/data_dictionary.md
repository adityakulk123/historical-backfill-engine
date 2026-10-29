# Data dictionary

| Field | Type | Rule |
|---|---|---|
| event_id | string | Required; stable unique event identifier |
| event_date | ISO date string | Required; inclusive date partition key |
| customer_id | string | Required; must exist in `customers` reference table |
| event_type | string | `PURCHASE`, `REFUND`, or `PAYMENT` |
| amount | decimal/float | Required; non-negative in this demonstration |
| source_file | string | Required; source filename/label |
| source_row | integer | Source CSV line number, retained for traceability |

The generator creates synthetic reference customers `CUST-00001` through `CUST-00500`.
