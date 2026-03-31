from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from i18n import tr


STATUS_LABELS = {
    "interested": {"ru": "🤔 Думаю", "en": "🤔 Thinking"},
    "going": {"ru": "✅ Еду", "en": "✅ Going"},
    "not_going": {"ru": "❌ Не еду", "en": "❌ Not going"},
}


def trip_summary_keyboard(trip_id: int, language_code: str = "ru") -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text=tr(language_code, "button_status_going"), callback_data=f"tripaction:{trip_id}:going"),
            InlineKeyboardButton(text=tr(language_code, "button_status_interested"), callback_data=f"tripaction:{trip_id}:interested"),
            InlineKeyboardButton(text=tr(language_code, "button_status_not_going"), callback_data=f"tripaction:{trip_id}:not_going"),
        ],
        [
            InlineKeyboardButton(text=tr(language_code, "button_route"), callback_data=f"tripaction:{trip_id}:show_route"),
        ],
        [
            InlineKeyboardButton(text=tr(language_code, "button_edit"), callback_data=f"tripaction:{trip_id}:edit"),
            InlineKeyboardButton(text=tr(language_code, "button_delete"), callback_data=f"tripaction:{trip_id}:delete_confirm"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def participant_status_keyboard(trip_id: int, language_code: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(text=tr(language_code, "button_status_going"), callback_data=f"tripaction:{trip_id}:going"),
            InlineKeyboardButton(text=tr(language_code, "button_status_interested"), callback_data=f"tripaction:{trip_id}:interested"),
            InlineKeyboardButton(text=tr(language_code, "button_status_not_going"), callback_data=f"tripaction:{trip_id}:not_going"),
        ]]
    )


def route_section_keyboard(trip_id: int, language_code: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=tr(language_code, "button_back_to_card"), callback_data=f"tripaction:{trip_id}:show_summary")]]
    )


def trip_delete_confirm_keyboard(trip_id: int, language_code: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text=tr(language_code, "button_delete_confirm"), callback_data=f"tripaction:{trip_id}:delete_now")],
            [InlineKeyboardButton(text=tr(language_code, "button_cancel"), callback_data=f"tripaction:{trip_id}:delete_cancel")],
        ]
    )


def trips_list_keyboard(trips: list[dict], language_code: str = "ru") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for trip in trips[:10]:
        trip_id = int(trip["id"])
        rows.append(
            [
                InlineKeyboardButton(text=tr(language_code, "button_open", trip_id=trip_id), callback_data=f"tripaction:{trip_id}:open_trip"),
                InlineKeyboardButton(text=tr(language_code, "button_delete_trip", trip_id=trip_id), callback_data=f"tripaction:{trip_id}:delete_confirm"),
            ]
        )
    return InlineKeyboardMarkup(rows)


def settings_keyboard(reminders_enabled: bool, autodraft_enabled: bool, language_code: str = "ru") -> InlineKeyboardMarkup:
    reminders_label = tr(language_code, "settings_button_reminders_on") if reminders_enabled else tr(language_code, "settings_button_reminders_off")
    autodraft_label = tr(language_code, "settings_button_autodraft_on") if autodraft_enabled else tr(language_code, "settings_button_autodraft_off")
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text=reminders_label, callback_data="settings:toggle_reminders")],
            [InlineKeyboardButton(text=autodraft_label, callback_data="settings:toggle_autodraft")],
            [InlineKeyboardButton(text=tr(language_code, "settings_button_language", language=tr(language_code, "language_name")), callback_data="settings:show_language")],
        ]
    )


def language_keyboard(current_language: str | None = None) -> InlineKeyboardMarkup:
    current = "en" if current_language == "en" else "ru"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text=(("• " if current == "ru" else "") + tr("ru", "lang_button_ru")), callback_data="language:set:ru"),
                InlineKeyboardButton(text=(("• " if current == "en" else "") + tr("en", "lang_button_en")), callback_data="language:set:en"),
            ]
        ]
    )


def date_vote_keyboard(option_id: int, votes: int) -> InlineKeyboardMarkup:
    label = f"🗳 Голосовать ({votes})"
    return InlineKeyboardMarkup([[InlineKeyboardButton(text=label, callback_data=f"datevote:{option_id}")]])


def trip_days_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(text="2"), KeyboardButton(text="3"), KeyboardButton(text="4")],
            [KeyboardButton(text="5"), KeyboardButton(text="7"), KeyboardButton(text="10")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Выберите длительность",
    )


def trip_group_size_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3")],
            [KeyboardButton(text="4"), KeyboardButton(text="5"), KeyboardButton(text="6")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Сколько человек едет",
    )


def trip_budget_keyboard(language_code: str = "ru") -> ReplyKeyboardMarkup:
    if language_code == "en":
        first_row = [KeyboardButton(text="Economy"), KeyboardButton(text="Business"), KeyboardButton(text="First Class")]
        second_row = [KeyboardButton(text="up to 50 000"), KeyboardButton(text="around 50 000"), KeyboardButton(text="from 50 000")]
        placeholder = "Choose budget"
    else:
        first_row = [KeyboardButton(text="Эконом"), KeyboardButton(text="Бизнес"), KeyboardButton(text="Первый класс")]
        second_row = [KeyboardButton(text="до 50 000"), KeyboardButton(text="на 50 000"), KeyboardButton(text="от 50 000")]
        placeholder = "Выберите бюджет"
    return ReplyKeyboardMarkup(
        [first_row, second_row],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder=placeholder,
    )


def trip_skip_keyboard(skip_text: str = "-") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(text=skip_text)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Можно пропустить",
    )
