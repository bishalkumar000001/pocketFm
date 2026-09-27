import asyncio
import html
import logging
import re
from dataclasses import dataclass

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.catalog import CatalogClient
from bot.config import load_settings
from db.database import Database

settings = load_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pocketfm-bot")

if not settings.database_url:
    raise RuntimeError("DATABASE_URL is required. Attach Heroku Postgres or provide a PostgreSQL URL.")

db = Database(settings.database_url)
catalog = CatalogClient(settings.catalog_api_url, settings.catalog_api_key, settings.request_timeout)

sessions: dict[int, dict] = {}
active_tasks: dict[int, asyncio.Task] = {}
job_semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)


def parse_range(value: str):
    match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", value.strip())
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    if start < 1 or end < start or end - start + 1 > settings.max_range:
        return None
    return start, end


def esc(value) -> str:
    return html.escape(str(value or ""))


def range_help() -> str:
    return (
        f"Send an episode number or range, up to {settings.max_range} episodes.\n\n"
        "Examples:\n<code>1</code>\n<code>1-10</code>\n<code>25-50</code>"
    )


async def remember_user(update: Update) -> None:
    user = update.effective_user
    if user:
        await asyncio.to_thread(db.upsert_user, user.id, user.username, user.first_name)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await remember_user(update)
    await update.effective_message.reply_text(
        "🎧 <b>Pocket FM Story Bot</b>\n\n"
        "Search a story, view its available information, then type the episode number or range manually.\n\n"
        "<b>Example</b>\n<code>/search King</code>\n\n"
        f"{range_help()}",
        parse_mode=ParseMode.HTML,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "<b>Commands</b>\n\n"
        "/start — welcome\n/search &lt;story&gt; — search\n/cancel — cancel your active job\n/stats — bot status\n\n"
        f"{range_help()}", parse_mode=ParseMode.HTML,
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await remember_user(update)
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text("Use: <code>/search story name</code>", parse_mode=ParseMode.HTML)
        return

    await update.effective_message.reply_text(f"🔎 Searching for <b>{esc(query)}</b>...", parse_mode=ParseMode.HTML)
    try:
        results = await catalog.search(query)
    except Exception:
        log.exception("Catalog search failed")
        await update.effective_message.reply_text("The configured catalog service is unavailable right now.")
        return

    if not results:
        await update.effective_message.reply_text(
            "No story data was returned. Configure CATALOG_API_URL with your documented/authorized catalog provider."
        )
        return

    results = results[:10]
    sessions[update.effective_user.id] = {"results": results, "story": None}
    lines = []
    for index, item in enumerate(results, 1):
        total = item.get("episodes") or item.get("total_episodes") or "?"
        lines.append(f"{index}. <b>{esc(item.get('title', 'Untitled'))}</b> — {esc(total)} episodes")
    await update.effective_message.reply_text(
        "📚 <b>Search Results</b>\n\n" + "\n".join(lines) +
        "\n\nReply with the result number, for example <code>1</code>.",
        parse_mode=ParseMode.HTML,
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = active_tasks.get(update.effective_user.id)
    if not task:
        await update.effective_message.reply_text("No active download job.")
        return
    task.cancel()
    await update.effective_message.reply_text("⏹ Cancellation requested. The current operation will stop safely.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Active users/jobs in this dyno: {len(active_tasks)}\nConcurrent workers: {settings.max_concurrent_jobs}"
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in settings.admin_ids:
        await update.effective_message.reply_text("Admin only.")
        return
    await update.effective_message.reply_text(
        f"<b>Admin</b>\nActive jobs: {len(active_tasks)}\nMax range: {settings.max_range}",
        parse_mode=ParseMode.HTML,
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await remember_user(update)
    text = update.effective_message.text.strip()
    if text.startswith("/"):
        return

    session = sessions.get(update.effective_user.id)
    if not session:
        await update.effective_message.reply_text("Use /search story name first.")
        return

    if session.get("story") is None and text.isdigit():
        index = int(text) - 1
        results = session.get("results", [])
        if index < 0 or index >= len(results):
            await update.effective_message.reply_text("Invalid result number.")
            return
        story = results[index]
        session["story"] = story
        caption = (
            f"🎧 <b>{esc(story.get('title', 'Untitled'))}</b>\n\n"
            f"{esc(story.get('description') or 'No description available.')}\n\n"
            f"Episodes: {esc(story.get('episodes') or story.get('total_episodes') or 'Unknown')}\n\n"
            f"{range_help()}"
        )
        poster = story.get("poster")
        if poster:
            try:
                await update.effective_message.reply_photo(poster, caption=caption, parse_mode=ParseMode.HTML)
                return
            except Exception:
                log.exception("Poster send failed")
        await update.effective_message.reply_text(caption, parse_mode=ParseMode.HTML)
        return

    if session.get("story") is None:
        await update.effective_message.reply_text("Choose a search result number first.")
        return

    parsed = parse_range(text)
    if not parsed:
        await update.effective_message.reply_text(f"Invalid range.\n\n{range_help()}", parse_mode=ParseMode.HTML)
        return

    if update.effective_user.id in active_tasks:
        await update.effective_message.reply_text("You already have an active job. Use /cancel first.")
        return

    start, end = parsed
    task = asyncio.create_task(run_job(update, session["story"], start, end))
    active_tasks[update.effective_user.id] = task
    try:
        await task
    finally:
        active_tasks.pop(update.effective_user.id, None)


async def run_job(update: Update, story: dict, start: int, end: int):
    user_id = update.effective_user.id
    job_id = await asyncio.to_thread(db.create_job, user_id, str(story["id"]), start, end)
    await asyncio.to_thread(db.set_job, job_id, status="running")
    total = end - start + 1
    success = 0
    failed = 0

    async with job_semaphore:
        progress = await update.effective_message.reply_text(
            f"📥 <b>Download job started</b>\nStory: {esc(story.get('title'))}\n"
            f"Range: {start}-{end}\nProgress: 0/{total}", parse_mode=ParseMode.HTML
        )
        try:
            for episode in range(start, end + 1):
                await asyncio.to_thread(db.set_job, job_id, current_episode=episode)
                try:
                    media = None
                    for attempt in range(settings.max_retries + 1):
                        try:
                            media = await catalog.resolve_episode(story, episode)
                            break
                        except Exception as exc:
                            if attempt >= settings.max_retries:
                                raise exc
                            await asyncio.sleep(min(2 ** attempt, 8))
                    if not media or not media.get("url"):
                        raise RuntimeError("Episode media is not available from the configured authorized source.")

                    # Media delivery is intentionally delegated to the authorized provider.
                    # This starter does not bypass DRM/paywalls/access controls.
                    await update.effective_message.reply_text(
                        f"Episode {episode} is available from the configured authorized source, "
                        "but no automatic media uploader is enabled in this safe starter."
                    )
                    success += 1
                    await asyncio.to_thread(db.set_item, job_id, episode, "success")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed += 1
                    await asyncio.to_thread(db.set_item, job_id, episode, "failed", str(exc)[:1000])
                    log.warning("Episode %s failed: %s", episode, exc)

                await asyncio.to_thread(db.set_job, job_id, success_count=success, failed_count=failed)
                try:
                    await progress.edit_text(
                        f"📥 <b>Processing</b>\nStory: {esc(story.get('title'))}\nRange: {start}-{end}\n"
                        f"Progress: {episode-start+1}/{total}\nSuccess: {success}\nFailed: {failed}",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

            await asyncio.to_thread(db.set_job, job_id, status="completed")
            await update.effective_message.reply_text(
                f"✅ <b>Job finished</b>\nCompleted: {success}\nFailed: {failed}", parse_mode=ParseMode.HTML
            )
        except asyncio.CancelledError:
            await asyncio.to_thread(db.set_job, job_id, status="cancelled", success_count=success, failed_count=failed)
            await update.effective_message.reply_text(
                f"⏹ <b>Job cancelled</b>\nCompleted: {success}\nFailed: {failed}", parse_mode=ParseMode.HTML
            )


def main():
    db.initialize()
    application = Application.builder().token(settings.bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    log.info("Starting Pocket FM Telegram bot")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
