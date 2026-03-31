from travel_links import build_links_text


def test_build_links_text_includes_russian_sources() -> None:
    links = build_links_text(
        "Казань",
        "12-14 июня",
        "Томск",
        context_text="нужны билеты, отель и экскурсии, потом может поезд",
    )

    lowered = links.lower()
    assert "aviasales" in lowered
    assert "tutu" in lowered
    assert "ostrovok" in lowered
    assert "sutochno" in lowered
    assert "tripster" in lowered


def test_build_links_text_only_shows_relevant_categories_from_chat() -> None:
    links = build_links_text(
        "Сочи",
        "12-14 июня",
        "Томск",
        context_text="может взять машину в аренду и сходить на экскурсию",
    )

    lowered = links.lower()
    assert "tripster" in lowered
    assert "аренда+авто" in lowered
    assert "ostrovok" not in lowered
    assert "tutu" not in lowered
