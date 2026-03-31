from travel_links import build_links_text


def test_build_links_text_includes_russian_sources() -> None:
    links = build_links_text("Казань", "12-14 июня", "Томск")

    lowered = links.lower()
    assert "aviasales" in lowered
    assert "tutu" in lowered
    assert "ostrovok" in lowered
    assert "sutochno" in lowered
    assert "tripster" in lowered
