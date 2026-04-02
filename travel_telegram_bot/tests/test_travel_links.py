from travel_links import _estimate_housing_result, _housing_links, build_structured_link_results


def test_housing_links_use_budget_profile_for_international_destination() -> None:
    premium_links = _housing_links("Париж", "2026-06-12", "2026-06-15", 1, "первый класс")
    economy_links = _housing_links("Париж", "2026-06-12", "2026-06-15", 1, "эконом")

    assert premium_links[0][0] == "🏨 Booking.com"
    assert "luxury+hotel" in premium_links[0][1]
    assert economy_links[0][0] == "🛎 Agoda"


def test_estimate_housing_result_raises_price_for_premium_budget() -> None:
    economy_price, economy_style, _ = _estimate_housing_result("Париж", "🏨 Booking.com", 1, "эконом", "")
    premium_price, premium_style, _ = _estimate_housing_result("Париж", "🏨 Booking.com", 1, "первый класс", "")

    assert economy_price == "from 95 EUR/night"
    assert "премиальный" in premium_style
    assert premium_price != economy_price


def test_build_structured_link_results_prioritizes_premium_housing() -> None:
    structured = build_structured_link_results(
        "Стамбул",
        "12 июня",
        "Тбилиси",
        group_size=1,
        context_text="жилье отель",
        budget_text="первый класс",
    )

    assert structured["housing"]
    assert "Яндекс Путешествия" in structured["housing"][0].source or "Booking.com" in structured["housing"][0].source
