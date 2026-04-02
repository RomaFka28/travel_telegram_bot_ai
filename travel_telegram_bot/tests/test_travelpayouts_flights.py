from travelpayouts_flights import TravelpayoutsFlightProvider


def test_build_search_url_for_one_way_uses_search_route() -> None:
    provider = TravelpayoutsFlightProvider(api_key="test-key")

    url = provider._build_search_url(
        origin_code="TBS",
        destination_code="IST",
        start_date="2026-06-12",
        end_date=None,
        one_way=True,
        adults=1,
    )

    assert url == "https://www.aviasales.ru/search/TBS1206IST1"


def test_build_search_url_for_round_trip_keeps_passenger_count() -> None:
    provider = TravelpayoutsFlightProvider(api_key="test-key")

    url = provider._build_search_url(
        origin_code="TBS",
        destination_code="IST",
        start_date="2026-06-12",
        end_date="2026-06-18",
        one_way=False,
        adults=2,
    )

    assert url == "https://www.aviasales.ru/search/TBS1206IST18062"
