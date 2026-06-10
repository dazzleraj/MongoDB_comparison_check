from collections import Counter
from typing import Any

from app.models.comparison_models import ComparisonRow


def build_comparison_rows(
    db1_counts: Counter[Any],
    db2_counts: Counter[Any],
) -> list[ComparisonRow]:
    comparison_values = sorted(
        set(db1_counts.keys()) | set(db2_counts.keys()),
        key=lambda value: str(value),
    )

    rows: list[ComparisonRow] = []
    for value in comparison_values:
        db1_count = db1_counts.get(value, 0)
        db2_count = db2_counts.get(value, 0)
        db1_exists = db1_count > 0
        db2_exists = db2_count > 0
        duplicate_detected = db1_count > 1 or db2_count > 1

        rows.append(
            ComparisonRow(
                comparison_value=value,
                db1_exists=db1_exists,
                db2_exists=db2_exists,
                db1_document_count=db1_count,
                db2_document_count=db2_count,
                status="MATCH" if db1_exists and db2_exists else "MISMATCH",
                duplicate_detected=duplicate_detected,
                null_value=value is None,
            )
        )

    return rows

