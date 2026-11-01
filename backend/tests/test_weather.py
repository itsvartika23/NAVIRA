from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from backend.app import main
from backend.app.services.weather import OpenWeatherAgent


def test_openweather_route_weather_fetches_current_and_forecast():
    urls = []

    def fetch(url):
        urls.append(url)
        if "/weather?" in url:
            return {
                "cod": 200,
                "name": "Delhi",
                "dt": 1793340000,
                "main": {"temp": 28.4, "feels_like": 31.2, "humidity": 61},
                "wind": {"speed": 3.0},
                "weather": [{"description": "light rain", "icon": "10d"}],
            }
        return {"cod": "200", "list": [
            {"dt_txt": "2026-10-30 12:00:00", "main": {"temp": 27}, "weather": [{"description": "rain", "icon": "10d"}], "pop": 0.65}
        ]}

    result = OpenWeatherAgent(api_key="test-secret", fetcher=fetch).route_weather([[77.2, 28.6], [77.3, 28.7]])

    assert len(urls) == 4
    assert result["status"] == "available"
    assert result["locations"][0]["location"] == "Delhi"
    assert result["locations"][0]["current"]["temperature_c"] == 28
    assert result["locations"][0]["current"]["wind_speed_kmh"] == 10.8
    assert result["locations"][0]["forecast"][0]["rain_probability"] == 65
    assert result["locations"][0]["rain_probability_percent"] == 65
    assert all(parse_qs(urlparse(url).query)["appid"] == ["test-secret"] for url in urls)


def test_openweather_missing_key_and_cache():
    def fail(_url):
        raise AssertionError("provider must not be called without a key")

    assert OpenWeatherAgent(api_key="", fetcher=fail).route_weather([[77, 28], [77.1, 28.1]])["status"] == "unconfigured"

    calls = []
    def fetch(url):
        calls.append(url)
        if "/weather?" in url:
            return {"name": "Delhi", "main": {"temp": 28, "feels_like": 29, "humidity": 60}, "weather": [{"description": "clear sky"}]}
        return {"list": []}

    agent = OpenWeatherAgent(api_key="key", fetcher=fetch)
    agent.route_weather([[77, 28], [77.1, 28.1]])
    agent.route_weather([[77, 28], [77.1, 28.1]])
    assert len(calls) == 4


def test_route_weather_validates_coordinates_and_endpoint_is_registered(monkeypatch):
    with pytest.raises(ValidationError):
        main.RouteWeatherIn(coordinates=[[181, 28], [77, 28]])
    with pytest.raises(ValidationError):
        main.RouteWeatherIn(coordinates=[[77, 28], [float("nan"), 28]])
    assert "/api/v1/routes/weather" in main.app.openapi()["paths"]
    monkeypatch.setattr(main, "get_route_weather", lambda coordinates: {"status": "unconfigured"})
    body = main.RouteWeatherIn(coordinates=[[77, 28], [77.1, 28.1]])
    assert main.route_weather(body) == {"status": "unconfigured"}


OPEN_METEO = {
    "utc_offset_seconds": 19800,
    "current": {"time": 1793340000, "temperature_2m": 24.9, "apparent_temperature": 27.6, "relative_humidity_2m": 67, "wind_speed_10m": 2.9, "weather_code": 61, "is_day": 0},
    "hourly": {"time": [1793340000 + 3600 * i for i in range(24)], "temperature_2m": [25] * 24,
               "precipitation_probability": [10] * 3 + [70] + [5] * 20, "weather_code": [0] * 24, "is_day": [1] * 24},
}


def test_open_meteo_returns_the_same_shape_without_a_key():
    from backend.app.services.weather import OpenMeteoAgent
    urls = []
    def fetch(url):
        urls.append(url)
        return OPEN_METEO
    result = OpenMeteoAgent(fetcher=fetch).route_weather([[77.2, 28.6], [77.3, 28.7]])
    assert result["status"] == "available" and result["provider"] == "Open-Meteo"
    loc = result["locations"][0]
    assert loc["current"] == {"temperature_c": 25, "feels_like_c": 28, "humidity_percent": 67, "wind_speed_kmh": 2.9, "condition": "Light rain", "icon": "10n"}
    assert len(loc["forecast"]) == 8 and loc["forecast"][1]["rain_probability"] == 70
    assert loc["rain_probability_percent"] == 70 and "appid" not in urls[0]


def test_route_weather_falls_back_to_open_meteo(monkeypatch):
    from backend.app.services import weather
    monkeypatch.delenv("OPENWEATHER_API_KEY", raising=False)
    monkeypatch.delenv("NAVIRA_WEATHER_PROVIDER", raising=False)
    monkeypatch.setattr(weather, "_open_meteo_agent", weather.OpenMeteoAgent(fetcher=lambda url: OPEN_METEO))
    assert weather.get_route_weather([[77.2, 28.6]])["provider"] == "Open-Meteo"

    def broken(url):
        raise weather.WeatherUnavailable("OpenWeatherMap rejected the backend API key.")
    monkeypatch.setenv("OPENWEATHER_API_KEY", "bad")
    monkeypatch.setattr(weather, "_weather_agent", weather.OpenWeatherAgent(api_key="bad", fetcher=broken))
    assert weather.get_route_weather([[77.2, 28.6]])["provider"] == "Open-Meteo"
