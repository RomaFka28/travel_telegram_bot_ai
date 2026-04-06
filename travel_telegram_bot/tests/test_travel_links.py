from travel_links import _estimate_housing_result, _housing_links, _ticket_links, build_links_map, build_structured_link_results, detect_link_needs


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
    assert "премиал" in premium_style.lower()
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


def test_build_links_map_uses_days_count_to_extend_checkout_date() -> None:
    links = build_links_map(
        "Томск",
        "12 июня",
        "Иркутск",
        days_count=3,
        group_size=2,
        context_text="квартира, туда-обратно",
        budget_text="эконом",
    )

    housing_urls = [url for _, url in links["housing"]]
    assert any("checkout=2026-06-14" in url or "checkoutDate=2026-06-14" in url for url in housing_urls)


def test_build_links_map_keeps_one_way_trip_without_checkout_or_return_date() -> None:
    links = build_links_map(
        "Стамбул",
        "12 июня",
        "Тбилиси",
        days_count=3,
        group_size=1,
        context_text="билет в одну сторону, отель",
        budget_text="бизнес",
    )

    ticket_urls = [url for _, url in links["tickets"]]
    housing_urls = [url for _, url in links["housing"]]
    assert all("return_date=" not in url for url in ticket_urls)
    assert all("checkout=" not in url and "checkoutDate=" not in url for url in housing_urls)


def test_detect_link_needs_treats_route_type_clarification_as_ticket_signal() -> None:
    needs = detect_link_needs("из Иркутска в Томск, квартира, туда-обратно")

    assert "tickets" in needs
    assert "housing" in needs
