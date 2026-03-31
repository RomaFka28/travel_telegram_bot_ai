from __future__ import annotations

import logging

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
from llm_travel_planner import LLMPlannerSettings, LLMTravelPlanner
from travel_planner import TravelPlanner


async def post_init(application) -> None:
    commands = [
        BotCommand("start", "Быстрый старт и сценарии"),
        BotCommand("help", "Возможности, ограничения и инструкции"),
        BotCommand("plan", "Создать поездку из свободного текста"),
        BotCommand("planai", "Создать поездку через LLM (OpenRouter)"),
        BotCommand("share", "Поделиться планом"),
        BotCommand("newtrip", "Создать поездку пошагово"),
        BotCommand("trips", "Показать историю поездок"),
        BotCommand("select_trip", "Сделать поездку активной по ID"),
        BotCommand("summary", "Показать сводку"),
        BotCommand("brief", "Показать travel-brief"),
        BotCommand("itinerary", "Маршрут по дням"),
        BotCommand("budget", "Показать или обновить бюджет"),
        BotCommand("route", "Логистика и как добраться"),
        BotCommand("stay", "Где жить"),
        BotCommand("alternatives", "Альтернативные направления"),
        BotCommand("status", "Отметить статус участия"),
        BotCommand("participants", "Статусы участников"),
        BotCommand("adddate", "Добавить вариант дат"),
        BotCommand("setdestination", "Изменить направление"),
        BotCommand("setdates", "Изменить даты"),
        BotCommand("interests", "Изменить интересы"),
        BotCommand("notes", "Обновить заметки"),
        BotCommand("settings", "Управление режимом чата"),
        BotCommand("archive_trip", "Архивировать активную поездку"),
    ]
    await application.bot.set_my_commands(commands)



def build_application():
    settings = load_settings()
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        level=getattr(logging, settings.log_level, logging.INFO),
    )

    database = Database(settings.database_dsn)
    database.init_db()
    planner: TravelPlanner
    if settings.openrouter_api_key:
        planner = LLMTravelPlanner(
            LLMPlannerSettings(
                openrouter_api_key=settings.openrouter_api_key,
                openrouter_model=settings.openrouter_model,
            )
        )
    else:
        planner = TravelPlanner()
    formatter = TripFormatter(database)
    service = TripService(database, planner)
    handlers = BotHandlers(database, planner, formatter, service)

    app = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .build()
    )

    new_trip_conversation = ConversationHandler(
        entry_points=[CommandHandler("newtrip", handlers.new_trip_start)],
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
    app.add_handler(CommandHandler("share", handlers.share_command))
    app.add_handler(CommandHandler("plan", handlers.plan_command))
    app.add_handler(CommandHandler("planai", handlers.plan_ai_command))
    app.add_handler(CommandHandler("trips", handlers.trips_command))
    app.add_handler(CommandHandler("select_trip", handlers.select_trip_command))
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
    app.add_error_handler(handlers.error_handler)
    return app


if __name__ == "__main__":
    # Render Web Service requires binding to $PORT.
    start_if_render()
    application = build_application()
    application.run_polling(allowed_updates=None, drop_pending_updates=True)
