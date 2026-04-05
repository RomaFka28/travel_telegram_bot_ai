from travelpayouts_flights import TravelpayoutsFlightProvider


def test_build_search_url_does_not_depend_on_module_name_urllib() -> None:
    provider = TravelpayoutsFlightProvider(api_key="demo-key")

    url = provider._build_search_url(
        origin_code="TBS",
        destination_code="IST",
        start_date="2026-06-12",
        end_date=None,
        one_way=True,
        adults=1,
        trip_class=1,
    )

    assert url.startswith("https://www.aviasales.ru/search?")
    assert "origin_iata=TBS" in url
    assert "destination_iata=IST" in url
    assert "one_way=1" in url
