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


def render_document_pair(
    title: str,
    db1,
    db1_collection: str,
    db1_values: set,
    db2,
    db2_collection: str,
    db2_values: set,
    comparison_field: str,
):
    st.subheader(title)

    if not db1_values and not db2_values:
        st.info(f"No {title.lower()} found.")
        return

    db1_documents = get_documents_for_comparison_values(
        db1,
        db1_collection,
        comparison_field,
        db1_values,
    )
    db2_documents = get_documents_for_comparison_values(
        db2,
        db2_collection,
        comparison_field,
        db2_values,
    )

    db1_column, db2_column = st.columns(2)

    with db1_column:
        st.markdown("**DB1 Documents**")
        st.caption(f"{len(db1_documents)} documents from {db1_collection}")
        if db1_documents:
            st.dataframe(
                pd.json_normalize(db1_documents),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No DB1 documents for this result group.")

    with db2_column:
        st.markdown("**DB2 Documents**")
        st.caption(f"{len(db2_documents)} documents from {db2_collection}")
        if db2_documents:
            st.dataframe(
                pd.json_normalize(db2_documents),
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


def render_collection_result(
    db1,
    db2,
    collection_name: str,
    comparison_field: str,
    include_null_values: bool,
):
    st.header(collection_name)

    try:
        result = compare_collections(
            db1=db1,
            db1_collection=collection_name,
            db2=db2,
            db2_collection=collection_name,
            comparison_field=comparison_field,
            include_null_values=include_null_values,
        )
    except (ComparisonValidationError, MongoServiceError) as exc:
        st.error(str(exc))
        return

    render_summary(result.summary)
    comparison_dataframe = pd.DataFrame(result.to_display_rows())
    st.subheader("Match / Mismatch Comparison")
    st.dataframe(comparison_dataframe, use_container_width=True, hide_index=True)

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

    render_document_pair(
        title="Matched Collection Data",
        db1=db1,
        db1_collection=collection_name,
        db1_values=matched_values,
        db2=db2,
        db2_collection=collection_name,
        comparison_field=comparison_field,
        db2_values=matched_values,
    )
    render_document_pair(
        title="Unmatched Collection Data",
        db1=db1,
        db1_collection=collection_name,
        db1_values=db1_unmatched_values,
        db2=db2,
        db2_collection=collection_name,
        comparison_field=comparison_field,
        db2_values=db2_unmatched_values,
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

available_fields = get_batch_field_options(db1, db2, collection_names)

if not available_fields:
    st.warning("No common fields were found across the configured collections.")
    st.stop()

comparison_field = st.selectbox("Select Comparison Attribute", available_fields)
include_null_values = st.checkbox("Include null and missing field values", value=True)

if st.button("SEARCH", type="primary"):
    for index, collection_name in enumerate(collection_names, start=1):
        st.divider()
        with st.spinner(f"Comparing {collection_name} ({index}/{len(collection_names)})..."):
            render_collection_result(
                db1=db1,
                db2=db2,
                collection_name=collection_name,
                comparison_field=comparison_field,
                include_null_values=include_null_values,
            )

