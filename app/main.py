from fastapi import FastAPI

from app.config.settings import get_settings


app = FastAPI(title="MongoDB Collection Comparator")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config/status")
def config_status() -> dict[str, bool]:
    settings = get_settings()
    return {
        "db1_uri_configured": bool(settings.db1_mongo_uri),
        "db2_uri_configured": bool(settings.db2_mongo_uri),
        "db1_name_configured": bool(settings.db1_name),
        "db2_name_configured": bool(settings.db2_name),
        "configuration_complete": settings.is_complete,
    }


