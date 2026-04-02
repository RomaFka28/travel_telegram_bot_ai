from travelpayouts_flights import FlightOffer, TravelpayoutsFlightProvider


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


def test_prioritize_offers_keeps_cheapest_direct_and_extra_variants() -> None:
    provider = TravelpayoutsFlightProvider(api_key="test-key")
    offers = [
        FlightOffer("TBS", "IST", "2026-06-12T04:35:00+00:00", "", 12556, 1, True),
        FlightOffer("TBS", "IST", "2026-06-12T17:25:00+00:00", "", 13390, 0, True),
        FlightOffer("TBS", "IST", "2026-06-12T07:25:00+00:00", "", 17569, 0, True),
        FlightOffer("TBS", "IST", "2026-06-12T21:10:00+00:00", "", 22312, 0, True),
    ]

    prioritized = provider._prioritize_offers(offers)

    assert len(prioritized) == 4
    assert prioritized[0][0] == "Самый дешевый"
    assert prioritized[1][0] == "Самый дешевый прямой"
    assert prioritized[2][0] == "Еще вариант"
    assert prioritized[3][0] == "Еще вариант 2"


def test_merge_offers_adds_direct_only_results_without_duplicates() -> None:
    provider = TravelpayoutsFlightProvider(api_key="test-key")
    common_offer = FlightOffer("TBS", "IST", "2026-06-12T04:35:00+00:00", "", 12556, 1, True)
    direct_offer = FlightOffer("TBS", "IST", "2026-06-12T17:25:00+00:00", "", 13390, 0, True)

    merged = provider._merge_offers([common_offer], [direct_offer, common_offer])

    assert [offer.value for offer in merged] == [12556, 13390]
