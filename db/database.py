import os
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    poster TEXT,
    total_episodes INTEGER,
    source_url TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS episodes (
    story_id TEXT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    episode_number INTEGER NOT NULL,
    title TEXT,
    duration_seconds INTEGER,
    media_url TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (story_id, episode_number)
);

CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    story_id TEXT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    episode_from INTEGER NOT NULL,
    episode_to INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    current_episode INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS job_items (
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    episode_number INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (job_id, episode_number)
);

CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs(telegram_id, status);
CREATE INDEX IF NOT EXISTS idx_episodes_story ON episodes(story_id);
"""


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


class Database:
    def __init__(self, url: str):
        self.url = url

    @contextmanager
    def connection(self):
        with psycopg.connect(self.url, row_factory=dict_row) as conn:
            yield conn

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.execute(SCHEMA)
            conn.commit()

    def upsert_user(self, telegram_id: int, username: str | None, first_name: str | None) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO users (telegram_id, username, first_name)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (telegram_id) DO UPDATE SET
                     username=EXCLUDED.username, first_name=EXCLUDED.first_name, updated_at=NOW()""",
                (telegram_id, username, first_name),
            )
            conn.commit()

    def save_story(self, story: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO stories (id,title,description,poster,total_episodes,source_url,metadata)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title,
                     description=EXCLUDED.description, poster=EXCLUDED.poster,
                     total_episodes=EXCLUDED.total_episodes, source_url=EXCLUDED.source_url,
                     metadata=EXCLUDED.metadata, updated_at=NOW()""",
                (story["id"], story["title"], story.get("description"), story.get("poster"),
                 story.get("episodes"), story.get("url"), psycopg.types.json.Jsonb(story.get("metadata", {}))),
            )
            conn.commit()

    def create_job(self, user_id: int, story_id: str, start: int, end: int) -> int:
        with self.connection() as conn:
            row = conn.execute(
                """INSERT INTO jobs (telegram_id,story_id,episode_from,episode_to)
                   VALUES (%s,%s,%s,%s) RETURNING id""", (user_id, story_id, start, end)
            ).fetchone()
            job_id = int(row["id"])
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO job_items (job_id,episode_number) VALUES (%s,%s)",
                    [(job_id, n) for n in range(start, end + 1)],
                )
            conn.commit()
            return job_id

    def set_job(self, job_id: int, **fields: Any) -> None:
        allowed = {"status", "success_count", "failed_count", "current_episode"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        fields["updated_at"] = "NOW()"
        assignments = []
        values = []
        for key, value in fields.items():
            if key == "updated_at":
                assignments.append("updated_at=NOW()")
            else:
                assignments.append(f"{key}=%s")
                values.append(value)
        values.append(job_id)
        with self.connection() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE id=%s", values)
            conn.commit()

    def set_item(self, job_id: int, episode: int, status: str, error: str | None = None) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE job_items SET status=%s,error=%s,updated_at=NOW() WHERE job_id=%s AND episode_number=%s",
                (status, error, job_id, episode),
            )
            conn.commit()

    def active_job_for_user(self, user_id: int):
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM jobs WHERE telegram_id=%s AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
