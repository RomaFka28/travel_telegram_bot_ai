from travel_links import _estimate_housing_result, _housing_links, _ticket_links, build_structured_link_results


def test_housing_links_use_budget_profile_for_international_destination() -> None:
    premium_links = _housing_links("Paris", "2026-06-12", "2026-06-15", 1, "first class")
    economy_links = _housing_links("Paris", "2026-06-12", "2026-06-15", 1, "economy")

    assert premium_links[0][0] == "🏨 Booking.com"
    assert "luxury+hotel" in premium_links[0][1]
    assert economy_links[0][0] == "🛎 Agoda"


def test_estimate_housing_result_raises_price_for_premium_budget() -> None:
    economy_price, economy_style, _ = _estimate_housing_result("Paris", "🏨 Booking.com", 1, "economy", "")
    premium_price, premium_style, _ = _estimate_housing_result("Paris", "🏨 Booking.com", 1, "first class", "")

    assert economy_price == "from 95 EUR/night"
    assert "премиаль" in premium_style.lower()
    assert premium_price != economy_price


def test_build_structured_link_results_prioritizes_premium_housing() -> None:
    structured = build_structured_link_results(
        "Istanbul",
        "12 June",
        "Tbilisi",
        group_size=1,
        context_text="housing hotel",
        budget_text="first class",
    )

    assert structured["housing"]
    assert any(source in structured["housing"][0].source for source in ("Booking.com", "Yandex"))


def test_ticket_links_use_iata_search_url(monkeypatch) -> None:
    monkeypatch.setattr(
        "travel_links._resolve_iata_code",
        lambda term: {"Tbilisi": "TBS", "Istanbul": "IST"}.get(term, ""),
    )

    links = _ticket_links("Istanbul", "Tbilisi", "2026-06-12", None)

    assert links
    assert "origin_iata=TBS" in links[0][1]
    assert "destination_iata=IST" in links[0][1]
    assert "one_way=1" in links[0][1]


def test_housing_links_include_home_search_sources_for_house_context() -> None:
    links = _housing_links(
        "Istanbul",
        "2026-06-12",
        "2026-06-15",
        1,
        "business",
        "need a house or apartment",
    )

    labels = [label for label, _ in links]
    assert "🏠 Airbnb" in labels
    assert any(label in labels for label in ("🏠 Суточно", "🏡 Booking Homes"))
