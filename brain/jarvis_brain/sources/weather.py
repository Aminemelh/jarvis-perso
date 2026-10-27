"""Météo via Open-Meteo : gratuit, sans clé API."""

from __future__ import annotations

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Codes météo WMO -> description en français
WMO = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant",
    51: "bruine légère", 53: "bruine", 55: "bruine forte",
    56: "bruine verglaçante", 57: "bruine verglaçante forte",
    61: "pluie légère", 63: "pluie", 65: "forte pluie",
    66: "pluie verglaçante", 67: "forte pluie verglaçante",
    71: "neige légère", 73: "neige", 75: "forte neige", 77: "grains de neige",
    80: "averses légères", 81: "averses", 82: "fortes averses",
    85: "averses de neige", 86: "fortes averses de neige",
    95: "orages", 96: "orages avec grêle", 99: "orages violents avec grêle",
}


class WeatherClient:
    def __init__(self, timezone: str, http: httpx.AsyncClient | None = None):
        self.timezone = timezone
        self.http = http or httpx.AsyncClient(timeout=15)
        self._geo_cache: dict[str, dict] = {}

    async def locate(self, place: str) -> dict:
        key = place.lower().strip()
        if key not in self._geo_cache:
            r = await self.http.get(GEOCODE_URL, params={"name": place, "count": 1, "language": "fr"})
            r.raise_for_status()
            results = r.json().get("results") or []
            if not results:
                raise LookupError(f"Lieu introuvable : {place}")
            self._geo_cache[key] = results[0]
        return self._geo_cache[key]

    async def forecast(self, place: str, days: int = 2) -> dict:
        loc = await self.locate(place)
        r = await self.http.get(FORECAST_URL, params={
            "latitude": loc["latitude"], "longitude": loc["longitude"],
            "timezone": self.timezone, "forecast_days": max(1, min(days, 7)),
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        })
        r.raise_for_status()
        data = r.json()
        cur = data.get("current", {})
        daily = data.get("daily", {})
        return {
            "lieu": f"{loc.get('name')}, {loc.get('admin1', '')}".strip(", "),
            "maintenant": {
                "temperature_c": cur.get("temperature_2m"),
                "ressenti_c": cur.get("apparent_temperature"),
                "conditions": WMO.get(cur.get("weather_code"), "inconnu"),
                "vent_kmh": cur.get("wind_speed_10m"),
                "precipitations_mm": cur.get("precipitation"),
            },
            "jours": [
                {
                    "date": d,
                    "conditions": WMO.get(code, "inconnu"),
                    "min_c": tmin, "max_c": tmax, "probabilite_pluie_pct": pp,
                }
                for d, code, tmin, tmax, pp in zip(
                    daily.get("time", []), daily.get("weather_code", []),
                    daily.get("temperature_2m_min", []), daily.get("temperature_2m_max", []),
                    daily.get("precipitation_probability_max", []),
                )
            ],
        }
