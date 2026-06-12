import json
from io import BytesIO

import pandas as pd
import streamlit as st

from app.comparison.comparator import ComparisonValidationError, compare_collections
from app.config.settings import get_settings
from app.database.mongo_connection import (
    MongoConnectionError,
    get_db1_connection,
    get_db2_connection,
)
from app.database.mongo_service import (
    MongoServiceError,
    get_collection_fields,
    get_documents_for_comparison_values,
    list_collections,
)


st.set_page_config(
    page_title="MongoDB Collection Comparator",
    page_icon="M",
    layout="wide",
)


def load_databases():
    settings = get_settings()
    if not settings.is_complete:
        missing = []
        if not settings.db1_mongo_uri:
            missing.append("DB1_MONGO_URI")
        if not settings.db2_mongo_uri:
            missing.append("DB2_MONGO_URI")
        if not settings.db1_name:
            missing.append("DB1_NAME")
        if not settings.db2_name:
            missing.append("DB2_NAME")
        raise MongoConnectionError(f"Missing environment variables: {', '.join(missing)}")

    return settings, get_db1_connection(settings), get_db2_connection(settings)


def cached_collection_names(_database):
    return list_collections(_database)


def cached_collection_fields(_database, collection_name: str):
    return get_collection_fields(_database, collection_name)


def render_summary(summary):
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Total Compared", summary.total_compared)
    col2.metric("Matched", summary.matched)
    col3.metric("Mismatch", summary.mismatched)
    col4.metric("Match Percentage", f"{summary.match_percentage}%")
    col5.metric("Duplicate Values", summary.duplicate_values)
    col6.metric("Null Values", summary.null_values)


# Maximum number of characters kept for any single cell when rendering a
# preview table in the browser. Mongo documents can contain very large
# embedded arrays/blobs which, once expanded across many rows/columns,
# can exceed Streamlit's default 200MB websocket message size limit
# (see MessageSizeError). Values are only truncated for on-screen display;
# the Excel export always uses the full, untruncated data.
MAX_CELL_LENGTH = 500

# Maximum number of documents shown in an on-screen preview table.
MAX_PREVIEW_ROWS = 200


def build_document_dataframe(documents: list[dict], truncate: bool = False) -> pd.DataFrame:
    dataframe = pd.json_normalize(documents)
    if dataframe.empty:
        return dataframe

    return dataframe.map(lambda value: format_cell_value(value, truncate=truncate))


def format_cell_value(value, truncate: bool = False):
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, default=str, ensure_ascii=False)
    elif isinstance(value, bytes):
        value = f"<binary data: {len(value)} bytes>"

    if truncate and isinstance(value, str) and len(value) > MAX_CELL_LENGTH:
        value = f"{value[:MAX_CELL_LENGTH]}... [truncated, {len(value)} chars total]"

    return value


def render_document_pair(
    title: str,
    db1_documents: list[dict],
    db1_collection: str,
    db2_documents: list[dict],
    db2_collection: str,
) -> None:
    st.subheader(title)

    if not db1_documents and not db2_documents:
        st.info(f"No {title.lower()} found.")
        return

    db1_column, db2_column = st.columns(2)

    with db1_column:
        st.markdown("**DB1 Documents**")
        st.caption(f"{len(db1_documents)} documents from {db1_collection}")
        if db1_documents:
            if len(db1_documents) > MAX_PREVIEW_ROWS:
                st.caption(
                    f"Showing first {MAX_PREVIEW_ROWS} of {len(db1_documents)} documents "
                    "(full data is included in the Excel download)."
                )
            st.dataframe(
                build_document_dataframe(db1_documents[:MAX_PREVIEW_ROWS], truncate=True),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No DB1 documents for this result group.")

    with db2_column:
        st.markdown("**DB2 Documents**")
        st.caption(f"{len(db2_documents)} documents from {db2_collection}")
        if db2_documents:
            if len(db2_documents) > MAX_PREVIEW_ROWS:
                st.caption(
                    f"Showing first {MAX_PREVIEW_ROWS} of {len(db2_documents)} documents "
                    "(full data is included in the Excel download)."
                )
            st.dataframe(
                build_document_dataframe(db2_documents[:MAX_PREVIEW_ROWS], truncate=True),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No DB2 documents for this result group.")


def get_batch_collections(settings, db1_collections: list[str], db2_collections: list[str]):
    if settings.collection_names:
        return list(settings.collection_names)
    return sorted(set(db1_collections) & set(db2_collections))


def get_batch_field_options(db1, db2, collection_names: list[str]) -> list[str]:
    fields: set[str] = set()

    for collection_name in collection_names:
        try:
            db1_fields = cached_collection_fields(db1, collection_name)
            db2_fields = cached_collection_fields(db2, collection_name)
        except MongoServiceError:
            continue
        fields.update(set(db1_fields) & set(db2_fields))

    return sorted(fields)


def compute_collection_result(
    db1,
    db2,
    collection_name: str,
    comparison_field: str,
    include_null_values: bool,
) -> dict:
    """Run the comparison and fetch all documents needed for display.

    This performs all the Mongo queries up front and returns a plain
    dict of data. It does NOT render anything, so the result can be
    cached in ``st.session_state`` and re-rendered on later Streamlit
    reruns (e.g. when a download button is clicked) without hitting the
    database again.
    """
    try:
        result = compare_collections(
            db1=db1,
            db1_collection=collection_name,
            db2=db2,
            db2_collection=collection_name,
            comparison_field=comparison_field,
            include_null_values=include_null_values,
        )
    except ComparisonValidationError as exc:
        return {
            "collection_name": collection_name,
            "error": str(exc),
            "error_type": "no_data",
        }
    except MongoServiceError as exc:
        return {
            "collection_name": collection_name,
            "error": str(exc),
            "error_type": "service",
        }

    matched_values = {
        row.comparison_value for row in result.rows if row.status == "MATCH"
    }
    db1_unmatched_values = {
        row.comparison_value
        for row in result.rows
        if row.status == "MISMATCH" and row.db1_exists
    }
    db2_unmatched_values = {
        row.comparison_value
        for row in result.rows
        if row.status == "MISMATCH" and row.db2_exists
    }

    matched_db1_documents = get_documents_for_comparison_values(
        db1, collection_name, comparison_field, matched_values
    )
    matched_db2_documents = get_documents_for_comparison_values(
        db2, collection_name, comparison_field, matched_values
    )
    unmatched_db1_documents = get_documents_for_comparison_values(
        db1, collection_name, comparison_field, db1_unmatched_values
    )
    unmatched_db2_documents = get_documents_for_comparison_values(
        db2, collection_name, comparison_field, db2_unmatched_values
    )

    return {
        "collection_name": collection_name,
        "error": None,
        "error_type": None,
        "summary": result.summary,
        "display_rows": result.to_display_rows(),
        "matched_db1": matched_db1_documents,
        "matched_db2": matched_db2_documents,
        "unmatched_db1": unmatched_db1_documents,
        "unmatched_db2": unmatched_db2_documents,
    }


def render_collection_result(data: dict) -> None:
    """Render a previously computed collection result. No DB access."""
    st.header(data["collection_name"])

    if data.get("error"):
        if data.get("error_type") == "no_data":
            st.markdown(
                f"**NO DATA AVAILABLE IN COLLECTION '{data['collection_name'].upper()}'.**"
            )
        else:
            st.error(data["error"])
        return

    render_summary(data["summary"])
    comparison_dataframe = pd.DataFrame(data["display_rows"])
    st.subheader("Match / Mismatch Comparison")
    st.dataframe(comparison_dataframe, use_container_width=True, hide_index=True)

    render_document_pair(
        title="Matched Collection Data",
        db1_documents=data["matched_db1"],
        db1_collection=data["collection_name"],
        db2_documents=data["matched_db2"],
        db2_collection=data["collection_name"],
    )
    render_document_pair(
        title="Unmatched Collection Data",
        db1_documents=data["unmatched_db1"],
        db1_collection=data["collection_name"],
        db2_documents=data["unmatched_db2"],
        db2_collection=data["collection_name"],
    )


def get_excel_engine() -> str:
    """Pick an available Excel writer engine.

    ``openpyxl`` is the preferred engine (and is listed in
    requirements.txt), but if the environment's dependencies are out of
    sync (ModuleNotFoundError: No module named 'openpyxl'), fall back to
    ``xlsxwriter`` if it happens to be available, instead of crashing the
    whole page.
    """
    for engine in ("openpyxl", "xlsxwriter"):
        try:
            __import__(engine)
            return engine
        except ImportError:
            continue

    raise RuntimeError(
        "No Excel writer engine is available. Install 'openpyxl' "
        "(pip install -r requirements.txt) to enable Excel downloads."
    )


def build_excel_download(collection_results: list[dict], data_type: str) -> bytes:
    output = BytesIO()
    used_sheet_names: set[str] = set()
    engine = get_excel_engine()

    with pd.ExcelWriter(output, engine=engine) as writer:
        wrote_sheet = False

        for result in collection_results:
            if result.get("error") or result.get("no_data"):
                continue

            collection_name = result["collection_name"]
            db1_key = f"{data_type}_db1"
            db2_key = f"{data_type}_db2"
            rows = []

            for document in result[db1_key]:
                rows.append({"Source DB": "DB1", "Collection": collection_name, **document})

            for document in result[db2_key]:
                rows.append({"Source DB": "DB2", "Collection": collection_name, **document})

            if not rows:
                continue

            dataframe = build_document_dataframe(rows)
            sheet_name = get_excel_sheet_name(collection_name, used_sheet_names)
            dataframe.to_excel(writer, sheet_name=sheet_name, index=False)
            wrote_sheet = True

        if not wrote_sheet:
            pd.DataFrame([{"Message": f"No {data_type} data found."}]).to_excel(
                writer,
                sheet_name=data_type.title(),
                index=False,
            )

    return output.getvalue()


def get_excel_sheet_name(collection_name: str, used_sheet_names: set[str]) -> str:
    invalid_characters = ["\\", "/", "*", "[", "]", ":", "?"]
    sheet_name = collection_name
    for character in invalid_characters:
        sheet_name = sheet_name.replace(character, "_")

    sheet_name = sheet_name[:31] or "Sheet"
    base_name = sheet_name
    counter = 1

    while sheet_name in used_sheet_names:
        suffix = f"_{counter}"
        sheet_name = f"{base_name[:31 - len(suffix)]}{suffix}"
        counter += 1

    used_sheet_names.add(sheet_name)
    return sheet_name


def render_download_buttons(collection_results: list[dict]) -> None:
    try:
        matched_excel = build_excel_download(collection_results, "matched")
        unmatched_excel = build_excel_download(collection_results, "unmatched")
    except RuntimeError as exc:
        st.error(str(exc))
        return

    left, right = st.columns(2)
    with left:
        st.download_button(
            "Download Matched Data Excel",
            data=matched_excel,
            file_name="matched_collection_data.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    with right:
        st.download_button(
            "Download Unmatched Data Excel",
            data=unmatched_excel,
            file_name="unmatched_collection_data.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


st.title("MongoDB Collection Comparator")

try:
    settings, db1, db2 = load_databases()
    db1_collections = cached_collection_names(db1)
    db2_collections = cached_collection_names(db2)
except (MongoConnectionError, MongoServiceError) as exc:
    st.error(str(exc))
    st.stop()

if not db1_collections:
    st.warning("DB1 has no collections.")
    st.stop()

if not db2_collections:
    st.warning("DB2 has no collections.")
    st.stop()

collection_names = get_batch_collections(settings, db1_collections, db2_collections)

if not collection_names:
    st.warning("No collections are configured or shared by both databases.")
    st.stop()

missing_in_db1 = sorted(set(collection_names) - set(db1_collections))
missing_in_db2 = sorted(set(collection_names) - set(db2_collections))

if missing_in_db1:
    st.warning(f"These configured collections are missing in DB1: {', '.join(missing_in_db1)}")
if missing_in_db2:
    st.warning(f"These configured collections are missing in DB2: {', '.join(missing_in_db2)}")

collection_names = [
    collection_name
    for collection_name in collection_names
    if collection_name in db1_collections and collection_name in db2_collections
]

if not collection_names:
    st.warning("None of the configured collections exist in both databases.")
    st.stop()

st.subheader("Batch Comparison Setup")
st.caption(f"{len(collection_names)} collections will be compared from the configured list.")
st.caption("Only documents with active = true and deleted = false are compared.")

available_fields = get_batch_field_options(db1, db2, collection_names)

if not available_fields:
    st.warning("No common fields were found across the configured collections.")
    st.stop()

comparison_field = st.selectbox("Select Comparison Attribute", available_fields)
include_null_values = st.checkbox("Include null and missing field values", value=True)

if st.button("SEARCH", type="primary"):
    collection_results = []
    progress_placeholder = st.empty()
    for index, collection_name in enumerate(collection_names, start=1):
        with progress_placeholder, st.spinner(
            f"Comparing {collection_name} ({index}/{len(collection_names)})..."
        ):
            collection_results.append(
                compute_collection_result(
                    db1=db1,
                    db2=db2,
                    collection_name=collection_name,
                    comparison_field=comparison_field,
                    include_null_values=include_null_values,
                )
            )
    progress_placeholder.empty()

    # Cache the computed results (along with the settings used to produce
    # them) so that later reruns - e.g. triggered by clicking a download
    # button - can re-render the page from cache instead of re-querying
    # MongoDB and instead of losing the results entirely.
    st.session_state["collection_results"] = collection_results
    st.session_state["collection_results_field"] = comparison_field
    st.session_state["collection_results_include_nulls"] = include_null_values

collection_results = st.session_state.get("collection_results")

if collection_results:
    if (
        st.session_state.get("collection_results_field") != comparison_field
        or st.session_state.get("collection_results_include_nulls") != include_null_values
    ):
        st.info(
            "Showing results for the previous selection. Click SEARCH again "
            "to refresh for the current selection."
        )

    for collection_result in collection_results:
        st.divider()
        render_collection_result(collection_result)

    st.divider()
    st.subheader("Excel Downloads")
    render_download_buttons(collection_results)