from bot.group_chat_analyzer import GroupChatAnalyzer


def test_analyzer_detects_contextual_categories() -> None:
    analyzer = GroupChatAnalyzer()

    signal = analyzer.analyze_messages(
        [
            "Ребята, давайте летом съездим в Казань",
            "Нужен отель и экскурсии",
            "Может еще взять машину в аренду",
        ]
    )

    assert signal.has_travel_intent is True
    assert signal.destination == "Казань"
    assert "housing" in signal.detected_needs
    assert "excursions" in signal.detected_needs
    assert "car_rental" in signal.detected_needs


def test_analyzer_ignores_unmentioned_categories() -> None:
    analyzer = GroupChatAnalyzer()

    signal = analyzer.analyze_messages(
        [
            "Летим из Томска в Сочи на выходные",
            "Нужны только билеты и жилье",
        ]
    )

    assert "tickets" in signal.detected_needs
    assert "housing" in signal.detected_needs
    assert "excursions" not in signal.detected_needs
