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
            InlineKeyboardButton(text="✅ Еду", callback_data=f"tripaction:{trip_id}:going"),
            InlineKeyboardButton(text="🤔 Думаю", callback_data=f"tripaction:{trip_id}:interested"),
            InlineKeyboardButton(text="❌ Не еду", callback_data=f"tripaction:{trip_id}:not_going"),
        ],
        [
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"tripaction:{trip_id}:edit"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"tripaction:{trip_id}:delete_confirm"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def trip_delete_confirm_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="🗑 Да, удалить навсегда", callback_data=f"tripaction:{trip_id}:delete_now"),
            ],
            [
                InlineKeyboardButton(text="↩️ Отмена", callback_data=f"tripaction:{trip_id}:delete_cancel"),
            ],
        ]
    )


def trips_list_keyboard(trips: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for trip in trips[:10]:
        trip_id = int(trip["id"])
        rows.append(
            [
                InlineKeyboardButton(text=f"📂 Открыть {trip_id}", callback_data=f"tripaction:{trip_id}:open_trip"),
                InlineKeyboardButton(text=f"🗑 Удалить {trip_id}", callback_data=f"tripaction:{trip_id}:delete_confirm"),
            ]
        )
    return InlineKeyboardMarkup(rows)



def settings_keyboard(reminders_enabled: bool, autodraft_enabled: bool) -> InlineKeyboardMarkup:
    reminders_label = "🔔 Напоминания: вкл" if reminders_enabled else "🔕 Напоминания: выкл"
    autodraft_label = "🧠 Авто-черновики: вкл" if autodraft_enabled else "🛑 Авто-черновики: выкл"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text=reminders_label, callback_data="settings:toggle_reminders")],
            [InlineKeyboardButton(text=autodraft_label, callback_data="settings:toggle_autodraft")],
        ]
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
