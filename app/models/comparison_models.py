from dataclasses import asdict, dataclass
from typing import Any, Literal


ComparisonStatus = Literal["MATCH", "MISMATCH"]


@dataclass(frozen=True)
class ComparisonRow:
    comparison_value: Any
    db1_exists: bool
    db2_exists: bool
    db1_document_count: int
    db2_document_count: int
    status: ComparisonStatus
    duplicate_detected: bool = False
    null_value: bool = False

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "Comparison Value": self._format_value(self.comparison_value),
            "DB1 Exists": "Exists" if self.db1_exists else "Missing",
            "DB2 Exists": "Exists" if self.db2_exists else "Missing",
            "DB1 Document Count": self.db1_document_count,
            "DB2 Document Count": self.db2_document_count,
            "Status": self.status,
            "Duplicate Value": "Yes" if self.duplicate_detected else "No",
            "Null Value": "Yes" if self.null_value else "No",
        }

    @staticmethod
    def _format_value(value: Any) -> str:
        if value is None:
            return "<NULL>"
        return str(value)


@dataclass(frozen=True)
class ComparisonSummary:
    total_compared: int
    matched: int
    mismatched: int
    match_percentage: float
    duplicate_values: int
    null_values: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComparisonResult:
    rows: list[ComparisonRow]
    summary: ComparisonSummary

    def to_display_rows(self) -> list[dict[str, Any]]:
        return [row.to_display_dict() for row in self.rows]

