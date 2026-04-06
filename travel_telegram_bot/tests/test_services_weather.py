import asyncio

from services import weather


class DummyResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "daily": {
                "time": ["2026-06-12"],
                "temperature_2m_max": [18],
                "temperature_2m_min": [9],
                "apparent_temperature_max": [17],
                "weathercode": [1],
                "precipitation_sum": [0.0],
                "wind_speed_10m_max": [12],
            }
        }


class DummyClient:
    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[dict[str, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, params: dict[str, object]):
        self.calls.append({"url": url, "params": params})
        DummyClient.last_call = {"url": url, "params": params}
        return DummyResponse()


def test_get_forecast_for_date_uses_current_open_meteo_wind_field(monkeypatch) -> None:
    monkeypatch.setattr("services.weather.httpx.AsyncClient", DummyClient)

    forecast = asyncio.run(weather.get_forecast_for_date(56.5, 84.98, "2026-06-12"))

    assert forecast is not None
    assert forecast["wind_speed_10m_max"] == 12
    assert "wind_speed_10m_max" in str(DummyClient.last_call["params"]["daily"])
