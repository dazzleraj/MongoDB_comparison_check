from pymongo.database import Database

from app.comparison.matcher import build_comparison_rows
from app.database.mongo_service import (
    collection_has_documents,
    collection_has_field,
    count_field_values,
)
from app.models.comparison_models import ComparisonResult, ComparisonSummary


class ComparisonValidationError(ValueError):
    pass


class NoDataError(ComparisonValidationError):
    """Raised when a collection has no documents matching the active filter.

    This is treated as an informational case (not a real error) by the UI.
    """


def compare_collections(
    db1: Database,
    db1_collection: str,
    db2: Database,
    db2_collection: str,
    comparison_field: str,
    include_null_values: bool = True,
) -> ComparisonResult:
    if not comparison_field:
        raise ComparisonValidationError("Select a comparison field.")

    db1_has_data = collection_has_documents(db1, db1_collection)
    db2_has_data = collection_has_documents(db2, db2_collection)

    if not db1_has_data and not db2_has_data:
        raise NoDataError(
            f"No data exists in collection '{db1_collection}' "
            f"(with active = true and deleted = false) for the selected comparison attribute."
        )

    if not db1_has_data:
        raise NoDataError(
            f"No data exists in DB1 collection '{db1_collection}' "
            f"(with active = true and deleted = false) for the selected comparison attribute."
        )

    if not db2_has_data:
        raise NoDataError(
            f"No data exists in DB2 collection '{db2_collection}' "
            f"(with active = true and deleted = false) for the selected comparison attribute."
        )

    if not collection_has_field(db1, db1_collection, comparison_field):
        raise ComparisonValidationError(
            f"Field '{comparison_field}' does not exist in DB1 collection '{db1_collection}'."
        )

    if not collection_has_field(db2, db2_collection, comparison_field):
        raise ComparisonValidationError(
            f"Field '{comparison_field}' does not exist in DB2 collection '{db2_collection}'."
        )

    db1_counts = count_field_values(
        db1,
        db1_collection,
        comparison_field,
        include_null_values=include_null_values,
    )
    db2_counts = count_field_values(
        db2,
        db2_collection,
        comparison_field,
        include_null_values=include_null_values,
    )

    rows = build_comparison_rows(db1_counts, db2_counts)
    matched = sum(1 for row in rows if row.status == "MATCH")
    mismatched = len(rows) - matched
    duplicate_values = sum(1 for row in rows if row.duplicate_detected)
    null_values = sum(1 for row in rows if row.null_value)
    match_percentage = round((matched / len(rows)) * 100, 2) if rows else 0.0

    return ComparisonResult(
        rows=rows,
        summary=ComparisonSummary(
            total_compared=len(rows),
            matched=matched,
            mismatched=mismatched,
            match_percentage=match_percentage,
            duplicate_values=duplicate_values,
            null_values=null_values,
        ),
    )