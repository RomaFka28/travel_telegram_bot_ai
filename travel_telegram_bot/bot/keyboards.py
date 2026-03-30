from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


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
