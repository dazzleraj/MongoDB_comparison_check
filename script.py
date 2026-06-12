"""
MongoDB Collection Comparator – headless script
================================================
Reads credentials and settings from a .env file in the same directory,
compares collections across two MongoDB databases, and writes two Excel
workbooks next to this script file:

    matched_YYYYMMDD_HHMMSS.xlsx    — matched documents per collection
    unmatched_YYYYMMDD_HHMMSS.xlsx  — unmatched documents per collection

Required .env keys
------------------
DB1_MONGO_URI            connection string for the first database
DB2_MONGO_URI            connection string for the second database
DB1_NAME                 database name on DB1
DB2_NAME                 database name on DB2
COMPARISON_FIELD         document field to compare on (e.g. "name", "_id")

Optional .env keys
------------------
COLLECTION_NAMES         comma-separated list of collections to compare.
                         When absent, all collections present in both
                         databases are compared.
INCLUDE_NULL_VALUES      true / false  (default: true)
MONGO_SERVER_SELECTION_TIMEOUT_MS   (default: 5000)
"""

import json
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from bson import json_util
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ConfigurationError, ConnectionFailure, InvalidURI, PyMongoError

# ---------------------------------------------------------------------------
# Load .env from the same directory as this script
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(_SCRIPT_DIR / ".env",override = True)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _parse_collection_names(raw: str) -> tuple[str, ...]:
    return tuple(
        name.strip().strip("'\"")
        for name in raw.replace("\n", ",").split(",")
        if name.strip().strip("'\"")
    )


@dataclass(frozen=True)
class Settings:
    db1_mongo_uri: str
    db2_mongo_uri: str
    db1_name: str
    db2_name: str
    comparison_field: str
    collection_names: tuple[str, ...]
    include_null_values: bool
    server_selection_timeout_ms: int

    @property
    def is_complete(self) -> bool:
        return all([
            self.db1_mongo_uri,
            self.db2_mongo_uri,
            self.db1_name,
            self.db2_name,
            self.comparison_field,
        ])


def load_settings() -> Settings:
    return Settings(
        db1_mongo_uri=os.getenv("DB1_MONGO_URI", "").strip(),
        db2_mongo_uri=os.getenv("DB2_MONGO_URI", "").strip(),
        db1_name=os.getenv("DB1_NAME", "").strip(),
        db2_name=os.getenv("DB2_NAME", "").strip(),
        comparison_field=os.getenv("COMPARISON_FIELD", "").strip(),
        collection_names=_parse_collection_names(os.getenv("COLLECTION_NAMES", "")),
        include_null_values=os.getenv("INCLUDE_NULL_VALUES", "true").strip().lower() != "false",
        server_selection_timeout_ms=int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")),
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
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
            "Comparison Value": "<NULL>" if self.comparison_value is None else str(self.comparison_value),
            "DB1 Exists": "Exists" if self.db1_exists else "Missing",
            "DB2 Exists": "Exists" if self.db2_exists else "Missing",
            "DB1 Document Count": self.db1_document_count,
            "DB2 Document Count": self.db2_document_count,
            "Status": self.status,
            "Duplicate Value": "Yes" if self.duplicate_detected else "No",
            "Null Value": "Yes" if self.null_value else "No",
        }


@dataclass(frozen=True)
class ComparisonSummary:
    total_compared: int
    matched: int
    mismatched: int
    match_percentage: float
    duplicate_values: int
    null_values: int


@dataclass(frozen=True)
class ComparisonResult:
    rows: list[ComparisonRow]
    summary: ComparisonSummary


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
ACTIVE_FILTER = {"active": True, "deleted": False}


def _build_client(uri: str, timeout_ms: int) -> MongoClient:
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        client.admin.command("ping")
        return client
    except (ConfigurationError, ConnectionFailure, InvalidURI, PyMongoError) as exc:
        raise RuntimeError(f"MongoDB connection failed: {exc}") from exc


def _normalize(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_normalize(item) for item in value)
    if isinstance(value, dict):
        return tuple((k, _normalize(v)) for k, v in sorted(value.items()))
    return value


def _to_json_safe(document: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json_util.dumps(document))


def list_collections(db: Database) -> list[str]:
    try:
        return sorted(db.list_collection_names())
    except PyMongoError as exc:
        raise RuntimeError(f"Unable to list collections: {exc}") from exc


def collection_has_field(db: Database, collection: str, field_name: str) -> bool:
    try:
        projection = {"_id": 1} if field_name == "_id" else {field_name: 1, "_id": 0}
        return db[collection].find_one(
            {**ACTIVE_FILTER, field_name: {"$exists": True}}, projection
        ) is not None
    except PyMongoError as exc:
        raise RuntimeError(f"Field check failed on '{collection}': {exc}") from exc


def count_field_values(
    db: Database,
    collection: str,
    field_name: str,
    include_null_values: bool,
) -> Counter[Any]:
    projection = {"_id": 1} if field_name == "_id" else {field_name: 1, "_id": 0}
    values: Counter[Any] = Counter()
    try:
        for doc in db[collection].find(ACTIVE_FILTER, projection):
            value = doc.get(field_name)
            if value is None and not include_null_values:
                continue
            values[_normalize(value)] += 1
    except PyMongoError as exc:
        raise RuntimeError(f"Unable to count values in '{collection}': {exc}") from exc
    return values


def get_documents_for_values(
    db: Database,
    collection: str,
    field_name: str,
    target_values: set[Any],
) -> list[dict[str, Any]]:
    if not target_values:
        return []
    docs: list[dict[str, Any]] = []
    try:
        for doc in db[collection].find(ACTIVE_FILTER):
            if _normalize(doc.get(field_name)) in target_values:
                docs.append(_to_json_safe(doc))
    except PyMongoError as exc:
        raise RuntimeError(f"Unable to fetch documents from '{collection}': {exc}") from exc
    return docs


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------
def _build_rows(db1_counts: Counter, db2_counts: Counter) -> list[ComparisonRow]:
    all_values = sorted(set(db1_counts) | set(db2_counts), key=lambda v: str(v))
    rows = []
    for value in all_values:
        c1 = db1_counts.get(value, 0)
        c2 = db2_counts.get(value, 0)
        rows.append(ComparisonRow(
            comparison_value=value,
            db1_exists=c1 > 0,
            db2_exists=c2 > 0,
            db1_document_count=c1,
            db2_document_count=c2,
            status="MATCH" if c1 > 0 and c2 > 0 else "MISMATCH",
            duplicate_detected=c1 > 1 or c2 > 1,
            null_value=value is None,
        ))
    return rows


def compare_collection(
    db1: Database,
    db2: Database,
    collection_name: str,
    comparison_field: str,
    include_null_values: bool,
) -> ComparisonResult:
    db1_counts = count_field_values(db1, collection_name, comparison_field, include_null_values)
    db2_counts = count_field_values(db2, collection_name, comparison_field, include_null_values)
    rows = _build_rows(db1_counts, db2_counts)
    matched = sum(1 for r in rows if r.status == "MATCH")
    mismatched = len(rows) - matched
    return ComparisonResult(
        rows=rows,
        summary=ComparisonSummary(
            total_compared=len(rows),
            matched=matched,
            mismatched=mismatched,
            match_percentage=round(matched / len(rows) * 100, 2) if rows else 0.0,
            duplicate_values=sum(1 for r in rows if r.duplicate_detected),
            null_values=sum(1 for r in rows if r.null_value),
        ),
    )


# ---------------------------------------------------------------------------
# Excel helpers
# ---------------------------------------------------------------------------
def _sanitize(value: Any) -> Any:
    """
    Convert any value to a type that openpyxl can safely write.

    This is the critical step that prevents corrupt Excel files.
    All MongoDB-specific types (ObjectId, datetime, Decimal128, Binary, etc.)
    are guaranteed to be stringified by json_util before reaching here,
    but nested dicts/lists still need flattening, and None must stay None
    (Excel blank cell) rather than the string "None".
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN / Inf are not valid Excel cell values
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return f"<binary: {len(value)} bytes>"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    # Catch-all: datetime, Decimal, ObjectId remnants, etc.
    return str(value)


def _sanitize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: _sanitize(v) for k, v in doc.items()}


def _safe_sheet_name(name: str, used: set[str]) -> str:
    for ch in r'\/*[]:?':
        name = name.replace(ch, "_")
    name = name[:31] or "Sheet"
    base = name
    counter = 1
    while name in used:
        suffix = f"_{counter}"
        name = f"{base[:31 - len(suffix)]}{suffix}"
        counter += 1
    used.add(name)
    return name


def _col_width(series: pd.Series, header: str) -> float:
    """
    Compute a safe column width.

    BUG FIXED: pandas .max() returns NaN for all-None columns.
    NaN passed to openpyxl column_dimensions.width writes width=""
    in the XML which is invalid per the OOXML spec and causes Excel
    to reject the entire file with "file format or extension not valid".
    We clamp any non-finite result to the header length as a fallback.
    """
    try:
        max_data_len = series.astype(str).str.len().max()
        # isnan() also catches NaN from all-None / all-NaT columns
        if not isinstance(max_data_len, (int, float)) or math.isnan(max_data_len):
            max_data_len = 0
        return min(max(float(max_data_len), len(str(header))) + 4, 60)
    except Exception:
        return min(len(str(header)) + 4, 60)


def _write_sheet(writer: pd.ExcelWriter, df: pd.DataFrame, sheet_name: str) -> None:
    """Write a DataFrame to a named sheet and auto-fit column widths safely."""
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    ws = writer.sheets[sheet_name]
    for col_idx, col in enumerate(df.columns, start=1):
        letter = ws.cell(row=1, column=col_idx).column_letter
        ws.column_dimensions[letter].width = _col_width(df[col], col)


def _write_excel(path: Path, sheets: list[tuple[str, pd.DataFrame]]) -> None:
    """
    Write multiple (sheet_name, DataFrame) pairs to a single workbook.
    Validates the resulting bytes before flushing to disk so a crash
    inside the writer never produces a silently corrupt file.
    """
    from io import BytesIO
    import zipfile

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet_name, df in sheets:
            _write_sheet(writer, df, sheet_name)

    data = buf.getvalue()

    # Integrity check: a valid xlsx is a ZIP archive starting with "PK"
    if len(data) < 4 or data[:2] != b"PK":
        raise RuntimeError(
            f"Excel writer produced an invalid file ({len(data)} bytes). "
            "Check for unsupported value types in the data."
        )

    # Verify the ZIP is well-formed (catches truncated writes)
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = zf.namelist()
            if "xl/workbook.xml" not in names:
                raise RuntimeError("Workbook XML missing from archive.")
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"Excel archive is corrupt: {exc}") from exc

    path.write_bytes(data)


# ---------------------------------------------------------------------------
# Per-collection sheet builders
# ---------------------------------------------------------------------------
def _build_collection_sheets(
    collection_results: list[dict],
    doc_key_db1: str,
    doc_key_db2: str,
    empty_message: str,
) -> list[tuple[str, pd.DataFrame]]:
    """
    Build (sheet_name, DataFrame) pairs for one output file.

    Each collection gets its own sheet containing documents from both
    DB1 and DB2 combined, with a leading "Source DB" column.
    Collections with no data for this category are skipped.
    """
    sheets: list[tuple[str, pd.DataFrame]] = []
    used: set[str] = set()

    has_any_data = False
    for result in collection_results:
        if result.get("error"):
            continue
        col_name = result["collection_name"]
        db1_docs = result.get(doc_key_db1, [])
        db2_docs = result.get(doc_key_db2, [])
        if not db1_docs and not db2_docs:
            continue

        rows = []
        for doc in db1_docs:
            rows.append({"Source DB": "DB1", "Collection": col_name, **_sanitize_doc(doc)})
        for doc in db2_docs:
            rows.append({"Source DB": "DB2", "Collection": col_name, **_sanitize_doc(doc)})

        sheet_name = _safe_sheet_name(col_name, used)
        sheets.append((sheet_name, pd.DataFrame(rows)))
        has_any_data = True

    if not has_any_data:
        sheets.append(("No Data", pd.DataFrame([{"Message": empty_message}])))

    return sheets


# ---------------------------------------------------------------------------
# Summary sheet builder
# ---------------------------------------------------------------------------
def _build_summary_df(summary_rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(summary_rows, columns=[
        "Collection", "Status", "Error",
        "Total Compared", "Matched", "Mismatched", "Match %",
        "Duplicates", "Null Values",
    ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("MongoDB Collection Comparator")
    print("=" * 50)

    # --- Load & validate settings ---
    settings = load_settings()

    missing = []
    for attr in ("db1_mongo_uri", "db2_mongo_uri", "db1_name", "db2_name", "comparison_field"):
        if not getattr(settings, attr):
            missing.append(attr.upper())
    if missing:
        print(f"\n[ERROR] Missing required .env variables: {', '.join(missing)}")
        sys.exit(1)

    print(f"\nDB1             : {settings.db1_name}")
    print(f"DB2             : {settings.db2_name}")
    print(f"Comparison field: {settings.comparison_field}")
    print(f"Include nulls   : {settings.include_null_values}")

    # --- Connect ---
    print("\nConnecting to databases...")
    try:
        db1 = _build_client(settings.db1_mongo_uri, settings.server_selection_timeout_ms)[settings.db1_name]
        print("  DB1 connected ✓")
    except RuntimeError as exc:
        print(f"  [ERROR] DB1: {exc}")
        sys.exit(1)

    try:
        db2 = _build_client(settings.db2_mongo_uri, settings.server_selection_timeout_ms)[settings.db2_name]
        print("  DB2 connected ✓")
    except RuntimeError as exc:
        print(f"  [ERROR] DB2: {exc}")
        sys.exit(1)

    # --- Resolve collection list ---
    db1_cols = set(list_collections(db1))
    db2_cols = set(list_collections(db2))

    if settings.collection_names:
        collection_names = list(settings.collection_names)
        missing_db1 = sorted(set(collection_names) - db1_cols)
        missing_db2 = sorted(set(collection_names) - db2_cols)
        if missing_db1:
            print(f"\n[WARN] Missing in DB1: {', '.join(missing_db1)}")
        if missing_db2:
            print(f"\n[WARN] Missing in DB2: {', '.join(missing_db2)}")
        collection_names = [c for c in collection_names if c in db1_cols and c in db2_cols]
    else:
        collection_names = sorted(db1_cols & db2_cols)

    if not collection_names:
        print("\n[ERROR] No collections to compare.")
        sys.exit(1)

    print(f"\n{len(collection_names)} collection(s) to compare.\n")

    # --- Run comparisons ---
    summary_rows: list[dict] = []
    collection_results: list[dict] = []

    total = len(collection_names)
    for idx, col_name in enumerate(collection_names, start=1):
        print(f"  [{idx:>3}/{total}] {col_name}", end=" ... ", flush=True)

        # Field existence check
        try:
            has_db1 = collection_has_field(db1, col_name, settings.comparison_field)
            has_db2 = collection_has_field(db2, col_name, settings.comparison_field)
        except RuntimeError as exc:
            print(f"SKIP  (field check error: {exc})")
            summary_rows.append(_error_row(col_name, "ERROR", str(exc)))
            continue

        if not has_db1 or not has_db2:
            missing_in = ", ".join(db for db, ok in [("DB1", has_db1), ("DB2", has_db2)] if not ok)
            msg = f"field '{settings.comparison_field}' not found in {missing_in}"
            print(f"SKIP  ({msg})")
            summary_rows.append(_error_row(col_name, "SKIPPED", msg))
            continue

        # Compare
        try:
            result = compare_collection(db1, db2, col_name, settings.comparison_field, settings.include_null_values)
        except RuntimeError as exc:
            print(f"ERROR ({exc})")
            summary_rows.append(_error_row(col_name, "ERROR", str(exc)))
            continue

        s = result.summary
        print(f"done  |  total={s.total_compared}  matched={s.matched}  mismatched={s.mismatched}  match%={s.match_percentage}%")

        summary_rows.append({
            "Collection": col_name, "Status": "OK", "Error": "",
            "Total Compared": s.total_compared, "Matched": s.matched,
            "Mismatched": s.mismatched, "Match %": s.match_percentage,
            "Duplicates": s.duplicate_values, "Null Values": s.null_values,
        })

        # Fetch full documents
        matched_values      = {r.comparison_value for r in result.rows if r.status == "MATCH"}
        db1_unmatched_vals  = {r.comparison_value for r in result.rows if r.status == "MISMATCH" and r.db1_exists}
        db2_unmatched_vals  = {r.comparison_value for r in result.rows if r.status == "MISMATCH" and r.db2_exists}

        collection_results.append({
            "collection_name": col_name,
            "error": None,
            "matched_db1":   get_documents_for_values(db1, col_name, settings.comparison_field, matched_values),
            "matched_db2":   get_documents_for_values(db2, col_name, settings.comparison_field, matched_values),
            "unmatched_db1": get_documents_for_values(db1, col_name, settings.comparison_field, db1_unmatched_vals),
            "unmatched_db2": get_documents_for_values(db2, col_name, settings.comparison_field, db2_unmatched_vals),
        })

    # --- Build and write Excel files ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    matched_path   = _SCRIPT_DIR / f"matched_{timestamp}.xlsx"
    unmatched_path = _SCRIPT_DIR / f"unmatched_{timestamp}.xlsx"

    print(f"\nWriting output files...")

    summary_df = _build_summary_df(summary_rows)

    # matched_TIMESTAMP.xlsx
    matched_sheets: list[tuple[str, pd.DataFrame]] = [("Summary", summary_df)]
    matched_sheets += _build_collection_sheets(
        collection_results, "matched_db1", "matched_db2",
        "No matched documents found across all collections."
    )
    print(f"  Building {matched_path.name}  ({len(matched_sheets)} sheets)...")
    _write_excel(matched_path, matched_sheets)
    print(f"  Saved → {matched_path.name}  ✓")

    # unmatched_TIMESTAMP.xlsx
    unmatched_sheets: list[tuple[str, pd.DataFrame]] = [("Summary", summary_df)]
    unmatched_sheets += _build_collection_sheets(
        collection_results, "unmatched_db1", "unmatched_db2",
        "No unmatched documents found across all collections."
    )
    print(f"  Building {unmatched_path.name}  ({len(unmatched_sheets)} sheets)...")
    _write_excel(unmatched_path, unmatched_sheets)
    print(f"  Saved → {unmatched_path.name}  ✓")

    print(f"\nDone.")
    print(f"  {matched_path}")
    print(f"  {unmatched_path}")


def _error_row(collection_name: str, status: str, error: str) -> dict:
    return {
        "Collection": collection_name, "Status": status, "Error": error,
        "Total Compared": "", "Matched": "", "Mismatched": "",
        "Match %": "", "Duplicates": "", "Null Values": "",
    }


if __name__ == "__main__":
    main()