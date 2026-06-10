from functools import lru_cache

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ConfigurationError, ConnectionFailure, InvalidURI, PyMongoError

from app.config.settings import MongoSettings, get_settings


class MongoConnectionError(RuntimeError):
    pass


def _build_client(uri: str, timeout_ms: int) -> MongoClient:
    if not uri:
        raise MongoConnectionError("MongoDB URI is not configured.")

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        client.admin.command("ping")
        return client
    except (ConfigurationError, ConnectionFailure, InvalidURI, PyMongoError) as exc:
        raise MongoConnectionError(str(exc)) from exc


@lru_cache(maxsize=1)
def get_db1_client() -> MongoClient:
    settings = get_settings()
    return _build_client(settings.db1_mongo_uri, settings.server_selection_timeout_ms)


@lru_cache(maxsize=1)
def get_db2_client() -> MongoClient:
    settings = get_settings()
    return _build_client(settings.db2_mongo_uri, settings.server_selection_timeout_ms)


def _get_database(client: MongoClient, database_name: str, label: str) -> Database:
    if not database_name:
        raise MongoConnectionError(f"{label} database name is not configured.")

    try:
        database = client[database_name]
        database.command("ping")
        return database
    except PyMongoError as exc:
        raise MongoConnectionError(f"Unable to access {label}: {exc}") from exc


def get_db1_connection(settings: MongoSettings | None = None) -> Database:
    active_settings = settings or get_settings()
    return _get_database(get_db1_client(), active_settings.db1_name, "DB1")


def get_db2_connection(settings: MongoSettings | None = None) -> Database:
    active_settings = settings or get_settings()
    return _get_database(get_db2_client(), active_settings.db2_name, "DB2")


def clear_connection_cache() -> None:
    get_db1_client.cache_clear()
    get_db2_client.cache_clear()

