from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


STATUS_LABELS = {
    "interested": "🤔 Интересно",
    "going": "✅ Еду",
    "not_going": "❌ Не еду",
}


def participant_status_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"participant:{trip_id}:{status}",
            )
        ]
        for status, label in STATUS_LABELS.items()
    ]
    return InlineKeyboardMarkup(buttons)



def settings_keyboard(reminders_enabled: bool) -> InlineKeyboardMarkup:
    label = "🔔 Напоминания: вкл" if reminders_enabled else "🔕 Напоминания: выкл"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=label, callback_data="settings:toggle_reminders")]]
    )



def date_vote_keyboard(option_id: int, votes: int) -> InlineKeyboardMarkup:
    label = f"🗳 Голосовать ({votes})"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=label, callback_data=f"datevote:{option_id}")]]
    )


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


def trip_budget_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(text="эконом"), KeyboardButton(text="средний"), KeyboardButton(text="комфорт")],
            [KeyboardButton(text="до 50 000"), KeyboardButton(text="до 80 000")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Выберите бюджет",
    )


def trip_skip_keyboard(skip_text: str = "-") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(text=skip_text)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Можно пропустить",
    )
