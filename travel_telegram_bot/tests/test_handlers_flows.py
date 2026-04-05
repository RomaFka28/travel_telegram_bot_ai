import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.formatters import TripFormatter
from bot.handlers import BotHandlers
from bot.trip_service import TripService
from database import Database
from housing_search import LinkOnlyHousingSearchProvider
from llm_provider_pool import LLMProvider, LLMProviderPool
from llm_travel_planner import LLMTravelPlanner
from telegram.error import Conflict
from travelpayouts_flights import TravelpayoutsFlightProvider
from travel_planner import TravelPlanner
from travel_result_models import TravelSearchResult
from trip_request_extractor import TripRequestExtraction


class DummyMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[dict[str, object]] = []

    async def reply_text(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        reply = {
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            **kwargs,
        }
        self.replies.append(reply)
        return DummySentMessage(reply)


class DummySentMessage:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.deleted = False

    async def edit_text(self, text: str, parse_mode=None, reply_markup=None, **kwargs) -> None:
        self.payload.update(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                **kwargs,
            }
        )

    async def delete(self) -> None:
        self.deleted = True


class DummyCallbackQuery:
    def __init__(self, data: str, user, message: DummyMessage) -> None:
        self.data = data
        self.from_user = user
        self.message = message
        self.answers: list[dict[str, object]] = []
        self.edits: list[dict[str, object]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append({"text": text, "show_alert": show_alert})

    async def edit_message_text(self, text: str, parse_mode=None, reply_markup=None, **kwargs) -> None:
        self.edits.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                **kwargs,
            }
        )


class DummyBot:
    username = "demo_trip_bot"


class DummyChat:
    def __init__(self, chat_id: int, chat_type: str = "group") -> None:
        self.id = chat_id
        self.type = chat_type
        self.actions: list[str] = []

    async def send_action(self, action: str) -> None:
        self.actions.append(action)


class DummyContext:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []
        self.user_data: dict[str, object] = {}
        self.chat_data: dict[str, object] = {}
        self.bot = DummyBot()


def make_update(
    *,
    text: str = "",
    chat_id: int = 100,
    chat_type: str = "group",
    user_id: int = 1,
    username: str = "user1",
    first_name: str = "Test",
    last_name: str = "User",
):
    message = DummyMessage(text)
    user = SimpleNamespace(
        id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=DummyChat(chat_id, chat_type),
        effective_user=user,
        callback_query=None,
    )
    return update, message


def make_callback_update(*, data: str, chat_id: int = 100, chat_type: str = "group", user_id: int = 1, username: str = "user1"):
    message = DummyMessage()
    user = SimpleNamespace(
        id=user_id,
        username=username,
        first_name="Callback",
        last_name="User",
    )
    query = DummyCallbackQuery(data=data, user=user, message=message)
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=DummyChat(chat_id, chat_type),
        effective_user=user,
        callback_query=query,
    )
    return update, message, query


def build_handlers(tmp_path) -> tuple[Database, BotHandlers]:
    database = Database(str(tmp_path / "handlers.db"))
    database.init_db()
    planner = TravelPlanner()
    formatter = TripFormatter(database)
    service = TripService(database, planner)
    housing_provider = LinkOnlyHousingSearchProvider()
    return database, BotHandlers(database, planner, formatter, service, housing_provider)


class FakeFlightProvider:
    enabled = True

    def search_results(
        self,
        *,
        origin: str,
        destination: str,
        dates_text: str,
        budget_text: str,
        group_size: int,
        source_text: str = "",
    ) -> list[TravelSearchResult]:
        return [
            TravelSearchResult(
                title=f"{origin} -> {destination}",
                price_text=f"12 300 ₽/чел. (49 200 ₽ на {group_size} чел.)",
                url="https://example.com/tickets",
                source="Travelpayouts / Aviasales",
                score=9,
                budget_fit="вписывается в средний бюджет",
                dates="2026-06-12 -> 2026-06-14",
                note="прямой, оценка 9/10",
            )
        ]

    def build_ticket_snapshot(
        self,
        *,
        origin: str,
        destination: str,
        dates_text: str,
        budget_text: str,
        group_size: int,
        source_text: str = "",
    ) -> str:
        return (
            f"Travelpayouts / Aviasales: свежие цены для {origin} -> {destination}\n"
            f"1. 12 300 ₽/чел. (49 200 ₽ на {group_size} чел.), 2026-06-12 -> 2026-06-14, прямой, оценка 9/10, вписывается в средний бюджет"
        )


def test_plan_command_without_args_starts_pending_flow(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    update, message = make_update(chat_id=1501, chat_type="private")
    context = DummyContext()

    asyncio.run(handlers.plan_command(update, context))

    assert context.chat_data["pending_plan_prompt:1"] is True
    assert "Отправьте следующим сообщением" in message.replies[-1]["text"]
    assert "сколько человек" in message.replies[-1]["text"]
    assert "в одну сторону" in message.replies[-1]["text"]


def test_start_in_new_chat_shows_language_picker(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    update, message = make_update(chat_id=1701, chat_type="private")

    asyncio.run(handlers.start(update, DummyContext()))

    assert "Choose the bot language" in message.replies[-1]["text"]
    assert message.replies[-1]["reply_markup"] is not None


def test_language_callback_saves_english_and_replies_in_english(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    callback_update, callback_message, query = make_callback_update(data="language:set:en", chat_id=1702, chat_type="private")

    asyncio.run(handlers.language_callback(callback_update, DummyContext()))

    assert database.get_chat_language(1702) == "en"
    assert "Hi! Add me to your trip chat" in query.edits[-1]["text"]


def test_status_command_describes_active_trip_context(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    create_update, _ = make_update(chat_id=1703)
    asyncio.run(handlers.plan_command(create_update, DummyContext(args=["Хочу", "в", "Казань", "на", "3", "дня"])))
    trip = database.get_active_trip(1703)
    assert trip is not None

    status_update, status_message = make_update(chat_id=1703)
    asyncio.run(handlers.status_command(status_update, DummyContext()))

    rendered = status_message.replies[-1]["text"]
    assert "Ваш ответ по активной поездке этого чата" in rendered
    assert "Сейчас речь про" in rendered
    assert "Казань" in rendered


def test_error_handler_stops_application_on_polling_conflict(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    stop_running = Mock()
    context = SimpleNamespace(
        error=Conflict("terminated by other getUpdates request"),
        application=SimpleNamespace(stop_running=stop_running),
    )

    asyncio.run(handlers.error_handler(update=None, context=context))

    stop_running.assert_called_once_with()


def test_plan_pending_message_in_private_chat_creates_trip(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    context = DummyContext()
    context.chat_data["pending_plan_prompt:1"] = True
    update, message = make_update(
        text="Хочу с друзьями в Казань на 3 дня, нас 4, из Томска, туда 12 июня, обратно 15 июня, бюджет средний",
        chat_id=1502,
        chat_type="private",
    )

    asyncio.run(handlers.handle_trip_edit_input(update, context))

    trip = database.get_active_trip(1502)
    assert trip is not None
    assert trip["destination"] == "Казань"
    assert "Собрал новый план" in message.replies[0]["text"]


def test_plan_pending_message_requests_missing_details_before_creating_trip(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    context = DummyContext()
    context.chat_data["pending_plan_prompt:1"] = True

    first_update, first_message = make_update(
        text="Хочу в Стамбул 12 июня, нужен билет, бюджет бизнес, люблю прогулки и еду",
        chat_id=1512,
        chat_type="private",
    )
    asyncio.run(handlers.handle_trip_edit_input(first_update, context))

    assert database.get_active_trip(1512) is None
    assert "вылет" in first_message.replies[-1]["text"].lower()

    second_update, second_message = make_update(text="Тбилиси", chat_id=1512, chat_type="private")
    asyncio.run(handlers.handle_trip_edit_input(second_update, context))

    assert database.get_active_trip(1512) is None
    assert "в одну сторону" in second_message.replies[-1]["text"].lower()

    third_update, third_message = make_update(text="в одну сторону", chat_id=1512, chat_type="private")
    asyncio.run(handlers.handle_trip_edit_input(third_update, context))

    trip = database.get_active_trip(1512)
    assert trip is not None
    assert trip["destination"] == "Стамбул"
    assert "Собрал новый план" in third_message.replies[0]["text"]
    assert "🧭" in third_message.replies[1]["text"]


def test_plan_pending_message_in_group_chat_creates_trip(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    context = DummyContext()
    context.chat_data["pending_plan_prompt:1"] = True
    update, message = make_update(
        text="Едем в Сочи вдвоем, вылет из Новосибирска, туда 12 июня, обратно 18 июня, бюджет комфорт",
        chat_id=1503,
        chat_type="group",
    )

    asyncio.run(handlers.handle_group_message(update, context))

    trip = database.get_active_trip(1503)
    assert trip is not None
    assert trip["destination"] == "Сочи"
    assert "Собрал новый план для этой группы" in message.replies[0]["text"]


def test_group_pending_plan_prompt_is_scoped_to_requesting_user(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    database.toggle_autodraft(1504)
    shared_chat_data: dict[str, object] = {"pending_plan_prompt:1": True}
    first_context = DummyContext()
    first_context.chat_data = shared_chat_data
    second_context = DummyContext()
    second_context.chat_data = shared_chat_data

    second_update, second_message = make_update(
        text="Едем в Сочи вдвоем, вылет из Новосибирска, туда 12 июня, обратно 18 июня, бюджет комфорт",
        chat_id=1504,
        chat_type="group",
        user_id=2,
        username="user2",
    )
    asyncio.run(handlers.handle_group_message(second_update, second_context))

    assert database.get_active_trip(1504) is None
    assert second_message.replies == []
    assert shared_chat_data["pending_plan_prompt:1"] is True

    first_update, first_message = make_update(
        text="Едем в Сочи вдвоем, вылет из Новосибирска, туда 12 июня, обратно 18 июня, бюджет комфорт",
        chat_id=1504,
        chat_type="group",
        user_id=1,
        username="user1",
    )
    asyncio.run(handlers.handle_group_message(first_update, first_context))

    trip = database.get_active_trip(1504)
    assert trip is not None
    assert trip["destination"] == "Сочи"
    assert "Собрал новый план для этой группы" in first_message.replies[0]["text"]


def test_group_plan_followup_is_scoped_to_requesting_user(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    shared_chat_data: dict[str, object] = {}
    first_context = DummyContext()
    first_context.chat_data = shared_chat_data
    second_context = DummyContext()
    second_context.chat_data = shared_chat_data

    first_update, first_message = make_update(
        text="Хочу в Стамбул 12 июня, нужен билет, бюджет бизнес, люблю прогулки и еду",
        chat_id=1514,
        chat_type="group",
        user_id=1,
        username="user1",
    )
    asyncio.run(handlers._create_trip_from_text(first_update, first_update.effective_message.text, first_context))

    assert "plan_followup:1" in shared_chat_data
    assert database.get_active_trip(1514) is None
    assert "вылет" in first_message.replies[-1]["text"].lower()

    second_update, second_message = make_update(
        text="Тбилиси",
        chat_id=1514,
        chat_type="group",
        user_id=2,
        username="user2",
    )
    asyncio.run(handlers.handle_group_message(second_update, second_context))

    assert database.get_active_trip(1514) is None
    assert shared_chat_data["plan_followup:1"]["index"] == 0
    assert second_message.replies == []


def test_plan_command_creates_trip_and_archives_previous(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)

    first_update, first_message = make_update(chat_id=501)
    first_context = DummyContext(args=["Хочу", "в", "Казань", "на", "3", "дня", "с", "друзьями", "бюджет", "средний"])
    asyncio.run(handlers.plan_command(first_update, first_context))

    second_update, second_message = make_update(chat_id=501)
    second_context = DummyContext(args=["Хочу", "в", "Сочи", "на", "4", "дня", "с", "друзьями", "бюджет", "комфорт"])
    asyncio.run(handlers.plan_command(second_update, second_context))

    active_trip = database.get_active_trip(501)
    all_trips = database.list_trips(501)

    assert active_trip is not None
    assert active_trip["destination"] == "Сочи"
    assert len(all_trips) == 2
    assert any(trip["status"] == "archived" for trip in all_trips)
    assert "Предыдущий сохранён в истории" in second_message.replies[0]["text"]
    assert "Сочи" in second_message.replies[1]["text"]
    assert "Казань" in first_message.replies[1]["text"]


def test_plan_command_does_not_mark_housing_need_without_explicit_housing_keywords(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    update, _ = make_update(chat_id=1513, chat_type="private")
    context = DummyContext(args=["Хочу", "в", "Стамбул", "один,", "вылет", "из", "Тбилиси", "12", "июня,", "билет", "в", "одну", "сторону,", "бюджет", "Бизнес,", "интересуют", "прогулки", "и", "еда"])

    asyncio.run(handlers.plan_command(update, context))

    trip = database.get_active_trip(1513)
    assert trip is not None
    assert "housing" not in (trip["detected_needs"] or "")


def test_plan_command_preserves_multiple_explicit_interests(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    update, _ = make_update(chat_id=1515, chat_type="private")
    context = DummyContext(args=["Хочу", "в", "Стамбул", "один,", "вылет", "из", "Тбилиси", "12", "июня,", "билет", "в", "одну", "сторону,", "бюджет", "Бизнес,", "интересуют", "прогулки", "и", "еда"])

    asyncio.run(handlers.plan_command(update, context))

    trip = database.get_active_trip(1515)
    assert trip is not None
    assert "прогулки" in (trip["interests_text"] or "")
    assert "еда" in (trip["interests_text"] or "")


def test_newtrip_flow_creates_trip(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    context = DummyContext()

    for handler, text in [
        (handlers.new_trip_start, ""),
        (handlers.new_trip_title, "Летний выезд"),
        (handlers.new_trip_destination, "Владивосток"),
        (handlers.new_trip_origin, "Новосибирск"),
        (handlers.new_trip_days, "5"),
        (handlers.new_trip_dates, "12–16 июня"),
        (handlers.new_trip_group_size, "4"),
        (handlers.new_trip_budget, "средний"),
        (handlers.new_trip_interests, "море, еда"),
        (handlers.new_trip_notes, "купить билеты до пятницы"),
    ]:
        update, _ = make_update(text=text, chat_id=777)
        asyncio.run(handler(update, context))

    trip = database.get_active_trip(777)
    assert trip is not None
    assert trip["destination"] == "Владивосток"
    assert trip["group_size"] == 4
    assert trip["notes"] == "купить билеты до пятницы"


def test_newtrip_restarts_from_first_step_when_called_again(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    context = DummyContext()

    first_update, first_message = make_update(chat_id=778, chat_type="private")
    asyncio.run(handlers.new_trip_start(first_update, context))
    assert "Как назвать поездку?" in first_message.replies[-1]["text"]

    context.user_data["trip_draft"] = {"destination": "Казань"}
    second_update, second_message = make_update(chat_id=778, chat_type="private")
    asyncio.run(handlers.new_trip_start(second_update, context))

    assert context.user_data["trip_draft"] == {}
    assert "Как назвать поездку?" in second_message.replies[-1]["text"]


def test_status_command_and_participants_summary_cover_all_statuses(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    setup_update, _ = make_update(chat_id=333)
    setup_context = DummyContext(args=["Хочу", "в", "Казань", "на", "3", "дня", "нас", "4"])
    asyncio.run(handlers.plan_command(setup_update, setup_context))

    for user_id, username, args in [
        (1, "goer", ["еду"]),
        (2, "maybe", ["думаю"]),
        (3, "nope", ["не", "еду"]),
    ]:
        update, _ = make_update(chat_id=333, user_id=user_id, username=username)
        context = DummyContext(args=args)
        asyncio.run(handlers.status_command(update, context))

    participants_update, participants_message = make_update(chat_id=333)
    asyncio.run(handlers.participants_command(participants_update, DummyContext()))

    response_text = participants_message.replies[-1]["text"]
    assert "Едут (1)" in response_text
    assert "Думают (1)" in response_text
    assert "Не едут (1)" in response_text


def test_settings_toggle_can_disable_group_autodraft(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)

    settings_update, settings_message = make_update(chat_id=909)
    asyncio.run(handlers.settings_command(settings_update, DummyContext()))
    assert "Авто-черновики" in settings_message.replies[0]["text"]
    assert "старую историю чата задним числом" in settings_message.replies[0]["text"]

    callback_update, _, query = make_callback_update(data="settings:toggle_autodraft", chat_id=909)
    asyncio.run(handlers.settings_callback(callback_update, DummyContext()))
    assert bool(database.get_or_create_settings(909)["autodraft_enabled"]) is False
    assert "Авто-черновики" in query.edits[-1]["text"]

    group_update, group_message = make_update(
        text="Ребята, поедем в Казань в июле на четыре дня?",
        chat_id=909,
    )
    group_context = DummyContext()
    asyncio.run(handlers.handle_group_message(group_update, group_context))

    assert database.get_active_trip(909) is None
    assert group_message.replies == []


def test_archive_keeps_trip_history(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    create_update, _ = make_update(chat_id=404)
    create_context = DummyContext(args=["Хочу", "в", "Питер", "на", "3", "дня"])
    asyncio.run(handlers.plan_command(create_update, create_context))

    archive_update, archive_message = make_update(chat_id=404)
    asyncio.run(handlers.archive_trip_command(archive_update, DummyContext()))

    all_trips = database.list_trips(404)
    assert database.get_active_trip(404) is None
    assert len(all_trips) == 1
    assert all_trips[0]["status"] == "archived"
    assert "История сохранена" in archive_message.replies[-1]["text"]


def test_group_autodraft_reply_shows_multiple_detected_categories(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    context = DummyContext()

    update, message = make_update(
        text="Летим из Томска в Казань, нужен отель, экскурсии и поезд обратно тоже посмотрим",
        chat_id=1404,
    )
    asyncio.run(handlers.handle_group_message(update, context))

    rendered = message.replies[-1]["text"]
    assert "Билеты" in rendered
    assert "Жильё" in rendered
    assert "Экскурсии" in rendered
    assert "Дорога" in rendered
    assert "Готовность плана" in rendered


def test_hotels_command_returns_russian_housing_sources(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    create_update, _ = make_update(chat_id=405)
    create_context = DummyContext(args=["Хочу", "в", "Казань", "на", "3", "дня"])
    asyncio.run(handlers.plan_command(create_update, create_context))

    hotels_update, hotels_message = make_update(chat_id=405)
    asyncio.run(handlers.hotels_command(hotels_update, DummyContext()))

    assert "Ищу варианты" in hotels_message.replies[0]["text"]
    assert "Островок" in hotels_message.replies[-1]["text"]
    assert "Яндекс Путешествия" in hotels_message.replies[-1]["text"]


def test_plan_command_in_private_chat_uses_private_wording(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    update, message = make_update(
        chat_id=4060,
        chat_type="private",
    )
    context = DummyContext(args=["Хочу", "в", "Казань", "на", "3", "дня"])

    asyncio.run(handlers.plan_command(update, context))

    assert "для этой группы" not in message.replies[0]["text"].lower()
    assert "собрал новый план" in message.replies[0]["text"].lower()


def test_tickets_command_returns_travelpayouts_snapshot(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    handlers.flight_provider = FakeFlightProvider()
    handlers.service._flight_provider = handlers.flight_provider
    create_update, _ = make_update(chat_id=406)
    create_context = DummyContext(args=["Хочу", "из", "Томска", "в", "Казань", "на", "3", "дня"])
    asyncio.run(handlers.plan_command(create_update, create_context))

    tickets_update, tickets_message = make_update(chat_id=406)
    asyncio.run(handlers.tickets_command(tickets_update, DummyContext()))

    assert "Travelpayouts / Aviasales" in tickets_message.replies[-1]["text"]
    assert "12 300" in tickets_message.replies[-1]["text"]


def test_group_chat_with_origin_populates_ticket_snapshot(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    handlers.flight_provider = FakeFlightProvider()
    handlers.service._flight_provider = handlers.flight_provider
    context = DummyContext()

    update, message = make_update(
        text="Ребята, летим из Томска в Казань на 3 дня, нас четверо, бюджет средний",
        chat_id=518,
    )
    asyncio.run(handlers.handle_group_message(update, context))

    trip = database.get_active_trip(518)
    assert trip is not None
    assert trip["origin"] == "Томска"
    assert "Travelpayouts / Aviasales" in (trip.get("tickets_text") or "")
    assert "Билеты" in message.replies[-1]["text"]
    assert database.count_chat_members(518) == 1


def test_group_chat_analysis_uses_recent_messages_context(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    context = DummyContext()

    update1, _ = make_update(text="Ребята, давайте летом куда-нибудь съездим", chat_id=515)
    asyncio.run(handlers.handle_group_message(update1, context))
    assert database.get_active_trip(515) is None

    update2, message2 = make_update(text="Я бы в Казань на 3 дня, нас будет четверо", chat_id=515)
    asyncio.run(handlers.handle_group_message(update2, context))

    trip = database.get_active_trip(515)
    assert trip is not None
    assert trip["destination"] == "Казань"
    assert trip["links_text"]
    assert trip["flight_results"] is not None
    assert trip["housing_results"] is not None
    assert "aviasales" in trip["links_text"].lower()
    assert "ostrovok" in trip["links_text"].lower()
    assert "собрал черновик поездки" in message2.replies[-1]["text"].lower()


def test_summary_only_shows_detected_categories(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    context = DummyContext()

    update, _ = make_update(
        text="Летим из Томска в Казань, нужен отель и экскурсии, машину не надо",
        chat_id=519,
    )
    asyncio.run(handlers.handle_group_message(update, context))

    trip = database.get_active_trip(519)
    assert trip is not None

    summary_update, summary_message = make_update(chat_id=519)
    asyncio.run(handlers.summary_command(summary_update, DummyContext()))

    rendered = summary_message.replies[-1]["text"]
    assert "Билеты" in rendered
    assert "Жильё" in rendered
    assert "Экскурсии" in rendered
    assert "Открытые вопросы" in rendered
    assert "Готовность плана" in rendered


def test_summary_shows_full_multiday_itinerary(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    create_update, _ = make_update(chat_id=520)
    create_context = DummyContext(args=["Хочу", "в", "Казань", "на", "5", "дней"])
    asyncio.run(handlers.plan_command(create_update, create_context))

    summary_update, summary_message = make_update(chat_id=520)
    asyncio.run(handlers.summary_command(summary_update, DummyContext()))

    rendered = summary_message.replies[-1]["text"]
    assert "День 1." not in rendered
    assert "Открывается отдельной кнопкой ниже." in rendered


def test_summary_hides_stale_links_and_context_for_invalid_destination(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    trip_id = database.create_trip(
        chat_id=521,
        created_by=1,
        payload={
            "title": "- • 7 дн.",
            "destination": "-",
            "origin": "не указано",
            "dates_text": "не указаны",
            "days_count": 7,
            "group_size": 1,
            "budget_text": "эконом",
            "interests_text": "геи",
            "notes": "",
            "source_prompt": "",
            "context_text": "• Направление: Санкт-Петербург, Россия",
            "itinerary_text": "День 1. Старый маршрут",
            "logistics_text": "",
            "stay_text": "• Базовый выбор: центр",
            "alternatives_text": "",
            "budget_breakdown_text": "",
            "budget_total_text": "26 600 ₽ – 55 400 ₽",
            "housing_results": '[{\"title\":\"Жильё и размещение: 🏨 Островок\",\"price_text\":\"Откройте ссылку, чтобы увидеть актуальные варианты и цены.\",\"url\":\"https://ostrovok.ru/hotel/search/?q=-\",\"source\":\"Островок\",\"note\":\"Подобрано из обсуждения в чате.\"}]',
            "status": "active",
        },
    )
    database.set_selected_trip(521, trip_id)

    summary_update, summary_message = make_update(chat_id=521)
    asyncio.run(handlers.summary_command(summary_update, DummyContext()))

    rendered = summary_message.replies[-1]["text"]
    assert "Санкт-Петербург, Россия" not in rendered
    assert "https://ostrovok.ru/hotel/search/?q=-" not in rendered
    assert "Куда: -" not in rendered
    assert "Открывается отдельной кнопкой ниже." in rendered


def test_group_chat_without_destination_asks_short_question(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    context = DummyContext()

    update, message = make_update(text="Ребята, давайте летом куда-нибудь съездим на несколько дней", chat_id=516)
    asyncio.run(handlers.handle_group_message(update, context))

    assert database.get_active_trip(516) is None
    assert "Куда хотите поехать" in message.replies[-1]["text"]


def test_group_chat_updates_existing_trip_without_creating_new_one(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    context = DummyContext()

    create_update, _ = make_update(text="Ребята, поедем в Казань на 3 дня", chat_id=517)
    asyncio.run(handlers.handle_group_message(create_update, context))

    trip = database.get_active_trip(517)
    assert trip is not None
    original_trip_id = int(trip["id"])

    update_message, message = make_update(text="Давайте тогда 12–14 июня и бюджет комфорт", chat_id=517)
    asyncio.run(handlers.handle_group_message(update_message, context))

    trips = database.list_trips(517)
    active_trip = database.get_active_trip(517)
    assert len(trips) == 1
    assert active_trip is not None
    assert int(active_trip["id"]) == original_trip_id
    assert active_trip["dates_text"] == "12–14 июня"
    assert active_trip["budget_text"] == "Бизнес"
    assert "Быстрый вывод" in message.replies[-1]["text"]


def test_participants_progress_uses_known_chat_members(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)

    for user_id, first_name in [(1, "One"), (2, "Two"), (3, "Three"), (4, "Four"), (5, "Five")]:
        update, _ = make_update(
            text="Ребята, давайте в Казань на выходные",
            chat_id=7777,
            user_id=user_id,
            username=f"user{user_id}",
            first_name=first_name,
            last_name="Member",
        )
        asyncio.run(handlers.handle_group_message(update, DummyContext()))

    status_update, _ = make_update(chat_id=7777, user_id=1, username="user1", first_name="One", last_name="Member")
    asyncio.run(handlers.status_command(status_update, DummyContext(args=["еду"])))

    participants_update, participants_message = make_update(chat_id=7777)
    asyncio.run(handlers.participants_command(participants_update, DummyContext()))

    text = participants_message.replies[-1]["text"]
    assert "1/5" in text


def test_trips_and_select_trip_commands_restore_archived_trip(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)

    first_update, _ = make_update(chat_id=606)
    asyncio.run(handlers.plan_command(first_update, DummyContext(args=["Хочу", "в", "Казань", "на", "3", "дня"])))

    first_trip = database.get_active_trip(606)
    assert first_trip is not None

    second_update, _ = make_update(chat_id=606)
    asyncio.run(handlers.plan_command(second_update, DummyContext(args=["Хочу", "в", "Сочи", "на", "4", "дня"])))

    trips_update, trips_message = make_update(chat_id=606)
    asyncio.run(handlers.trips_command(trips_update, DummyContext()))
    assert "Поездки этого чата" in trips_message.replies[-1]["text"]
    assert str(first_trip["id"]) in trips_message.replies[-1]["text"]

    select_update, select_message = make_update(chat_id=606)
    asyncio.run(handlers.select_trip_command(select_update, DummyContext(args=[str(first_trip["id"])])))

    active_trip = database.get_active_trip(606)
    assert active_trip is not None
    assert active_trip["id"] == first_trip["id"]
    assert "снова активна" in select_message.replies[0]["text"]


def test_delete_trip_command_removes_archived_trip(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)

    first_update, _ = make_update(chat_id=607)
    asyncio.run(handlers.plan_command(first_update, DummyContext(args=["Хочу", "в", "Казань", "на", "3", "дня"])))

    first_trip = database.get_active_trip(607)
    assert first_trip is not None

    second_update, _ = make_update(chat_id=607)
    asyncio.run(handlers.plan_command(second_update, DummyContext(args=["Хочу", "в", "Сочи", "на", "4", "дня"])))

    delete_update, delete_message = make_update(chat_id=607)
    asyncio.run(handlers.delete_trip_command(delete_update, DummyContext(args=[str(first_trip["id"])])))

    assert database.get_trip_by_id(int(first_trip["id"])) is None
    assert "удалена" in delete_message.replies[-1]["text"].lower()


def test_summary_shows_entry_requirements_for_international_trip(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    trip_id = database.create_trip(
        chat_id=608,
        created_by=1,
        payload={
            "title": "Paris • 4 дн.",
            "destination": "Paris",
            "origin": "Berlin",
            "dates_text": "12-15 июня",
            "days_count": 4,
            "group_size": 2,
            "budget_text": "средний",
            "interests_text": "город, музеи",
            "notes": "",
            "source_prompt": "",
            "context_text": "Европейский city-break",
            "itinerary_text": "День 1. Прогулка",
            "logistics_text": "",
            "stay_text": "",
            "alternatives_text": "",
            "budget_breakdown_text": "",
            "budget_total_text": "нужна проверка цен в EUR",
            "entry_requirements_text": "Маршрут международный: Германия → Франция.",
            "open_questions_text": "• Уточнить гражданство или тип паспорта, чтобы проверить визовые и въездные правила.",
            "status": "active",
        },
    )
    database.set_selected_trip(608, trip_id)

    summary_update, summary_message = make_update(chat_id=608)
    asyncio.run(handlers.summary_command(summary_update, DummyContext()))

    rendered = summary_message.replies[-1]["text"]
    assert "Въезд и документы" not in rendered
    assert "тип паспорта" in rendered


def test_travelpayouts_detects_one_way_text() -> None:
    assert TravelpayoutsFlightProvider._is_one_way("Нужен билет в одну сторону", "12 июня") is True
    assert TravelpayoutsFlightProvider._is_one_way("Без обратного билета", "12 июня") is True
    assert TravelpayoutsFlightProvider._is_one_way("Туда 12 июня, обратно 18 июня", "12-18 июня") is False


def test_trip_action_buttons_open_route_tickets_and_housing(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    handlers.flight_provider = FakeFlightProvider()
    handlers.service._flight_provider = handlers.flight_provider
    create_update, _ = make_update(chat_id=1601)
    asyncio.run(
        handlers.plan_command(
            create_update,
            DummyContext(args=["Хочу", "в", "Казань", "из", "Томска", "на", "3", "дня", "нужен", "отель"]),
        )
    )
    trip = database.get_active_trip(1601)
    assert trip is not None

    for action, expected in [
        ("show_route", "Маршрут по дням"),
        ("show_tickets", "Билеты"),
        ("show_housing", "Жильё"),
    ]:
        callback_update, callback_message, query = make_callback_update(
            data=f"tripaction:{int(trip['id'])}:{action}",
            chat_id=1601,
        )
        asyncio.run(handlers.trip_action_callback(callback_update, DummyContext()))
        assert expected in callback_message.replies[-1]["text"]
        assert query.answers[-1]["text"] is not None


def test_create_trip_from_text_offloads_blocking_work_and_sends_typing(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    update, _ = make_update(
        text="Хочу в Казань на 3 дня, нас 2",
        chat_id=1801,
        chat_type="private",
    )
    stub_plan = handlers.planner.generate_plan(
        handlers.planner.build_request_from_fields(
            title="Казань • 3 дн.",
            destination="Казань",
            origin="Томск",
            dates_text="12–14 июня",
            days_count=3,
            group_size=2,
            budget_text="средний",
            interests_text="город, еда",
            notes="",
            source_prompt="Хочу в Казань",
            language_code="ru",
        )
    )
    stub_payload = {
        "title": "Казань • 3 дн.",
        "destination": "Казань",
        "origin": "Томск",
        "dates_text": "12–14 июня",
        "days_count": 3,
        "group_size": 2,
        "budget_text": "средний",
        "interests_text": "город, еда",
        "notes": "",
        "source_prompt": "Хочу в Казань",
        "context_text": stub_plan.context_text,
        "itinerary_text": stub_plan.itinerary_text,
        "logistics_text": stub_plan.logistics_text,
        "stay_text": stub_plan.stay_text,
        "alternatives_text": stub_plan.alternatives_text,
        "budget_breakdown_text": stub_plan.budget_breakdown_text,
        "budget_total_text": stub_plan.budget_total_text,
        "status": "active",
    }

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch("bot.handlers.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)) as to_thread_mock, patch.object(
        handlers.planner,
        "generate_plan",
        return_value=stub_plan,
    ), patch.object(
        handlers.service,
        "_build_trip_payload",
        return_value=stub_payload,
    ), patch.object(handlers.service, "_refresh_weather_for_trip", new=AsyncMock()) as refresh_mock:
        created = asyncio.run(handlers._create_trip_from_text(update, update.effective_message.text))

    assert created is True
    assert update.effective_chat.actions == ["typing"]
    assert to_thread_mock.await_count == 2
    refresh_mock.assert_awaited_once()
    assert database.get_active_trip(1801) is not None


def test_new_trip_notes_offloads_blocking_work(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    context = DummyContext()
    context.user_data["trip_draft"] = {
        "title": "Лето",
        "destination": "Владивосток",
        "origin": "Томск",
        "days_count": 5,
        "dates_text": "12–16 июня",
        "group_size": 4,
        "budget_text": "средний",
        "interests_text": "море, еда",
    }
    update, _ = make_update(text="купить билеты", chat_id=1802)
    stub_plan = handlers.planner.generate_plan(
        handlers.planner.build_request_from_fields(
            title="Лето",
            destination="Владивосток",
            origin="Томск",
            dates_text="12–16 июня",
            days_count=5,
            group_size=4,
            budget_text="средний",
            interests_text="море, еда",
            notes="купить билеты",
            source_prompt="Новый бриф: Владивосток, 5 дн.",
            language_code="ru",
        )
    )
    stub_payload = {
        "title": "Лето",
        "destination": "Владивосток",
        "origin": "Томск",
        "dates_text": "12–16 июня",
        "days_count": 5,
        "group_size": 4,
        "budget_text": "средний",
        "interests_text": "море, еда",
        "notes": "купить билеты",
        "source_prompt": "Новый бриф: Владивосток, 5 дн.",
        "context_text": stub_plan.context_text,
        "itinerary_text": stub_plan.itinerary_text,
        "logistics_text": stub_plan.logistics_text,
        "stay_text": stub_plan.stay_text,
        "alternatives_text": stub_plan.alternatives_text,
        "budget_breakdown_text": stub_plan.budget_breakdown_text,
        "budget_total_text": stub_plan.budget_total_text,
        "status": "active",
    }

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch("bot.handlers.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)) as to_thread_mock, patch.object(
        handlers.planner,
        "generate_plan",
        return_value=stub_plan,
    ), patch.object(
        handlers.service,
        "_build_trip_payload",
        return_value=stub_payload,
    ), patch.object(handlers.service, "_refresh_weather_for_trip", new=AsyncMock()) as refresh_mock:
        result = asyncio.run(handlers.new_trip_notes(update, context))

    assert result is not None
    assert to_thread_mock.await_count == 2
    refresh_mock.assert_awaited_once()
    assert database.get_active_trip(1802) is not None


def test_handle_trip_edit_input_offloads_blocking_work(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    create_update, _ = make_update(chat_id=1803)
    asyncio.run(handlers.plan_command(create_update, DummyContext(args=["Хочу", "в", "Казань", "на", "3", "дня"])))
    trip = database.get_active_trip(1803)
    assert trip is not None

    context = DummyContext()
    context.user_data["edit_trip_id"] = int(trip["id"])
    update, _ = make_update(text="сделай 4 дня", chat_id=1803)
    stub_plan = handlers.planner.generate_plan(
        handlers.service._merge_edit_request(trip, "сделай 4 дня")
    )
    stub_payload = {
        "title": trip["title"],
        "destination": trip["destination"],
        "origin": trip["origin"],
        "dates_text": trip["dates_text"],
        "days_count": 4,
        "group_size": int(trip["group_size"]),
        "budget_text": trip["budget_text"],
        "interests_text": trip["interests_text"],
        "notes": trip["notes"] or "",
        "source_prompt": trip["source_prompt"] or "",
        "context_text": stub_plan.context_text,
        "itinerary_text": stub_plan.itinerary_text,
        "logistics_text": stub_plan.logistics_text,
        "stay_text": stub_plan.stay_text,
        "alternatives_text": stub_plan.alternatives_text,
        "budget_breakdown_text": stub_plan.budget_breakdown_text,
        "budget_total_text": stub_plan.budget_total_text,
    }

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch("bot.handlers.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)) as to_thread_mock, patch.object(
        handlers.planner,
        "generate_plan",
        return_value=stub_plan,
    ), patch.object(
        handlers.service,
        "_build_trip_payload",
        return_value=stub_payload,
    ), patch.object(handlers.service, "_refresh_weather_for_trip", new=AsyncMock()) as refresh_mock:
        asyncio.run(handlers.handle_trip_edit_input(update, context))

    assert to_thread_mock.await_count == 2
    refresh_mock.assert_awaited_once()


def test_create_trip_from_text_uses_async_llm_planner_path(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    handlers.planner = LLMTravelPlanner(
        LLMProviderPool(
            [
                LLMProvider(
                    name="Groq",
                    daily_limit=14400,
                    api_key="groq-key",
                    base_url="https://api.groq.com/openai/v1/chat/completions",
                    model="llama-3.3-70b-versatile",
                )
            ]
        )
    )
    handlers.service._planner = handlers.planner
    update, message = make_update(
        text="Хочу в Казань на 3 дня, нас 2",
        chat_id=1810,
        chat_type="private",
    )
    request = handlers.planner.parse_trip_request(update.effective_message.text, language_code="ru")
    stub_plan = TravelPlanner().generate_plan(request)
    stub_payload = {
        "title": request.title,
        "destination": request.destination,
        "origin": request.origin,
        "dates_text": request.dates_text,
        "days_count": request.days_count,
        "group_size": request.group_size,
        "budget_text": request.budget_text,
        "interests_text": request.interests_text,
        "notes": "",
        "source_prompt": request.source_prompt,
        "context_text": stub_plan.context_text,
        "itinerary_text": stub_plan.itinerary_text,
        "logistics_text": stub_plan.logistics_text,
        "stay_text": stub_plan.stay_text,
        "alternatives_text": stub_plan.alternatives_text,
        "budget_breakdown_text": stub_plan.budget_breakdown_text,
        "budget_total_text": stub_plan.budget_total_text,
        "status": "active",
    }

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch.object(
        handlers.planner,
        "generate_plan_async",
        new=AsyncMock(return_value=stub_plan),
    ) as llm_async_mock, patch(
        "bot.handlers.asyncio.to_thread",
        new=AsyncMock(side_effect=fake_to_thread),
    ) as to_thread_mock, patch.object(
        handlers.service,
        "_build_trip_payload",
        return_value=stub_payload,
    ), patch.object(handlers.service, "_refresh_weather_for_trip", new=AsyncMock()) as refresh_mock:
        created = asyncio.run(handlers._create_trip_from_text(update, update.effective_message.text))

    assert created is True
    llm_async_mock.assert_awaited_once()
    assert to_thread_mock.await_count == 1
    refresh_mock.assert_awaited_once()
    assert len(message.replies) == 1
    assert "🧭" in str(message.replies[0]["text"])
    assert "Thinking over the trip with AI" not in str(message.replies[0]["text"])


def test_new_trip_notes_uses_async_llm_planner_path(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    handlers.planner = LLMTravelPlanner(
        LLMProviderPool(
            [
                LLMProvider(
                    name="Gemini",
                    daily_limit=1500,
                    api_key="gemini-key",
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    model="gemini-2.0-flash",
                    use_web_search=True,
                )
            ]
        )
    )
    handlers.service._planner = handlers.planner
    context = DummyContext()
    context.user_data["trip_draft"] = {
        "title": "Лето",
        "destination": "Владивосток",
        "origin": "Томск",
        "days_count": 5,
        "dates_text": "12–16 июня",
        "group_size": 4,
        "budget_text": "средний",
        "interests_text": "море, еда",
    }
    update, _ = make_update(text="купить билеты", chat_id=1811)
    request = handlers.planner.build_request_from_fields(
        title="Лето",
        destination="Владивосток",
        origin="Томск",
        dates_text="12–16 июня",
        days_count=5,
        group_size=4,
        budget_text="средний",
        interests_text="море, еда",
        notes="купить билеты",
        source_prompt="Новый бриф: Владивосток, 5 дн.",
        language_code="ru",
    )
    stub_plan = TravelPlanner().generate_plan(request)
    stub_payload = {
        "title": request.title,
        "destination": request.destination,
        "origin": request.origin,
        "dates_text": request.dates_text,
        "days_count": request.days_count,
        "group_size": request.group_size,
        "budget_text": request.budget_text,
        "interests_text": request.interests_text,
        "notes": request.notes,
        "source_prompt": request.source_prompt,
        "context_text": stub_plan.context_text,
        "itinerary_text": stub_plan.itinerary_text,
        "logistics_text": stub_plan.logistics_text,
        "stay_text": stub_plan.stay_text,
        "alternatives_text": stub_plan.alternatives_text,
        "budget_breakdown_text": stub_plan.budget_breakdown_text,
        "budget_total_text": stub_plan.budget_total_text,
        "status": "active",
    }

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch.object(
        handlers.planner,
        "generate_plan_async",
        new=AsyncMock(return_value=stub_plan),
    ) as llm_async_mock, patch(
        "bot.handlers.asyncio.to_thread",
        new=AsyncMock(side_effect=fake_to_thread),
    ) as to_thread_mock, patch.object(
        handlers.service,
        "_build_trip_payload",
        return_value=stub_payload,
    ), patch.object(handlers.service, "_refresh_weather_for_trip", new=AsyncMock()) as refresh_mock:
        result = asyncio.run(handlers.new_trip_notes(update, context))

    assert result is not None
    llm_async_mock.assert_awaited_once()
    assert to_thread_mock.await_count == 1
    refresh_mock.assert_awaited_once()


def test_handle_trip_edit_input_uses_async_llm_planner_path(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    create_update, _ = make_update(chat_id=1812)
    asyncio.run(handlers.plan_command(create_update, DummyContext(args=["Хочу", "в", "Казань", "на", "3", "дня"])))
    trip = database.get_active_trip(1812)
    assert trip is not None

    handlers.planner = LLMTravelPlanner(
        LLMProviderPool(
            [
                LLMProvider(
                    name="OpenRouter",
                    daily_limit=500,
                    api_key="openrouter-key",
                    base_url="https://openrouter.ai/api/v1/chat/completions",
                    model="google/gemini-2.0-flash-exp:free",
                    use_web_search=True,
                )
            ]
        )
    )
    handlers.service._planner = handlers.planner
    context = DummyContext()
    context.user_data["edit_trip_id"] = int(trip["id"])
    update, _ = make_update(text="сделай 4 дня", chat_id=1812)
    merged_request = handlers.service._merge_edit_request(trip, "сделай 4 дня")
    stub_plan = TravelPlanner().generate_plan(merged_request)
    stub_payload = {
        "title": merged_request.title,
        "destination": merged_request.destination,
        "origin": merged_request.origin,
        "dates_text": merged_request.dates_text,
        "days_count": merged_request.days_count,
        "group_size": merged_request.group_size,
        "budget_text": merged_request.budget_text,
        "interests_text": merged_request.interests_text,
        "notes": merged_request.notes,
        "source_prompt": merged_request.source_prompt,
        "context_text": stub_plan.context_text,
        "itinerary_text": stub_plan.itinerary_text,
        "logistics_text": stub_plan.logistics_text,
        "stay_text": stub_plan.stay_text,
        "alternatives_text": stub_plan.alternatives_text,
        "budget_breakdown_text": stub_plan.budget_breakdown_text,
        "budget_total_text": stub_plan.budget_total_text,
    }

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch.object(
        handlers.planner,
        "generate_plan_async",
        new=AsyncMock(return_value=stub_plan),
    ) as llm_async_mock, patch(
        "bot.handlers.asyncio.to_thread",
        new=AsyncMock(side_effect=fake_to_thread),
    ) as to_thread_mock, patch.object(
        handlers.service,
        "_build_trip_payload",
        return_value=stub_payload,
    ), patch.object(handlers.service, "_refresh_weather_for_trip", new=AsyncMock()) as refresh_mock:
        asyncio.run(handlers.handle_trip_edit_input(update, context))

    llm_async_mock.assert_awaited_once()
    assert to_thread_mock.await_count == 1
    refresh_mock.assert_awaited_once()


def test_summary_command_awaits_weather_refresh(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    create_update, _ = make_update(chat_id=1804)
    asyncio.run(handlers.plan_command(create_update, DummyContext(args=["Хочу", "в", "Казань", "на", "3", "дня"])))
    trip = database.get_active_trip(1804)
    assert trip is not None

    summary_update, _ = make_update(chat_id=1804)
    with patch.object(handlers.service, "_refresh_weather_for_trip", new=AsyncMock()) as refresh_mock:
        asyncio.run(handlers.summary_command(summary_update, DummyContext()))

    refresh_mock.assert_awaited_once_with(int(trip["id"]))


def test_handle_group_message_awaits_async_service_helpers(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    create_update, _ = make_update(text="Ребята, поедем в Казань на 3 дня", chat_id=1805)
    asyncio.run(handlers.handle_group_message(create_update, DummyContext()))
    active_trip = database.get_active_trip(1805)
    assert active_trip is not None

    update_message, _ = make_update(text="Давайте тогда 12–14 июня и бюджет комфорт", chat_id=1805)
    with patch.object(handlers.service, "_rebuild_trip", new=AsyncMock()) as rebuild_mock, patch.object(
        handlers.service,
        "_refresh_weather_for_trip",
        new=AsyncMock(),
    ) as refresh_mock:
        asyncio.run(handlers.handle_group_message(update_message, DummyContext()))

    rebuild_mock.assert_awaited_once_with(int(active_trip["id"]))
    refresh_mock.assert_awaited_once_with(int(active_trip["id"]))


def test_handle_group_message_reports_rebuild_failure_to_user(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    trip_id = database.create_trip(
        chat_id=1805,
        created_by=1,
        payload={
            "title": "Казань • 3 дн.",
            "destination": "Казань",
            "origin": "Томск",
            "dates_text": "не указаны",
            "days_count": 3,
            "group_size": 2,
            "budget_text": "средний",
            "interests_text": "еда",
            "notes": "",
            "source_prompt": "",
            "detected_needs": "",
            "status": "active",
        },
    )
    signal = SimpleNamespace(
        has_travel_intent=True,
        destination="Казань",
        destination_votes=[],
        origin=None,
        dates_text="12–14 июня",
        budget_hint="комфорт",
        interests=[],
        detected_needs=[],
        raw_text="Давайте тогда 12–14 июня и бюджет комфорт",
    )

    update_message, message = make_update(text="Давайте тогда 12–14 июня и бюджет комфорт", chat_id=1805)
    with patch.object(
        handlers.service,
        "_rebuild_trip",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ) as rebuild_mock, patch.object(
        handlers.service,
        "_refresh_weather_for_trip",
        new=AsyncMock(),
    ) as refresh_mock, patch("bot.handlers.logger.exception") as logger_mock, patch(
        "bot.group_chat_analyzer.GroupChatAnalyzer"
    ) as analyzer_cls:
        analyzer_cls.return_value.analyze_messages.return_value = signal
        asyncio.run(handlers.handle_group_message(update_message, DummyContext()))

    rebuild_mock.assert_awaited_once_with(trip_id)
    refresh_mock.assert_not_awaited()
    logger_mock.assert_called_once()
    assert "Не удалось обновить поездку автоматически" in message.replies[-1]["text"]


def test_handle_group_message_reports_weather_refresh_failure_to_user(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    trip_id = database.create_trip(
        chat_id=1805,
        created_by=1,
        payload={
            "title": "Казань • 3 дн.",
            "destination": "Казань",
            "origin": "Томск",
            "dates_text": "не указаны",
            "days_count": 3,
            "group_size": 2,
            "budget_text": "средний",
            "interests_text": "еда",
            "notes": "",
            "source_prompt": "",
            "detected_needs": "",
            "status": "active",
        },
    )
    signal = SimpleNamespace(
        has_travel_intent=True,
        destination="Казань",
        destination_votes=[],
        origin=None,
        dates_text="12–14 июня",
        budget_hint="комфорт",
        interests=[],
        detected_needs=[],
        raw_text="Давайте тогда 12–14 июня и бюджет комфорт",
    )

    update_message, message = make_update(text="Давайте тогда 12–14 июня и бюджет комфорт", chat_id=1805)
    with patch.object(
        handlers.service,
        "_rebuild_trip",
        new=AsyncMock(),
    ) as rebuild_mock, patch.object(
        handlers.service,
        "_refresh_weather_for_trip",
        new=AsyncMock(side_effect=RuntimeError("weather")),
    ) as refresh_mock, patch("bot.handlers.logger.exception") as logger_mock, patch(
        "bot.group_chat_analyzer.GroupChatAnalyzer"
    ) as analyzer_cls:
        analyzer_cls.return_value.analyze_messages.return_value = signal
        asyncio.run(handlers.handle_group_message(update_message, DummyContext()))

    rebuild_mock.assert_awaited_once_with(trip_id)
    refresh_mock.assert_awaited_once_with(trip_id)
    logger_mock.assert_called_once()
    assert "Не удалось обновить поездку автоматически" in message.replies[-1]["text"]


def test_handle_group_message_awaits_auto_draft_from_signal(tmp_path) -> None:
    database, handlers = build_handlers(tmp_path)
    trip_id = database.create_trip(
        chat_id=1806,
        created_by=1,
        payload={
            "title": "Временная поездка",
            "destination": "Сочи",
            "origin": "Томск",
            "dates_text": "не указаны",
            "days_count": 3,
            "group_size": 2,
            "budget_text": "средний",
            "interests_text": "море",
            "notes": "",
            "source_prompt": "",
            "status": "active",
        },
    )
    database.archive_active_trip(1806)
    database.set_selected_trip(1806, None)

    update, _ = make_update(text="Ребята, поедем в Казань на 3 дня", chat_id=1806)
    with patch.object(handlers.service, "auto_draft_from_signal", new=AsyncMock(return_value=trip_id)) as autodraft_mock:
        asyncio.run(handlers.handle_group_message(update, DummyContext()))

    autodraft_mock.assert_awaited_once()


def test_handle_group_message_cooldown_allows_only_one_reply_under_concurrency(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    context = DummyContext()
    update1, message1 = make_update(text="Ребята, куда поедем в июле на пару дней?", chat_id=1807)
    update2, message2 = make_update(text="Ребята, куда поедем в июле на пару дней?", chat_id=1807)
    signal = SimpleNamespace(
        has_travel_intent=True,
        destination=None,
        destination_votes=[("Казань", 2)],
        origin=None,
        dates_text=None,
        budget_hint=None,
        interests=[],
        detected_needs=[],
        raw_text="Голосуем за направление",
    )

    async def run_test() -> None:
        with patch("bot.group_chat_analyzer.GroupChatAnalyzer") as analyzer_cls:
            analyzer = analyzer_cls.return_value
            analyzer.analyze_messages.return_value = signal
            await asyncio.gather(
                handlers.handle_group_message(update1, context),
                handlers.handle_group_message(update2, context),
            )

    asyncio.run(run_test())

    reply_count = len(message1.replies) + len(message2.replies)
    assert reply_count == 1


def test_handle_group_message_keeps_both_recent_messages_under_concurrency(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    context = DummyContext()
    update1, _ = make_update(text="Ребята, давайте летом куда-нибудь уедем на выходные", chat_id=1808)
    update2, _ = make_update(text="Может тогда в Казань с пятницы по воскресенье", chat_id=1808)
    signal = SimpleNamespace(
        has_travel_intent=False,
        destination=None,
        destination_votes=[],
        origin=None,
        dates_text=None,
        budget_hint=None,
        interests=[],
        detected_needs=[],
        raw_text="",
    )

    async def run_test() -> None:
        with patch("bot.group_chat_analyzer.GroupChatAnalyzer") as analyzer_cls:
            analyzer = analyzer_cls.return_value
            analyzer.analyze_messages.return_value = signal
            await asyncio.gather(
                handlers.handle_group_message(update1, context),
                handlers.handle_group_message(update2, context),
            )

    asyncio.run(run_test())

    recent_messages = context.chat_data["recent_group_messages"]
    assert len(recent_messages) == 2
    assert update1.effective_message.text in recent_messages
    assert update2.effective_message.text in recent_messages


def test_select_trip_command_without_args_returns_usage_message(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    update, message = make_update(chat_id=1810)

    asyncio.run(handlers.select_trip_command(update, DummyContext()))

    assert message.replies[-1]["text"] == "Использование: /select_trip 12"


def test_delete_trip_command_without_args_returns_usage_message(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    update, message = make_update(chat_id=1811)

    asyncio.run(handlers.delete_trip_command(update, DummyContext()))

    assert message.replies[-1]["text"] == "Использование: /delete_trip 12"


def test_create_trip_from_text_uses_extraction_missing_fields_for_followup(tmp_path) -> None:
    _, handlers = build_handlers(tmp_path)
    update, message = make_update(
        text="Хочу в Стамбул 12 июня, нужен билет, бюджет Бизнес",
        chat_id=1812,
        chat_type="private",
    )
    context = DummyContext()
    extraction = TripRequestExtraction(
        destination="Стамбул",
        origin=None,
        dates_text="12 июня",
        days_count=None,
        group_size=1,
        budget_text="Бизнес",
        interests=[],
        needs=["tickets"],
        route_type="unknown",
        notes="",
        language_code="ru",
        missing_fields=["origin", "route_type"],
        is_actionable=False,
    )

    with patch.object(handlers.request_extractor, "extract_async", new=AsyncMock(return_value=extraction)):
        created = asyncio.run(handlers._create_trip_from_text(update, update.effective_message.text, context))

    assert created is True
    assert "plan_followup:1" in context.chat_data
    assert context.chat_data["plan_followup:1"]["fields"] == ["origin", "route_type"]
    assert "вылет" in message.replies[-1]["text"].lower()
