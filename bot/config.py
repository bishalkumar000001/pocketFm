import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: set[int]
    database_url: str
    catalog_api_url: str
    catalog_api_key: str
    max_range: int
    max_concurrent_jobs: int
    request_timeout: int
    max_retries: int
    log_level: str


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is required")

    admin_ids: set[int] = set()
    for value in os.getenv("ADMIN_IDS", "").split(","):
        value = value.strip()
        if value:
            try:
                admin_ids.add(int(value))
            except ValueError:
                pass

    return Settings(
        bot_token=token,
        admin_ids=admin_ids,
        database_url=os.getenv("DATABASE_URL", "").strip(),
        catalog_api_url=os.getenv("CATALOG_API_URL", "").strip().rstrip("/"),
        catalog_api_key=os.getenv("CATALOG_API_KEY", "").strip(),
        max_range=max(1, _int("MAX_RANGE", 100)),
        max_concurrent_jobs=max(1, _int("MAX_CONCURRENT_JOBS", 1)),
        request_timeout=max(10, _int("REQUEST_TIMEOUT", 60)),
        max_retries=max(0, _int("MAX_RETRIES", 3)),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
