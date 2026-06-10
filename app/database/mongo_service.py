from collections import Counter
from typing import Any

from pymongo.database import Database
from pymongo.errors import PyMongoError


class MongoServiceError(RuntimeError):
    pass


def list_collections(database: Database) -> list[str]:
    try:
        return sorted(database.list_collection_names())
    except PyMongoError as exc:
        raise MongoServiceError(f"Unable to fetch collections: {exc}") from exc


def ensure_collection_exists(database: Database, collection_name: str) -> None:
    if collection_name not in list_collections(database):
        raise MongoServiceError(f"Collection '{collection_name}' does not exist.")


def get_collection_fields(
    database: Database,
    collection_name: str,
    sample_size: int = 100,
) -> list[str]:
    ensure_collection_exists(database, collection_name)
    collection = database[collection_name]
    fields: set[str] = set()

    try:
        for document in collection.find({}, limit=sample_size):
            fields.update(document.keys())
    except PyMongoError as exc:
        raise MongoServiceError(f"Unable to fetch fields from '{collection_name}': {exc}") from exc

    return sorted(fields)


def count_field_values(
    database: Database,
    collection_name: str,
    comparison_field: str,
    include_null_values: bool = True,
) -> Counter[Any]:
    ensure_collection_exists(database, collection_name)
    collection = database[collection_name]
    projection = {comparison_field: 1, "_id": 0}
    values: Counter[Any] = Counter()

    try:
        cursor = collection.find({}, projection)
        for document in cursor:
            value = document.get(comparison_field)
            if value is None and not include_null_values:
                continue
            values[_normalize_hashable_value(value)] += 1
    except PyMongoError as exc:
        raise MongoServiceError(
            f"Unable to fetch comparison values from '{collection_name}': {exc}"
        ) from exc

    return values


def collection_has_field(
    database: Database,
    collection_name: str,
    comparison_field: str,
) -> bool:
    ensure_collection_exists(database, collection_name)
    try:
        return database[collection_name].find_one(
            {comparison_field: {"$exists": True}},
            {comparison_field: 1, "_id": 0},
        ) is not None
    except PyMongoError as exc:
        raise MongoServiceError(
            f"Unable to validate field '{comparison_field}' in '{collection_name}': {exc}"
        ) from exc


def _normalize_hashable_value(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_normalize_hashable_value(item) for item in value)
    if isinstance(value, dict):
        return tuple(
            (key, _normalize_hashable_value(nested_value))
            for key, nested_value in sorted(value.items())
        )
    return value

