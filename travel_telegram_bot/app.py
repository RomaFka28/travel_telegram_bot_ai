from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from telegram import BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.formatters import TripFormatter
from bot.handlers import (
    NEW_TRIP_BUDGET,
    NEW_TRIP_DATES,
    NEW_TRIP_DESTINATION,
    NEW_TRIP_DAYS,
    NEW_TRIP_GROUP_SIZE,
    NEW_TRIP_INTERESTS,
    NEW_TRIP_NOTES,
    NEW_TRIP_ORIGIN,
    NEW_TRIP_TITLE,
    BotHandlers,
)
from bot.trip_service import TripService
from config import load_settings
from database import Database
from health_server import start_if_render
from housing_search import build_housing_provider
from llm_provider_pool import LLMProviderPool, build_provider_list
from llm_travel_planner import LLMTravelPlanner
from logging_config import get_logger, setup_logging
from travelpayouts_flights import TravelpayoutsFlightProvider
from travelpayouts_partner_links import TravelpayoutsPartnerLinksClient, TravelpayoutsPartnerLinksConfig
from travel_planner import TravelPlanner

logger = get_logger(__name__)


def _database_target_label(database: Database) -> str:
    if database.is_postgres:
        parsed = urlparse(database.dsn)
        host = parsed.hostname or "unknown-host"
        db_name = (parsed.path or "/").lstrip("/") or "postgres"
        return f"postgres host={host} db={db_name}"
    return f"sqlite path={Path(database.dsn).resolve()}"


async def post_init(application) -> None:
    commands = [
        BotCommand("start", "Как пользоваться ботом в чате"),
        BotCommand("help", "Короткая справка"),
        BotCommand("summary", "Текущий план поездки"),
        BotCommand("tickets", "Цены на билеты и оценка"),
        BotCommand("status", "Мой ответ по поездке"),
        BotCommand("settings", "Авто-анализ и режим чата"),
        BotCommand("trips", "История поездок"),
        BotCommand("select_trip", "Вернуть поездку из истории"),
        BotCommand("delete_trip", "Удалить поездку навсегда"),
        BotCommand("plan", "Начать ручной бриф поездки"),
        BotCommand("newtrip", "Пошаговое создание поездки"),
        BotCommand("hotels", "Где искать жильё и варианты"),
        BotCommand("participants", "Статусы участников"),
        BotCommand("adddate", "Добавить вариант дат"),
        BotCommand("archive_trip", "Убрать активную поездку в архив"),
    ]
    await application.bot.set_my_commands(commands)



def build_application():
    settings = load_settings()
    
    # Настраиваем расширенную систему логирования
    log_file = "logs/bot.log" if not os.getenv("RENDER") else None
    setup_logging(
        level=settings.log_level,
        log_file=log_file,
        max_bytes=10 * 1024 * 1024,  # 10 MB
        backup_count=5,
    )

    database = Database(settings.database_dsn)
    database.init_db()
    logger.info(
        "Database backend ready: database_backend=%s target=%s",
        "postgres" if database.is_postgres else "sqlite",
        _database_target_label(database),
    )
    planner: TravelPlanner
    providers = build_provider_list(
        openrouter_api_key=settings.openrouter_api_key,
        openrouter_model=settings.openrouter_model,
        openrouter_web_search=settings.openrouter_web_search,
        gemini_api_key=settings.gemini_api_key,
        groq_api_key=settings.groq_api_key,
    )
    if providers:
        pool = LLMProviderPool(providers)
        planner = LLMTravelPlanner(pool)
        logger.info(
            "LLM provider pool ready: %s",
            " -> ".join(f"{provider.name}({provider.daily_limit}/day)" for provider in providers),
        )
    else:
        planner = TravelPlanner()
        logger.info("No LLM API keys configured, using heuristic planner")
    formatter = TripFormatter(database)
    partner_links = TravelpayoutsPartnerLinksClient(
        TravelpayoutsPartnerLinksConfig(
            api_key=settings.travelpayouts_api_key,
            marker=settings.travelpayouts_marker,
            trs=settings.travelpayouts_trs,
        )
    )
    flight_provider = TravelpayoutsFlightProvider(settings.travelpayouts_api_key, partner_links)
    service = TripService(database, planner, flight_provider)
    housing_provider = build_housing_provider(
        playwright_enabled=settings.playwright_enabled,
        timeout_ms=settings.playwright_timeout_ms,
    )
    handlers = BotHandlers(database, planner, formatter, service, housing_provider, flight_provider)

    app = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .build()
    )

    new_trip_conversation = ConversationHandler(
        entry_points=[CommandHandler("newtrip", handlers.new_trip_start)],
        allow_reentry=True,
        states={
            NEW_TRIP_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.new_trip_title)],
            NEW_TRIP_DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.new_trip_destination)],
            NEW_TRIP_ORIGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.new_trip_origin)],
            NEW_TRIP_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.new_trip_days)],
            NEW_TRIP_DATES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.new_trip_dates)],
            NEW_TRIP_GROUP_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.new_trip_group_size)],
            NEW_TRIP_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.new_trip_budget)],
            NEW_TRIP_INTERESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.new_trip_interests)],
            NEW_TRIP_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.new_trip_notes)],
        },
        fallbacks=[CommandHandler("cancel", handlers.cancel_new_trip)],
        name="new_trip_conversation",
        persistent=False,
    )

    app.add_handler(new_trip_conversation)
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("help", handlers.help_command))
    app.add_handler(CommandHandler("tickets", handlers.tickets_command))
    app.add_handler(CommandHandler("hotels", handlers.hotels_command))
    app.add_handler(CommandHandler("plan", handlers.plan_command))
    app.add_handler(CommandHandler("trips", handlers.trips_command))
    app.add_handler(CommandHandler("select_trip", handlers.select_trip_command))
    app.add_handler(CommandHandler("delete_trip", handlers.delete_trip_command))
    app.add_handler(CommandHandler("summary", handlers.summary_command))
    app.add_handler(CommandHandler("brief", handlers.brief_command))
    app.add_handler(CommandHandler("itinerary", handlers.itinerary_command))
    app.add_handler(CommandHandler("budget", handlers.budget_command))
    app.add_handler(CommandHandler("route", handlers.route_command))
    app.add_handler(CommandHandler("stay", handlers.stay_command))
    app.add_handler(CommandHandler("alternatives", handlers.alternatives_command))
    app.add_handler(CommandHandler("status", handlers.status_command))
    app.add_handler(CommandHandler("participants", handlers.participants_command))
    app.add_handler(CommandHandler("adddate", handlers.add_date_command))
    app.add_handler(CommandHandler("setdestination", handlers.set_destination_command))
    app.add_handler(CommandHandler("setdates", handlers.set_dates_command))
    app.add_handler(CommandHandler("interests", handlers.interests_command))
    app.add_handler(CommandHandler("notes", handlers.notes_command))
    app.add_handler(CommandHandler("settings", handlers.settings_command))
    app.add_handler(CommandHandler("archive_trip", handlers.archive_trip_command))

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handlers.handle_trip_edit_input,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            handlers.handle_group_message,
        )
    )
    app.add_handler(CallbackQueryHandler(handlers.trip_action_callback, pattern=r"^tripaction:"))
    app.add_handler(CallbackQueryHandler(handlers.date_vote_callback, pattern=r"^datevote:"))
    app.add_handler(CallbackQueryHandler(handlers.settings_callback, pattern=r"^settings:"))
    app.add_handler(CallbackQueryHandler(handlers.language_callback, pattern=r"^language:"))
    app.add_error_handler(handlers.error_handler)
    return app


if __name__ == "__main__":
    # Render Web Service requires binding to $PORT.
    start_if_render()
    logger.info("🚀 Starting Telegram bot...")
    application = build_application()
    logger.info("Bot application built, starting polling...")
    try:
        application.run_polling(allowed_updates=None, drop_pending_updates=True)
    except Exception as e:
        logger.critical("Bot crashed: %s: %s", e.__class__.__name__, e, exc_info=True)
        raise
