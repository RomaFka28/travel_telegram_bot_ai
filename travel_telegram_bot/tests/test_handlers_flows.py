import asyncio
from types import SimpleNamespace

from bot.formatters import TripFormatter
from bot.handlers import BotHandlers
from bot.trip_service import TripService
from database import Database
from housing_search import LinkOnlyHousingSearchProvider
from travel_planner import TravelPlanner
from travel_result_models import TravelSearchResult


class DummyMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[dict[str, object]] = []

    async def reply_text(self, text: str, parse_mode=None, reply_markup=None) -> None:
        self.replies.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )


class DummyCallbackQuery:
    def __init__(self, data: str, user, message: DummyMessage) -> None:
        self.data = data
        self.from_user = user
        self.message = message
        self.answers: list[dict[str, object]] = []
        self.edits: list[dict[str, object]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append({"text": text, "show_alert": show_alert})

    async def edit_message_text(self, text: str, parse_mode=None, reply_markup=None) -> None:
        self.edits.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )


class DummyBot:
    username = "demo_trip_bot"


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
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
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
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
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

    def search_results(self, *, origin: str, destination: str, dates_text: str, budget_text: str, group_size: int) -> list[TravelSearchResult]:
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

    def build_ticket_snapshot(self, *, origin: str, destination: str, dates_text: str, budget_text: str, group_size: int) -> str:
        return (
            f"Travelpayouts / Aviasales: свежие цены для {origin} -> {destination}\n"
            f"1. 12 300 ₽/чел. (49 200 ₽ на {group_size} чел.), 2026-06-12 -> 2026-06-14, прямой, оценка 9/10, вписывается в средний бюджет"
        )


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
    assert "День 1." in rendered
    assert "День 2." in rendered
    assert "День 3." in rendered


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
    assert "Маршрут появится после того" in rendered


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
    assert active_trip["budget_text"] == "комфорт"
    assert "Откройте /summary" in message.replies[-1]["text"]


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
    assert "удалена навсегда" in delete_message.replies[-1]["text"].lower()
