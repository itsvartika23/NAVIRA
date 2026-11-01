"""Route weather: Open-Meteo (no key needed) by default, OpenWeatherMap when a key is set."""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

logger = logging.getLogger(__name__)


class WeatherUnavailable(RuntimeError):
    """The configured weather provider did not return usable data."""


class OpenWeatherAgent:
    BASE_URL = "https://api.openweathermap.org/data/2.5"
    CACHE_TTL_SECONDS = 600
    MAX_ROUTE_POINTS = 3

    def __init__(self, api_key: str | None = None, fetcher: Callable[[str], Any] | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("OPENWEATHER_API_KEY", "")
        self.fetcher = fetcher or self._fetch
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _fetch(url: str) -> Any:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "NAVIRA/0.1"})
        try:
            with urlopen(request, timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 401:
                raise WeatherUnavailable("OpenWeatherMap rejected the backend API key.") from error
            if error.code == 404:
                raise WeatherUnavailable("OpenWeatherMap has no weather data for this route location.") from error
            if error.code == 429:
                raise WeatherUnavailable("OpenWeatherMap request limit reached. Try again later.") from error
            raise WeatherUnavailable(f"OpenWeatherMap returned HTTP {error.code}.") from error
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise WeatherUnavailable("OpenWeatherMap could not be reached.") from error

    @staticmethod
    def _sample_indices(count: int, limit: int) -> list[int]:
        if count <= limit:
            return list(range(count))
        return sorted({round(index * (count - 1) / (limit - 1)) for index in range(limit)})

    def _request(self, endpoint: str, latitude: float, longitude: float) -> dict[str, Any]:
        query = urlencode({"lat": f"{latitude:.5f}", "lon": f"{longitude:.5f}", "units": "metric", "appid": self.api_key})
        result = self.fetcher(f"{self.BASE_URL}/{endpoint}?{query}")
        if not isinstance(result, dict) or result.get("cod") not in (None, 200, "200"):
            raise WeatherUnavailable("OpenWeatherMap returned an invalid response.")
        return result

    def _location_weather(self, latitude: float, longitude: float) -> dict[str, Any]:
        cache_key = f"{latitude:.3f}:{longitude:.3f}"
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.CACHE_TTL_SECONDS:
                return dict(cached[1])

        current = self._request("weather", latitude, longitude)
        forecast = self._request("forecast", latitude, longitude)
        main = current.get("main") or {}
        wind = current.get("wind") or {}
        conditions = current.get("weather") or []
        condition = conditions[0] if conditions and isinstance(conditions[0], dict) else {}
        if not all(math.isfinite(float(main.get(field, float("nan")))) for field in ("temp", "feels_like", "humidity")):
            raise WeatherUnavailable("OpenWeatherMap returned incomplete current conditions.")
        entries = forecast.get("list") or []
        hourly = []
        for item in entries[:8]:
            item_main = item.get("main") or {}
            item_conditions = item.get("weather") or []
            item_condition = item_conditions[0] if item_conditions else {}
            if "temp" not in item_main:
                continue
            hourly.append({
                "time": item.get("dt_txt"),
                "temperature_c": round(float(item_main["temp"])),
                "condition": str(item_condition.get("description") or "Unknown").capitalize(),
                "icon": item_condition.get("icon"),
                "rain_probability": round(float(item.get("pop", 0)) * 100),
            })
        payload = {
            "status": "available",
            "location": str(current.get("name") or "Route location"),
            "latitude": latitude,
            "longitude": longitude,
            "current": {
                "temperature_c": round(float(main["temp"])),
                "feels_like_c": round(float(main["feels_like"])),
                "humidity_percent": round(float(main["humidity"])),
                "wind_speed_kmh": round(float(wind.get("speed", 0)) * 3.6, 1),
                "condition": str(condition.get("description") or "Unknown").capitalize(),
                "icon": condition.get("icon"),
            },
            "forecast": hourly,
            "rain_probability_percent": max((item["rain_probability"] for item in hourly), default=None),
            "alerts": [],
            "alerts_note": "Official severe-weather alerts are not included in the Current Weather and 5-day Forecast endpoints.",
            "observed_at": current.get("dt"),
            "retrieved_at": time.time(),
            "provider": "OpenWeatherMap",
        }
        with self._lock:
            self._cache[cache_key] = (time.monotonic(), payload)
            if len(self._cache) > 500:
                self._cache.pop(next(iter(self._cache)))
        return dict(payload)

    def route_weather(self, coordinates: list[list[float]]) -> dict[str, Any]:
        if not self.api_key:
            return {"status": "unconfigured", "provider": "OpenWeatherMap", "message": "Set OPENWEATHER_API_KEY on the backend."}
        indices = self._sample_indices(len(coordinates), self.MAX_ROUTE_POINTS)
        locations = []
        errors = []
        labels = ("Start",) if len(indices) == 1 else ("Start", "Destination") if len(indices) == 2 else ("Start", "Along route", "Destination")
        for index, label in zip(indices, labels):
            longitude, latitude = coordinates[index]
            try:
                locations.append({"route_position": label, **self._location_weather(latitude, longitude)})
            except (WeatherUnavailable, KeyError, TypeError, ValueError, AttributeError) as error:
                message = str(error) if isinstance(error, WeatherUnavailable) else "OpenWeatherMap returned incomplete weather data."
                logger.warning("Weather lookup failed for route sample %s: %s", label, message)
                errors.append(message)
        if not locations:
            return {"status": "unavailable", "provider": "OpenWeatherMap", "message": errors[0] if errors else "No route weather data is available."}
        return {
            "status": "available" if not errors else "partial",
            "provider": "OpenWeatherMap",
            "retrieved_at": max(item["retrieved_at"] for item in locations),
            "stale_after_seconds": self.CACHE_TTL_SECONDS,
            "locations": locations,
            "unavailable_locations": len(errors),
            "message": errors[0] if errors else None,
            "limitations": ["Forecasts are provider estimates and do not guarantee route safety.", "Weather is sampled at up to three points along the route."],
        }


# WMO weather interpretation codes used by Open-Meteo.
WMO_CODES = {
    0: ("Clear sky", "01"), 1: ("Mainly clear", "02"), 2: ("Partly cloudy", "03"), 3: ("Overcast", "04"),
    45: ("Fog", "50"), 48: ("Freezing fog", "50"),
    51: ("Light drizzle", "09"), 53: ("Drizzle", "09"), 55: ("Heavy drizzle", "09"),
    56: ("Freezing drizzle", "09"), 57: ("Heavy freezing drizzle", "09"),
    61: ("Light rain", "10"), 63: ("Rain", "10"), 65: ("Heavy rain", "10"),
    66: ("Freezing rain", "13"), 67: ("Heavy freezing rain", "13"),
    71: ("Light snow", "13"), 73: ("Snow", "13"), 75: ("Heavy snow", "13"), 77: ("Snow grains", "13"),
    80: ("Light rain showers", "09"), 81: ("Rain showers", "09"), 82: ("Violent rain showers", "09"),
    85: ("Snow showers", "13"), 86: ("Heavy snow showers", "13"),
    95: ("Thunderstorm", "11"), 96: ("Thunderstorm with hail", "11"), 99: ("Severe thunderstorm with hail", "11"),
}


def describe_wmo(code: Any, is_day: Any = 1) -> tuple[str, str]:
    try:
        label, icon = WMO_CODES.get(int(code), ("Unknown", "03"))
    except (TypeError, ValueError):
        label, icon = "Unknown", "03"
    return label, icon + ("d" if is_day in (1, True, None) else "n")


class OpenMeteoAgent(OpenWeatherAgent):
    """Keyless weather from Open-Meteo, returned in the same shape as OpenWeatherAgent."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"
    PROVIDER = "Open-Meteo"

    def __init__(self, fetcher: Callable[[str], Any] | None = None):
        super().__init__(api_key="open-meteo", fetcher=fetcher or self._fetch_open_meteo)

    @staticmethod
    def _fetch_open_meteo(url: str) -> Any:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "NAVIRA/0.1"})
        try:
            try:
                with urlopen(request, timeout=8) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (URLError, TimeoutError, ConnectionError) as first_error:
                if isinstance(first_error, HTTPError):
                    raise
                time.sleep(0.4)  # one retry for a dropped connection
                with urlopen(request, timeout=8) as response:
                    return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 429:
                raise WeatherUnavailable("Open-Meteo request limit reached. Try again later.") from error
            raise WeatherUnavailable(f"Open-Meteo returned HTTP {error.code}.") from error
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise WeatherUnavailable("Open-Meteo could not be reached.") from error

    def _location_weather(self, latitude: float, longitude: float) -> dict[str, Any]:
        cache_key = f"{latitude:.3f}:{longitude:.3f}"
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < self.CACHE_TTL_SECONDS:
                return dict(cached[1])
        query = urlencode({
            "latitude": f"{latitude:.5f}", "longitude": f"{longitude:.5f}",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,is_day",
            "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
            "forecast_hours": 24, "wind_speed_unit": "kmh", "timezone": "auto", "timeformat": "unixtime",
        })
        data = self.fetcher(f"{self.BASE_URL}?{query}")
        if not isinstance(data, dict) or data.get("error"):
            raise WeatherUnavailable(str((data or {}).get("reason") or "Open-Meteo returned an invalid response.") if isinstance(data, dict) else "Open-Meteo returned an invalid response.")
        current = data.get("current") or {}
        if not all(isinstance(current.get(f), (int, float)) and math.isfinite(current[f]) for f in ("temperature_2m", "apparent_temperature", "relative_humidity_2m")):
            raise WeatherUnavailable("Open-Meteo returned incomplete current conditions.")
        condition, icon = describe_wmo(current.get("weather_code"), current.get("is_day"))
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        offset = int(data.get("utc_offset_seconds") or 0)
        forecast = []
        for i in range(0, min(len(times), 24), 3):  # 3-hourly, like OpenWeatherMap's forecast
            temp = (hourly.get("temperature_2m") or [None] * len(times))[i]
            if temp is None:
                continue
            label, item_icon = describe_wmo((hourly.get("weather_code") or [None] * len(times))[i], (hourly.get("is_day") or [1] * len(times))[i])
            rain = (hourly.get("precipitation_probability") or [None] * len(times))[i]
            forecast.append({
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(times[i]) + offset)),
                "temperature_c": round(float(temp)),
                "condition": label,
                "icon": item_icon,
                "rain_probability": round(float(rain)) if rain is not None else 0,
            })
        payload = {
            "status": "available",
            "location": "Route location",
            "latitude": latitude,
            "longitude": longitude,
            "current": {
                "temperature_c": round(float(current["temperature_2m"])),
                "feels_like_c": round(float(current["apparent_temperature"])),
                "humidity_percent": round(float(current["relative_humidity_2m"])),
                "wind_speed_kmh": round(float(current.get("wind_speed_10m") or 0), 1),
                "condition": condition,
                "icon": icon,
            },
            "forecast": forecast,
            "rain_probability_percent": max((item["rain_probability"] for item in forecast), default=None),
            "alerts": [],
            "alerts_note": "Official severe-weather alerts are not included; check IMD (mausam.imd.gov.in) for warnings.",
            "observed_at": current.get("time"),
            "retrieved_at": time.time(),
            "provider": self.PROVIDER,
        }
        with self._lock:
            self._cache[cache_key] = (time.monotonic(), payload)
            if len(self._cache) > 500:
                self._cache.pop(next(iter(self._cache)))
        return dict(payload)

    def route_weather(self, coordinates: list[list[float]]) -> dict[str, Any]:
        result = super().route_weather(coordinates)
        if result.get("provider") == "OpenWeatherMap":
            result["provider"] = self.PROVIDER
            if isinstance(result.get("message"), str):
                result["message"] = result["message"].replace("OpenWeatherMap", self.PROVIDER)
        for item in result.get("locations") or []:
            item["provider"] = self.PROVIDER
        return result


_weather_agent = OpenWeatherAgent()
_open_meteo_agent = OpenMeteoAgent()
_agent_lock = threading.Lock()


def get_route_weather(coordinates: list[list[float]]) -> dict[str, Any]:
    """OpenWeatherMap when OPENWEATHER_API_KEY is set and working; otherwise Open-Meteo (no key)."""
    global _weather_agent
    provider = os.getenv("NAVIRA_WEATHER_PROVIDER", "auto").strip().lower()
    if provider == "open-meteo":
        return _open_meteo_agent.route_weather(coordinates)
    api_key = os.getenv("OPENWEATHER_API_KEY", "")
    if _weather_agent.api_key != api_key:
        with _agent_lock:
            if _weather_agent.api_key != api_key:
                _weather_agent = OpenWeatherAgent(api_key=api_key)
    if api_key:
        result = _weather_agent.route_weather(coordinates)
        if result.get("status") in ("available", "partial") or provider == "openweather":
            return result
        logger.warning("OpenWeatherMap failed (%s); falling back to Open-Meteo.", result.get("message"))
    elif provider == "openweather":
        return _weather_agent.route_weather(coordinates)
    return _open_meteo_agent.route_weather(coordinates)
