import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class MongoSettings:
    db1_mongo_uri: str
    db2_mongo_uri: str
    db1_name: str
    db2_name: str
    server_selection_timeout_ms: int = 5000

    @property
    def is_complete(self) -> bool:
        return all(
            [
                self.db1_mongo_uri,
                self.db2_mongo_uri,
                self.db1_name,
                self.db2_name,
            ]
        )


@lru_cache(maxsize=1)
def get_settings() -> MongoSettings:
    return MongoSettings(
        db1_mongo_uri=os.getenv("DB1_MONGO_URI", "").strip(),
        db2_mongo_uri=os.getenv("DB2_MONGO_URI", "").strip(),
        db1_name=os.getenv("DB1_NAME", "").strip(),
        db2_name=os.getenv("DB2_NAME", "").strip(),
        server_selection_timeout_ms=int(
            os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")
        ),
    )

