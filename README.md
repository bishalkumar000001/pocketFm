# Pocket FM Telegram Bot — Heroku Ready

A Python/Telegram worker with PostgreSQL persistence, manual episode-range input, job tracking, retries, and an adapter for a documented/public/authorized catalog/media provider.

## Heroku

Current Heroku guidance recommends `.python-version` rather than `runtime.txt`; this project includes both. `.python-version` is authoritative and uses Python 3.14. `runtime.txt` is retained only as a compatibility fallback.

### Buildpack

```text
heroku/python
```

### Required Config Vars

```text
BOT_TOKEN=your Telegram bot token
DATABASE_URL=automatically supplied by Heroku Postgres
```

Optional:

```text
ADMIN_IDS=123456789,987654321
Pocket FM public web catalog=https://your-authorized-provider.example/api
(no catalog key required)=your-key
MAX_RANGE=100
MAX_CONCURRENT_JOBS=1
REQUEST_TIMEOUT=60
MAX_RETRIES=3
LOG_LEVEL=INFO
```

### Deploy

```bash
heroku create your-pocketfm-bot
heroku addons:create heroku-postgresql:essential-0
heroku buildpacks:set heroku/python
heroku config:set BOT_TOKEN="YOUR_TOKEN"
git push heroku main
heroku ps:scale worker=1
heroku logs --tail
```

The database schema is initialized automatically when the worker starts.

## Bot flow

```text
/start
/search story name
1
1-10
25-50
/cancel
```

There are no episode inline buttons. After choosing a story, the user types the episode number or range manually.

## Media-source integration

`bot/catalog.py` expects an authorized provider with:

- `GET /search?q=<story>`
- `GET /episode?story_id=<id>&episode=<number>`

The episode response should contain a usable `url` for media the requesting user is authorized to access.

This project intentionally does not bypass DRM, paywalls, authentication barriers, signed-access restrictions, or other access controls.


## Catalog

The bot reads story/search metadata from Pocket FM's public web pages, so `CATALOG_API_URL` and `CATALOG_API_KEY` are not required. It does not attempt to bypass login, coins, DRM, signed-media restrictions, or other access controls. A download is attempted only when the public page exposes a directly usable media URL.
