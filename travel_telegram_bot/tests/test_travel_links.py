from types import SimpleNamespace

import travel_links
from travel_links import build_links_map, build_links_text


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


def test_build_links_map_skips_placeholder_destination() -> None:
    links = build_links_map(
        "-",
        "12-14 июня",
        "Томск",
        context_text="нужно жильё и билеты",
    )

    assert links == {}


def test_build_links_text_switches_to_international_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        travel_links,
        "detect_route_locale",
        lambda destination, origin=None: SimpleNamespace(is_ru_cis_destination=False),
    )

    links = build_links_text(
        "Paris",
        "12-14 июня",
        "Berlin",
        context_text="нужны отель, экскурсии, дорога и аренда машины",
    )

    lowered = links.lower()
    assert "booking.com" in lowered
    assert "getyourguide" in lowered
    assert "rome2rio" in lowered
    assert "rentalcars" in lowered
    assert "ostrovok" not in lowered
