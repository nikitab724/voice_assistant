"""Weather workflow tools using Open-Meteo API (free, no API key needed)."""

from __future__ import annotations

import asyncio
from datetime import datetime, date as date_obj, timedelta
from typing import Any, Literal, Optional

import httpx
from fastmcp import Context
from pydantic import BaseModel

from user_context import get_user_location


# Weather code descriptions
WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


class CurrentWeather(BaseModel):
    temperature_f: int
    feels_like_f: int
    humidity: int
    wind_mph: float
    condition: str
    is_day: bool


class DailyForecast(BaseModel):
    date: str
    high_f: int
    low_f: int
    condition: str
    precipitation_chance: int


class WeatherResult(BaseModel):
    status: Literal["success", "error"]
    location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    current: Optional[CurrentWeather] = None
    forecast: Optional[list[DailyForecast]] = None
    message: Optional[str] = None


async def get_weather_tool(
    *,
    location: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    date: str | None = None,
    datetime_iso: str | None = None,
    include_forecast: bool = True,
    context: Context | None = None,
) -> dict[str, Any]:
    """
    Get current weather and forecast. Provide either location name OR lat/lon coordinates.
    If no location provided, uses user's current location from device.
    Optional:
    - date: YYYY-MM-DD to get weather for a specific day
    - datetime_iso: ISO-8601 datetime (e.g. 2025-12-25T15:30:00-06:00). Date portion is used.
    """
    try:
        # Determine if we are looking for a specific date.
        # If datetime_iso is provided, we use its date portion (in the provided offset/timezone).
        # If date is provided, it takes precedence.
        target_date: date_obj | None = None
        if datetime_iso and not date:
            try:
                iso = datetime_iso.replace("Z", "+00:00")
                target_date = datetime.fromisoformat(iso).date()
                date = target_date.strftime("%Y-%m-%d")
            except ValueError:
                return WeatherResult(
                    status="error",
                    message=f"Invalid datetime_iso: {datetime_iso}. Please use ISO-8601 (e.g. 2025-12-25T15:30:00-06:00).",
                ).model_dump()

        if date:
            try:
                target_date = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                return WeatherResult(
                    status="error",
                    message=f"Invalid date format: {date}. Please use YYYY-MM-DD.",
                ).model_dump()

        async with httpx.AsyncClient(timeout=10) as client:
            # Step 1: Resolve coordinates.
            # IMPORTANT: The weather API call always uses latitude/longitude.
            resolved_location = None

            if latitude is not None and longitude is not None:
                resolved_location = f"{latitude:.2f}, {longitude:.2f}"
            elif location:
                # Geocode the user-provided place name into coordinates.
                geo_url = "https://geocoding-api.open-meteo.com/v1/search"
                query = location
                geo_resp = await client.get(geo_url, params={"name": query, "count": 1})
                geo_data = geo_resp.json()
                results = geo_data.get("results", []) or []

                # Simple fallback: try the part before the comma.
                if not results and "," in query:
                    query2 = query.split(",", 1)[0].strip()
                    if query2:
                        geo_resp = await client.get(geo_url, params={"name": query2, "count": 1})
                        geo_data = geo_resp.json()
                        results = geo_data.get("results", []) or []

                if not results:
                    return WeatherResult(
                        status="error",
                        message=f"Could not find location: {location}",
                    ).model_dump()

                place = results[0]
                latitude = float(place["latitude"])
                longitude = float(place["longitude"])
                parts = [p for p in [place.get("name"), place.get("admin1"), place.get("country")] if p]
                resolved_location = ", ".join(parts) if parts else location
            else:
                # Use the user's device location (set by the iOS app).
                user_lat, user_lon = get_user_location()
                if user_lat is None or user_lon is None:
                    return WeatherResult(
                        status="error",
                        message="No location provided and device location unavailable. Please specify a city.",
                    ).model_dump()

                latitude = float(user_lat)
                longitude = float(user_lon)

                # Optional: reverse geocode for nicer display.
                try:
                    reverse_url = "https://nominatim.openstreetmap.org/reverse"
                    reverse_resp = await client.get(
                        reverse_url,
                        params={
                            "lat": latitude,
                            "lon": longitude,
                            "format": "json",
                            "zoom": 18,
                            "addressdetails": 1,
                        },
                        headers={"User-Agent": "VoiceAssistant/1.0"},
                    )
                    reverse_data = reverse_resp.json()
                    addr = reverse_data.get("address", {}) or {}
                    city = (
                        addr.get("city")
                        or addr.get("town")
                        or addr.get("village")
                        or addr.get("municipality")
                        or addr.get("suburb")
                        or addr.get("county")
                        or ""
                    )
                    state = addr.get("state") or ""
                    parts = [p for p in [city, state] if p]
                    resolved_location = ", ".join(parts) if parts else "your location"
                except Exception:
                    resolved_location = "your location"

            # Final sanity: ensure we have coordinates before hitting weather endpoint.
            if latitude is None or longitude is None:
                return WeatherResult(
                    status="error",
                    message="Missing latitude/longitude after location resolution.",
                ).model_dump()
            
            # Step 2: Get weather
            today = date_obj.today()
            is_historical = target_date and target_date < today
            is_far_future = target_date and target_date > today + timedelta(days=16)
            
            if is_far_future:
                return WeatherResult(
                    status="error",
                    message="Weather forecast is only available for up to 16 days in the future.",
                ).model_dump()

            if is_historical:
                weather_url = "https://archive-api.open-meteo.com/v1/archive"
            else:
                weather_url = "https://api.open-meteo.com/v1/forecast"
            
            params = {
                "latitude": latitude,
                "longitude": longitude,
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "auto",
            }

            if target_date:
                # For a specific date, we get the daily summary for that date
                params["start_date"] = date
                params["end_date"] = date
                params["daily"] = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                if is_historical:
                    # Archive API doesn't have precipitation_probability_max, use precipitation_sum
                    params["daily"] = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum"
            else:
                # Current weather + optional 5-day forecast
                params["current"] = "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,is_day"
                if include_forecast:
                    params["daily"] = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                    params["forecast_days"] = 5
            
            weather_resp = await client.get(weather_url, params=params)
            weather_data = weather_resp.json()
            
            # Check for API error
            if "error" in weather_data:
                return WeatherResult(
                    status="error",
                    message=f"API Error: {weather_data.get('reason', 'Unknown error')}",
                ).model_dump()

            # Parse current weather (only if not a specific date)
            current = None
            if not target_date:
                current_data = weather_data.get("current", {})
                current = CurrentWeather(
                    temperature_f=int(current_data.get("temperature_2m", 0)),
                    feels_like_f=int(current_data.get("apparent_temperature", 0)),
                    humidity=current_data.get("relative_humidity_2m", 0),
                    wind_mph=round(current_data.get("wind_speed_10m", 0), 1),
                    condition=WMO_CODES.get(current_data.get("weather_code", 0), "Unknown"),
                    is_day=bool(current_data.get("is_day", 1)),
                )
            
            # Parse forecast or specific date result
            forecast = None
            if include_forecast or target_date:
                daily = weather_data.get("daily", {})
                dates = daily.get("time", [])
                highs = daily.get("temperature_2m_max", [])
                lows = daily.get("temperature_2m_min", [])
                codes = daily.get("weather_code", [])
                
                # Precipitation field name varies between forecast and archive
                precip = daily.get("precipitation_probability_max") or daily.get("precipitation_sum") or []
                
                forecast = []
                for i in range(len(dates)):
                    forecast.append(DailyForecast(
                        date=dates[i],
                        high_f=int(highs[i]) if i < len(highs) else 0,
                        low_f=int(lows[i]) if i < len(lows) else 0,
                        condition=WMO_CODES.get(codes[i] if i < len(codes) else 0, "Unknown"),
                        precipitation_chance=int(precip[i]) if i < len(precip) else 0,
                    ))
            
            if context:
                if target_date and forecast:
                    await context.info(f"Weather for {resolved_location} on {date}: {forecast[0].high_f}°F/{forecast[0].low_f}°F, {forecast[0].condition}")
                elif current:
                    await context.info(f"Weather for {resolved_location}: {current.temperature_f}°F, {current.condition}")
            
            return WeatherResult(
                status="success",
                location=resolved_location,
                latitude=float(latitude) if latitude is not None else None,
                longitude=float(longitude) if longitude is not None else None,
                current=current,
                forecast=forecast,
            ).model_dump()
            
    except Exception as exc:
        msg = f"Failed to get weather: {exc}"
        if context:
            await context.error(msg)
        return WeatherResult(status="error", message=msg).model_dump()
