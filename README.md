# MongoDB Collection Comparator

A Streamlit-based validation tool for comparing values in MongoDB collections across two database environments.

## Features

- Connects to two MongoDB instances using environment variables.
- Reads a configured collection list from `.env`.
- Runs collection comparisons in batch.
- Fetches available comparison attributes from the configured collections.
- Compares selected field values using dictionary and set-style lookups.
- Compares only documents where `active` is `true` and `deleted` is `false`.
- Reports matches, mismatches, duplicate values, null values, and document counts.
- Shows each collection layer by layer: comparison table, matched documents, then unmatched documents.
- Provides Excel downloads for all matched data and all unmatched data.
- Uses MongoDB projections so only the selected comparison field is fetched.
- Includes a small FastAPI app for Uvicorn health/config checks.

## Project Structure

```text
app/
  main.py
  config/
    settings.py
  database/
    mongo_connection.py
    mongo_service.py
  comparison/
    comparator.py
    matcher.py
  models/
    comparison_models.py
streamlit_app.py
requirements.txt
.env
README.md
```

## Configuration

Update `.env` with your MongoDB connection details:

```env
DB1_MONGO_URI=<mongodb_connection_string>
DB2_MONGO_URI=<mongodb_connection_string>
DB1_NAME=<database_name>
DB2_NAME=<database_name>
MONGO_SERVER_SELECTION_TIMEOUT_MS=5000
COLLECTION_NAMES=collection_one,collection_two,collection_three
```

Connection details are loaded with `python-dotenv` and are never hardcoded.

`COLLECTION_NAMES` is comma-separated. The app compares each listed collection by the same selected comparison attribute. Collections missing from either database are skipped with a warning.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run the Streamlit UI

```bash
streamlit run streamlit_app.py
```

## Run the Uvicorn API

```bash
uvicorn app.main:app --reload
```

Available API endpoints:

- `GET /health`
- `GET /config/status`

## Comparison Rules

For the selected comparison field across each configured collection:

- `MATCH`: value exists in both DB1 and DB2 collections.
- `MISMATCH`: value exists in only one database.
- Duplicate values are counted and flagged.
- Null or missing field values can be included or excluded from the comparison.

The Streamlit UI only asks for the comparison attribute. Collection selection is controlled by `COLLECTION_NAMES` in `.env`.

Only filtered active records are compared:

```python
{"active": True, "deleted": False}
```

## Output Columns

- Comparison Value
- DB1 Exists
- DB2 Exists
- DB1 Document Count
- DB2 Document Count
- Status
- Duplicate Value
- Null Value

## Performance Notes

The comparator avoids nested loops. It builds count maps for each collection and then compares the union of keys. MongoDB queries use projection:

```python
collection.find({}, {comparison_field: 1, "_id": 0})
```

For best performance on large collections, add indexes for fields that are frequently compared.
