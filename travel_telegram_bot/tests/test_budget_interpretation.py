from travel_planner import TravelPlanner


def test_budget_interpretation_understands_hard_ceiling() -> None:
    planner = TravelPlanner()
    interpreted = planner.interpret_budget_text("Хотим уложиться до 50 000 на человека")
    assert interpreted.display_text == "до 50 000 ₽"
    assert interpreted.budget_class == "бизнес"
    assert interpreted.mode == "ceiling"


def test_budget_interpretation_understands_target_budget() -> None:
    planner = TravelPlanner()
    interpreted = planner.interpret_budget_text("Нормально будет на 50000, без роскоши")
    assert interpreted.display_text == "на 50 000 ₽"
    assert interpreted.budget_class == "бизнес"
    assert interpreted.mode == "target"


def test_budget_interpretation_understands_floor_budget() -> None:
    planner = TravelPlanner()
    interpreted = planner.interpret_budget_text("От 150000 и выше, можно красиво")
    assert interpreted.display_text == "от 150 000 ₽"
    assert interpreted.budget_class == "первый класс"
    assert interpreted.mode == "floor"


def test_budget_interpretation_understands_casual_economy_language() -> None:
    planner = TravelPlanner()
    interpreted = planner.interpret_budget_text("Хотим подешевле и без лишних трат")
    assert interpreted.display_text == "Эконом"
    assert interpreted.budget_class == "эконом"
    assert interpreted.mode == "class_only"


def test_detect_budget_level_uses_interpretation() -> None:
    planner = TravelPlanner()
    assert planner._detect_budget_level("бюджет не ограничен") == "первый класс"
    assert planner._detect_budget_level("до 30 000") == "эконом"
    assert planner._detect_budget_level("на 50 000") == "бизнес"
