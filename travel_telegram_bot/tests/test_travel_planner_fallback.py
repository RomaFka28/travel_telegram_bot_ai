import re

from travel_planner import TravelPlanner


def test_fallback_context_for_foreign_destination_mentions_api_key() -> None:
    planner = TravelPlanner()
    request = planner.build_request_from_fields(
        title="Paris trip",
        destination="Paris",
        origin="Berlin",
        dates_text="12-14 June",
        days_count=3,
        group_size=2,
        budget_text="mid-range",
        interests_text="city, food",
        notes="",
        source_prompt="Plan Paris",
        language_code="en",
    )

    plan = planner.generate_plan(request)

    assert "OPENROUTER_API_KEY" in plan.context_text
    assert "реальных мест" in plan.context_text


def test_known_foreign_destination_budget_total_is_in_rubles() -> None:
    planner = TravelPlanner()
    request = planner.build_request_from_fields(
        title="Istanbul trip",
        destination="Стамбул",
        origin="Тбилиси",
        dates_text="12 июня",
        days_count=3,
        group_size=1,
        budget_text="Бизнес",
        interests_text="еда, прогулки",
        notes="",
        source_prompt="Хочу в Стамбул",
        language_code="ru",
    )

    plan = planner.generate_plan(request)

    assert "₽" in plan.budget_total_text
    assert "₺" not in plan.budget_total_text
    assert "В местной валюте" in plan.budget_breakdown_text


def test_tomsk_economy_budget_is_reasonable() -> None:
    """Томск эконом, 3 дня, 2 чел — общий бюджет не должен превышать ~30к ₽/чел."""
    planner = TravelPlanner()
    request = planner.build_request_from_fields(
        title="томск • 3 дн.",
        destination="Томск",
        origin="Иркутск",
        dates_text="12 июня",
        days_count=3,
        group_size=2,
        budget_text="эконом",
        interests_text="нет",
        notes="квартира туда-обратно",
        source_prompt="Из иркутска в томск на 3 дня 12 июня 2 человека бюджет эконом квартира туда-обратно",
        language_code="ru",
    )

    plan = planner.generate_plan(request)

    # Томск должен быть найден как отдельный профиль
    profile = planner._find_profile("Томск")
    assert profile.key == "томск", f"Expected 'томск' profile, got '{profile.key}'"
    assert profile.country == "Россия"

    # Бюджет должен быть в рублях
    assert "₽" in plan.budget_total_text

    # Парсим итоговую сумму из budget_total_text (формат: "X – Y ₽")
    numbers = [int(n.replace(" ", "")) for n in re.findall(r"([\d\s]+) ₽", plan.budget_total_text)]
    assert len(numbers) >= 2, f"Expected at least 2 numbers in '{plan.budget_total_text}'"
    total_low, total_high = numbers[0], numbers[1]

    # На человека (делим на group_size=2)
    per_person_low = total_low // 2
    per_person_high = total_high // 2

    # Для эконом Томска 3 дня: должно быть разумным — не больше 30к/чел
    assert per_person_high <= 30000, (
        f"Tomsk economy budget too high: {per_person_low}-{per_person_high} ₽/person. "
        f"Total: {plan.budget_total_text}"
    )
    # Дорога Иркутск-Томск не должна доминировать
    assert "Дорога" in plan.budget_breakdown_text
