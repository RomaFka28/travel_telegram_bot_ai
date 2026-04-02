from __future__ import annotations

import html

from bot.keyboards import STATUS_LABELS
from database import Database
from housing_search import HousingSearchResponse
from i18n import tr
from travel_result_models import deserialize_needs, deserialize_results
from value_normalization import normalized_search_value


class TripFormatter:
    def __init__(self, database: Database) -> None:
        self._db = database

    def _chat_language(self, chat_id: int | None) -> str:
        if chat_id is None:
            return "ru"
        return self._db.get_chat_language(int(chat_id))

    def _trip_language(self, trip: dict | None) -> str:
        if not trip:
            return "ru"
        return self._chat_language(int(trip.get("chat_id") or 0))

    def _participant_lines(self, trip_id: int) -> list[str]:
        trip = self._db.get_trip_by_id(trip_id)
        lang = self._trip_language(trip)
        participants = self._db.list_participants(trip_id)
        known_members = self._db.count_chat_members(int(trip["chat_id"])) if trip else 0
        responded = len(participants)
        if not participants:
            if known_members:
                return [tr(lang, "participants_none_progress", known=known_members)]
            return [tr(lang, "participants_none")]

        labels = {
            "going": tr(lang, "participants_going"),
            "interested": tr(lang, "participants_interested"),
            "not_going": tr(lang, "participants_not_going"),
        }
        lines: list[str] = [tr(lang, "participants_progress", responded=responded, known=max(known_members, responded))]
        for status in ("going", "interested", "not_going"):
            names = [participant["full_name"] for participant in participants if participant["status"] == status]
            if names:
                lines.append(f"{labels[status]} ({len(names)}): {html.escape(', '.join(names))}")
            else:
                lines.append(f"{labels[status]} (0): —")
        return lines

    def _date_lines(self, trip_id: int) -> list[str]:
        trip = self._db.get_trip_by_id(trip_id)
        lang = self._trip_language(trip)
        date_options = self._db.list_date_options(trip_id)
        return [
            f"• {html.escape(option['label'])} — <b>{option['votes']}</b> голос(ов)"
            for option in date_options
        ] or [tr(lang, "date_options_empty")]

    @staticmethod
    def _preview_multiline(text: str, *, max_blocks: int) -> str:
        blocks = [block.strip() for block in (text or "").split("\n\n") if block.strip()]
        if not blocks:
            return "—"
        return "\n\n".join(blocks[:max_blocks])

    @staticmethod
    def _escape_block(text: str) -> str:
        return html.escape(text or "—")

    @staticmethod
    def _category_title(key: str, language_code: str = "ru") -> str:
        labels = {
            "ru": {
                "flight_results": "Билеты",
                "housing_results": "Жильё",
                "activity_results": "Экскурсии",
                "transport_results": "Дорога",
                "rental_results": "Аренда",
            },
            "en": {
                "flight_results": "Tickets",
                "housing_results": "Housing",
                "activity_results": "Activities",
                "transport_results": "Transport",
                "rental_results": "Rental",
            },
        }
        lang = "en" if language_code == "en" else "ru"
        return labels[lang].get(key, key)

    @staticmethod
    def _clean_result_title(result_title: str, category_title: str) -> str:
        raw = (result_title or "").strip()
        prefix = f"{category_title}:"
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
        return raw or category_title

    @staticmethod
    def _display_result_hint(result) -> str:
        price_text = (result.price_text or "").strip()
        if not price_text:
            return ""
        lowered = price_text.lower()
        if "откройте ссылку" in lowered:
            return "Актуальные варианты и цены по ссылке"
        if "open options" in lowered:
            return "Open options via link"
        return price_text

    def _category_section(self, trip: dict, column: str) -> str:
        results = deserialize_results(trip.get(column))
        if not results:
            return ""
        lang = self._trip_language(trip)
        category_title = self._category_title(column, lang)
        lines = [f"<b>{category_title}</b>"]
        for result in results[:3]:
            clean_title = self._clean_result_title(result.title, category_title)
            link_url = html.escape(result.url, quote=True)
            lines.append(f"• <b>{html.escape(clean_title)}</b> — <a href=\"{link_url}\">{html.escape(tr(lang, 'open_link'))}</a>")
            hint = self._display_result_hint(result)
            extra_parts = [html.escape(part) for part in (hint, result.budget_fit, result.note) if part]
            if extra_parts:
                lines.append("  " + " — ".join(extra_parts))
        return "\n".join(lines)

    def _detected_needs_line(self, trip: dict) -> str:
        lang = self._trip_language(trip)
        detected_needs = deserialize_needs(trip.get("detected_needs"))
        if not detected_needs:
            return ""
        labels = {
            "tickets": tr(lang, "need_tickets"),
            "housing": tr(lang, "need_housing"),
            "excursions": tr(lang, "need_excursions"),
            "road": tr(lang, "need_road"),
            "car_rental": tr(lang, "need_car_rental"),
            "bike_rental": tr(lang, "need_bike_rental"),
            "transfers": tr(lang, "need_transfers"),
        }
        rendered = ", ".join(labels.get(item, item) for item in detected_needs)
        return f"\n{tr(lang, 'detected_needs_intro')}: <b>{html.escape(rendered)}</b>"

    @staticmethod
    def _has_housing_type_hint(trip: dict) -> bool:
        combined = " ".join(
            str(trip.get(field) or "")
            for field in ("notes", "source_prompt", "interests_text")
        ).lower()
        return any(keyword in combined for keyword in ("отел", "квартир", "апарт", "дом", "студи", "хостел"))

    def _planning_readiness(self, trip: dict, trip_id: int) -> tuple[str, str]:
        lang = self._trip_language(trip)
        detected_needs = set(deserialize_needs(trip.get("detected_needs")))
        known_members = self._db.count_chat_members(int(trip["chat_id"])) if trip.get("chat_id") else 0
        responded = len(self._db.list_participants(trip_id))
        interests_text = normalized_search_value(trip.get("interests_text"))

        checks: list[tuple[str, bool]] = [
            (tr(lang, "check_destination"), bool(normalized_search_value(trip.get("destination")))),
            (tr(lang, "check_dates"), bool(normalized_search_value(trip.get("dates_text")))),
            (tr(lang, "check_group"), int(trip.get("group_size") or 0) > 0),
            (tr(lang, "check_budget"), bool(normalized_search_value(trip.get("budget_text")))),
        ]
        if "tickets" in detected_needs:
            checks.append((tr(lang, "check_origin"), bool(normalized_search_value(trip.get("origin")))))
        if "housing" in detected_needs:
            checks.append((tr(lang, "check_housing_type"), self._has_housing_type_hint(trip)))
        if "excursions" in detected_needs:
            checks.append((tr(lang, "check_excursions"), bool(interests_text)))
        if "road" in detected_needs:
            checks.append((tr(lang, "check_departure_day"), bool(normalized_search_value(trip.get("dates_text")))))
        if known_members > 1:
            checks.append((tr(lang, "check_participant_replies"), responded > 0))

        ready_count = sum(1 for _, is_ready in checks if is_ready)
        total_count = max(1, len(checks))
        status_lines = [tr(lang, "readiness_title", ready=ready_count, total=total_count)]
        checklist_lines = [
            f"{'✅' if is_ready else '⏳'} {label}"
            for label, is_ready in checks
        ]
        return "\n".join(status_lines), "\n".join(checklist_lines)

    @staticmethod
    def _has_destination(trip: dict) -> bool:
        return bool(normalized_search_value(trip.get("destination")))

    @staticmethod
    def _budget_class_key(budget_text: str) -> str:
        lowered = (budget_text or "").lower()
        if any(token in lowered for token in ("первый класс", "без ограничений", "не ограничен", "люкс", "премиум", "vip", "вип")):
            return "trip_class_first"
        if any(token in lowered for token in ("эконом", "подешевле", "недорого", "дешево", "дёшево")):
            return "trip_class_economy"
        digits = [int(value) for value in "".join(ch if ch.isdigit() else " " for ch in lowered).split()]
        if digits:
            amount = digits[0]
            if amount <= 40000:
                return "trip_class_economy"
            if amount >= 120000:
                return "trip_class_first"
        return "trip_class_business"

    def _budget_class_label(self, budget_text: str, language_code: str = "ru") -> str:
        return tr(language_code, self._budget_class_key(budget_text))

    def build_start_text(self) -> str:
        return tr("ru", "start_intro")

    def build_start_text_for_language(self, language_code: str) -> str:
        return tr(language_code, "start_intro")

    def build_help_text(self, language_code: str = "ru") -> str:
        return tr(language_code, "help_text")

    def build_settings_text(self, chat_id: int) -> str:
        settings = self._db.get_or_create_settings(chat_id)
        lang = self._chat_language(chat_id)
        active_trip = self._db.get_active_trip(chat_id)
        reminders_enabled = bool(settings["reminders_enabled"])
        autodraft_enabled = bool(settings["autodraft_enabled"])

        active_trip_line = (
            f"{tr(lang, 'settings_active_trip')}: <b>{html.escape(active_trip['title'])}</b>"
            if active_trip
            else f"{tr(lang, 'settings_active_trip')}: <b>{tr(lang, 'settings_none')}</b>"
        )
        return (
            f"<b>{tr(lang, 'settings_title')}</b>\n"
            f"{active_trip_line}\n\n"
            f"• {tr(lang, 'settings_reminders')}: <b>{tr(lang, 'settings_reminders_on') if reminders_enabled else tr(lang, 'settings_reminders_off')}</b>\n"
            f"• {tr(lang, 'settings_autodraft')}: <b>{tr(lang, 'settings_autodraft_on') if autodraft_enabled else tr(lang, 'settings_autodraft_off')}</b>\n"
            f"• {tr(lang, 'settings_language')}: <b>{tr(lang, 'language_name')}</b>\n\n"
            + tr(lang, "settings_explainer")
        )

    def build_trip_created_text(self, *, replaced_trip: bool, chat_type: str | None = None, language_code: str = "ru") -> str:
        is_group = chat_type in {"group", "supergroup"}
        if replaced_trip:
            if is_group:
                return tr(language_code, "trip_created_replaced_group")
            return tr(language_code, "trip_created_replaced_private")
        if is_group:
            return tr(language_code, "trip_created_new_group")
        return tr(language_code, "trip_created_new_private")

    def build_status_updated_text(self, status: str, language_code: str = "ru") -> str:
        mapping = {
            "going": tr(language_code, "status_updated_going"),
            "interested": tr(language_code, "status_updated_interested"),
            "not_going": tr(language_code, "status_updated_not_going"),
        }
        return mapping.get(status, tr(language_code, "status_updated_default"))

    def build_participants_text(self, trip_id: int) -> str:
        trip = self._db.get_trip_by_id(trip_id)
        lang = self._trip_language(trip)
        return tr(lang, "participants_title") + "\n" + "\n".join(self._participant_lines(trip_id))

    def build_status_options_text(self, trip_id: int) -> str:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return tr("ru", "trip_not_found")
        lang = self._trip_language(trip)
        destination = html.escape(normalized_search_value(trip.get("destination")) or tr(lang, "settings_none"))
        dates_text = html.escape(normalized_search_value(trip.get("dates_text")) or tr(lang, "status_unknown_dates"))
        title = html.escape(trip.get("title") or destination)
        group_size = int(trip.get("group_size") or 0)
        return (
            f"{tr(lang, 'status_prompt_intro')}\n\n"
            f"{tr(lang, 'status_prompt_trip')}:\n"
            f"<b>{title}</b>\n"
            f"{tr(lang, 'summary_destination')}: <b>{destination}</b>\n"
            f"{tr(lang, 'status_prompt_dates')}: <b>{dates_text}</b>\n"
            f"{tr(lang, 'status_prompt_group')}: <b>{group_size}</b>\n\n"
            f"{tr(lang, 'status_choose')}\n"
            f"{tr(lang, 'status_going_hint')}\n"
            f"{tr(lang, 'status_interested_hint')}\n"
            f"{tr(lang, 'status_not_going_hint')}\n\n"
            f"{tr(lang, 'status_footer')}"
        )

    def build_trip_list_text(self, chat_id: int) -> str:
        lang = self._chat_language(chat_id)
        trips = self._db.list_trips(chat_id)
        if not trips:
            return tr(lang, "trip_list_empty")

        lines = [tr(lang, "trip_list_title")]
        for trip in trips[:10]:
            badge = tr(lang, "trip_status_active") if trip["status"] == "active" else tr(lang, "trip_status_archived")
            destination = html.escape(normalized_search_value(trip["destination"]) or tr(lang, "unknown_destination"))
            dates_text = html.escape(normalized_search_value(trip["dates_text"]) or tr(lang, "unknown_dates"))
            lines.append(
                f"• <b>{int(trip['id'])}</b> — {html.escape(trip['title'])} [{badge}]"
                f"\n  {destination}, {dates_text}"
            )
        lines.append("")
        lines.append(tr(lang, "trip_list_hint"))
        return "\n".join(lines)

    def build_trip_delete_confirm_text(self, trip: dict) -> str:
        lang = self._trip_language(trip)
        destination = html.escape(normalized_search_value(trip.get("destination")) or tr(lang, "unknown_destination"))
        dates = html.escape(normalized_search_value(trip.get("dates_text")) or tr(lang, "status_unknown_dates"))
        return tr(lang, "trip_delete_confirm_text", title=html.escape(trip["title"]), destination=destination, dates=dates)

    def build_group_clarifying_question(self, language_code: str = "ru") -> str:
        return tr(language_code, "group_clarify_destination")

    def build_group_destination_vote_text(self, options: list[tuple[str, int]], language_code: str = "ru") -> str:
        if not options:
            return tr(language_code, "group_vote_fallback")
        rendered = "\n".join(f"• {html.escape(name)} — {count}" for name, count in options[:4])
        return tr(language_code, "group_vote_intro", rendered=rendered)

    def build_group_autodraft_reply(self, trip: dict) -> str:
        lang = self._trip_language(trip)
        weather_text = (trip.get("weather_text") or "").strip()
        summary_short = (trip.get("summary_short_text") or "").strip()
        open_questions = (trip.get("open_questions_text") or "").strip()
        entry_requirements = (trip.get("entry_requirements_text") or "").strip()
        destination = normalized_search_value(trip.get("destination")) or tr(lang, "unknown_destination")
        dates_text = normalized_search_value(trip.get("dates_text")) or tr(lang, "status_unknown_dates")
        budget_text = normalized_search_value(trip.get("budget_text")) or tr(lang, "settings_none")
        budget_class = self._budget_class_label(budget_text, lang)
        readiness_text, checklist_text = self._planning_readiness(trip, int(trip["id"]))
        has_destination = self._has_destination(trip)
        sections = [
            self._category_section(trip, "flight_results"),
            self._category_section(trip, "housing_results"),
            self._category_section(trip, "activity_results"),
            self._category_section(trip, "transport_results"),
            self._category_section(trip, "rental_results"),
        ]
        visible_sections = [section for section in sections if section]
        compact_sections = "\n\n".join(visible_sections[:4])
        direction_block = (
            html.escape(summary_short)
            if summary_short and has_destination
            else tr(lang, "group_wait_destination")
        )
        return (
            f"{tr(lang, 'group_draft_title')}\n"
            f"{tr(lang, 'group_where')}: <b>{html.escape(destination)}</b>\n"
            f"{tr(lang, 'group_when')}: <b>{html.escape(dates_text)}</b>\n"
            f"{tr(lang, 'group_people')}: <b>{int(trip['group_size'] or 0)}</b>\n"
            f"{tr(lang, 'group_budget')}: <b>{html.escape(budget_text)}</b>\n"
            f"{tr(lang, 'group_trip_class')}: <b>{html.escape(budget_class)}</b>\n"
            + self._detected_needs_line(trip)
            + f"\n\n{readiness_text}\n{html.escape(checklist_text)}"
            + f"\n\n<b>{tr(lang, 'group_short')}</b>\n{direction_block}"
            + (f"\n\n<b>{tr(lang, 'summary_weather')}</b>\n{html.escape(weather_text)}" if weather_text else "")
            + (f"\n\n<b>{tr(lang, 'summary_entry')}</b>\n{html.escape(entry_requirements)}" if entry_requirements else "")
            + (f"\n\n{compact_sections}" if compact_sections and has_destination else "")
            + (f"\n\n<b>{tr(lang, 'group_open_questions')}</b>\n{html.escape(open_questions)}" if open_questions else "")
            + f"\n\n{tr(lang, 'summary_use_summary')}"
        )

    def build_housing_search_text(self, trip: dict, response: HousingSearchResponse) -> str:
        lang = self._trip_language(trip)
        destination = normalized_search_value(trip["destination"]) or tr(lang, "unknown_destination")
        lines = [
            tr(lang, "housing_search_title", destination=html.escape(destination)),
            html.escape(response.summary),
        ]
        if response.results:
            lines.append("")
            lines.append(tr(lang, "housing_search_open"))
            for result in response.results[:5]:
                link_url = html.escape(result.url, quote=True)
                lines.append(
                    f"• <b>{html.escape(result.source)}</b> — <a href=\"{link_url}\">{html.escape(tr(lang, 'open_link'))}</a>\n"
                    f"  {html.escape(self._clean_result_title(result.title, self._category_title('housing_results', lang)))}\n"
                    f"  {html.escape(self._display_result_hint(result))}"
                )
        else:
            lines.append("")
            lines.append(tr(lang, "housing_try_later"))
        return "\n".join(lines)

    def build_route_section_text(self, trip_id: int) -> str:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return tr("ru", "trip_not_found")
        lang = self._trip_language(trip)
        return (
            f"{tr(lang, 'route_title')}\n"
            f"{html.escape(trip['itinerary_text'] or tr(lang, 'route_empty'))}"
        )

    def build_tickets_section_text(self, trip_id: int) -> str:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return tr("ru", "trip_not_found")
        lang = self._trip_language(trip)
        section = self._category_section(trip, "flight_results")
        if section:
            return section
        tickets_text = (trip.get("tickets_text") or "").strip()
        if tickets_text:
            return f"{tr(lang, 'tickets_title')}\n{html.escape(tickets_text)}"
        return f"{tr(lang, 'tickets_title')}\n{tr(lang, 'tickets_empty')}"

    def build_housing_section_text(self, trip_id: int) -> str:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return tr("ru", "trip_not_found")
        lang = self._trip_language(trip)
        section = self._category_section(trip, "housing_results")
        if section:
            return section
        return f"{tr(lang, 'housing_title')}\n{tr(lang, 'housing_empty')}"

    def _build_brief_html(self, trip_id: int) -> str:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return "<b>Поездка не найдена.</b>"
        lang = self._trip_language(trip)
        destination = normalized_search_value(trip["destination"]) or tr(lang, "unknown_destination")
        origin = normalized_search_value(trip["origin"]) or tr(lang, "settings_none")
        dates_text = normalized_search_value(trip["dates_text"]) or tr(lang, "status_unknown_dates")
        budget_text = normalized_search_value(trip["budget_text"]) or tr(lang, "settings_none")
        budget_class = self._budget_class_label(budget_text, lang)
        interests_text = normalized_search_value(trip["interests_text"]) or tr(lang, "settings_none")
        lines = [
            f"<b>🧾 {html.escape(trip['title'])}</b>",
            f"{tr(lang, 'summary_destination')}: <b>{html.escape(destination)}</b>",
            f"{tr(lang, 'summary_origin')}: <b>{html.escape(origin)}</b>",
            f"{tr(lang, 'summary_dates')}: <b>{html.escape(dates_text)}</b>",
            f"⏱ Длительность: <b>{int(trip['days_count'] or 0)} дн.</b>",
            f"👥 Размер группы: <b>{int(trip['group_size'] or 0)} чел.</b>",
            f"{tr(lang, 'summary_budget')}: <b>{html.escape(budget_text)}</b>",
            f"{tr(lang, 'summary_trip_class')}: <b>{html.escape(budget_class)}</b>",
            f"{tr(lang, 'summary_interests')}: <b>{html.escape(interests_text)}</b>",
        ]
        if trip["source_prompt"]:
            lines.append("")
            lines.append("<b>Исходный запрос</b>")
            lines.append(html.escape(trip["source_prompt"]))
        return "\n".join(lines)

    def _build_summary_html(self, trip_id: int) -> str:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return tr("ru", "trip_not_found")
        lang = self._trip_language(trip)

        destination = normalized_search_value(trip["destination"]) or tr(lang, "unknown_destination")
        origin = normalized_search_value(trip["origin"]) or tr(lang, "settings_none")
        dates_text = normalized_search_value(trip["dates_text"]) or tr(lang, "status_unknown_dates")
        budget_text = normalized_search_value(trip["budget_text"]) or tr(lang, "settings_none")
        budget_class = self._budget_class_label(budget_text, lang)
        interests_text = normalized_search_value(trip["interests_text"]) or tr(lang, "settings_none")
        has_destination = self._has_destination(trip)
        stay_preview = self._escape_block(
            self._preview_multiline(trip["stay_text"] or "", max_blocks=1)
            if has_destination
            else tr(lang, "group_wait_destination")
        )
        context_preview = self._escape_block(
            self._preview_multiline(trip["context_text"] or "", max_blocks=1)
            if has_destination
            else tr(lang, "summary_short_no_destination")
        )
        notes_text = self._escape_block(trip["notes"] or "—")
        weather_text = (trip["weather_text"] or "").strip()
        entry_requirements = (trip.get("entry_requirements_text") or "").strip()
        weather_block = f"\n\n<b>{tr(lang, 'summary_weather')}</b>\n{html.escape(weather_text)}" if weather_text else ""
        entry_block = f"\n\n<b>{tr(lang, 'summary_entry')}</b>\n{html.escape(entry_requirements)}" if entry_requirements else ""
        sections = [
            self._category_section(trip, "flight_results"),
            self._category_section(trip, "housing_results"),
            self._category_section(trip, "activity_results"),
            self._category_section(trip, "transport_results"),
            self._category_section(trip, "rental_results"),
        ]
        structured_block = "\n\n".join(section for section in sections if section) if has_destination else ""
        links_text = (trip.get("links_text") or "").strip()
        links_block = f"\n\n<b>Полезные ссылки</b>\n{html.escape(links_text)}" if links_text and not structured_block and has_destination else ""
        summary_short = (trip.get("summary_short_text") or "").strip()
        short_summary_text = (
            summary_short
            if summary_short and has_destination
            else tr(lang, "summary_short_no_destination")
        )
        short_block = f"\n\n<b>{tr(lang, 'summary_quick')}</b>\n{html.escape(short_summary_text)}"
        open_questions = (trip.get("open_questions_text") or "").strip()
        open_questions_block = f"\n\n<b>{tr(lang, 'summary_open_questions')}</b>\n{html.escape(open_questions)}" if open_questions else ""
        readiness_text, checklist_text = self._planning_readiness(trip, trip_id)
        route_preview = self._escape_block(
            self._preview_multiline(trip["itinerary_text"] or "", max_blocks=3)
            if has_destination
            else tr(lang, "summary_short_no_destination")
        )

        return (
            f"<b>🧭 {html.escape(trip['title'])}</b>\n"
            f"{tr(lang, 'summary_destination')}: <b>{html.escape(destination)}</b>\n"
            f"{tr(lang, 'summary_origin')}: <b>{html.escape(origin)}</b>\n"
            f"{tr(lang, 'summary_dates')}: <b>{html.escape(dates_text)}</b> · <b>{int(trip['days_count'] or 0)} дн.</b>\n"
            f"{tr(lang, 'summary_group')}: <b>{int(trip['group_size'] or 0)} чел.</b>\n"
            f"{tr(lang, 'summary_budget')}: <b>{html.escape(budget_text)}</b>\n"
            f"{tr(lang, 'summary_trip_class')}: <b>{html.escape(budget_class)}</b>\n"
            f"{tr(lang, 'summary_interests')}: <b>{html.escape(interests_text)}</b>"
            + self._detected_needs_line(trip)
            + "\n"
            + f"\n{readiness_text}\n{html.escape(checklist_text)}"
            + short_block
            + "\n\n"
            f"<b>{tr(lang, 'summary_context')}</b>\n{context_preview}\n\n"
            f"<b>{tr(lang, 'summary_route')}</b>\n{route_preview}\n{tr(lang, 'summary_route_button_note')}\n\n"
            f"<b>{tr(lang, 'summary_stay')}</b>\n{stay_preview}\n\n"
            f"<b>{tr(lang, 'summary_budget_total')}</b>\n{html.escape(trip['budget_total_text'] or tr(lang, 'summary_budget_total_empty'))}\n\n"
            f"<b>{tr(lang, 'summary_participants')}</b>\n"
            + "\n".join(self._participant_lines(trip_id))
            + "\n\n"
            + f"<b>{tr(lang, 'summary_date_options')}</b>\n"
            + "\n".join(self._date_lines(trip_id))
            + "\n\n"
            + f"<b>{tr(lang, 'summary_notes')}</b>\n{notes_text}"
            + open_questions_block
            + (f"\n\n{structured_block}" if structured_block else "")
            + links_block
            + entry_block
            + weather_block
        )
