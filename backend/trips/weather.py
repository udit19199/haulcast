"""Open-Meteo hourly forecast, cached per cell as one series from its start hour."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from django.core.cache import cache
from pydantic import BaseModel, ValidationError

from trips.domain import Cell, Weather
from trips.remote import ProviderError, chunks, get_json, on_hour

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SERVICE = "The weather service"
OPENMETEO_FIELDS = ("wind_speed_10m", "wind_gusts_10m", "rain", "showers", "snowfall")
MAX_GAP_FILL_HOURS = 3
WEATHER_CACHE_SECONDS = 30 * 60


class _Hourly(BaseModel):
    time: list[datetime]
    wind_speed_10m: list[float | None]
    wind_gusts_10m: list[float | None]
    rain: list[float | None]
    showers: list[float | None]
    snowfall: list[float | None]


class _Forecast(BaseModel):
    hourly: _Hourly


def hourly(cells: list[Cell], start: datetime, end: datetime) -> dict[Cell, list[Weather]]:
    start = on_hour(start)
    hours = max(1, int((on_hour(end) - start).total_seconds() // 3600) + 1)

    keys = {cell: _cache_key(cell) for cell in cells}
    cached = cache.get_many(list(keys.values()))

    series: dict[Cell, list[Weather]] = {}
    missing: list[Cell] = []
    for cell, key in keys.items():
        window = _cached_window(cached.get(key), start, hours)
        if window is None:
            missing.append(cell)
        else:
            series[cell] = window

    with ThreadPoolExecutor(max_workers=4) as pool:
        for fetched in pool.map(lambda chunk: _fetch(chunk, start, hours), chunks(missing, 100)):
            series.update(fetched)
    return series


def _fetch(cells: list[Cell], start: datetime, hours: int) -> dict[Cell, list[Weather]]:
    end = start + timedelta(hours=hours - 1)
    data = get_json(
        OPEN_METEO_URL,
        {
            "latitude": ",".join(f"{cell[1]:.2f}" for cell in cells),
            "longitude": ",".join(f"{cell[0]:.2f}" for cell in cells),
            "hourly": ",".join(OPENMETEO_FIELDS),
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "GMT",
            "start_hour": start.strftime("%Y-%m-%dT%H:%M"),
            "end_hour": end.strftime("%Y-%m-%dT%H:%M"),
        },
        SERVICE,
    )
    items = [data] if isinstance(data, dict) else data
    if not isinstance(items, list) or len(items) != len(cells):
        raise ProviderError(f"{SERVICE} returned an unexpected response.")

    try:
        forecasts = [_Forecast.model_validate(item).hourly for item in items]
    except ValidationError:
        raise ProviderError(f"{SERVICE} returned an unexpected response.") from None

    result: dict[Cell, list[Weather]] = {}
    for cell, block in zip(cells, forecasts, strict=True):
        fields = {name: getattr(block, name) for name in OPENMETEO_FIELDS}
        if (
            len(block.time) != hours
            or block.time[0].replace(tzinfo=UTC) != start
            or any(len(values) != hours for values in fields.values())
        ):
            raise ProviderError(f"{SERVICE} returned an unexpected response.")

        wind, gust, rain, showers, snow = (_fill_gaps(fields[name]) for name in OPENMETEO_FIELDS)
        gust = gust or wind
        if wind is None or rain is None or showers is None or snow is None or gust is None:
            raise ProviderError(f"{SERVICE} returned incomplete data.")

        forecast = [
            Weather(
                wind_mph=wind[hour],
                gust_mph=gust[hour],
                rain_in_hr=rain[hour] + showers[hour],
                snow_in_hr=snow[hour],
            )
            for hour in range(hours)
        ]
        result[cell] = forecast
    cache.set_many(
        {_cache_key(cell): (start, forecast) for cell, forecast in result.items()},
        WEATHER_CACHE_SECONDS,
    )
    return result


def _fill_gaps(values: list[float | None]) -> list[float] | None:
    known = [(index, value) for index, value in enumerate(values) if value is not None]
    if not known:
        return None
    filled: list[float] = []
    for index, value in enumerate(values):
        if value is None:
            near = min(known, key=lambda pair: abs(pair[0] - index))
            if abs(near[0] - index) > MAX_GAP_FILL_HOURS:
                raise ProviderError(f"{SERVICE} returned incomplete data.")
            value = near[1]
        filled.append(value)
    return filled


def _cached_window(
    entry: tuple[datetime, list[Weather]] | None, start: datetime, hours: int
) -> list[Weather] | None:
    if entry is None:
        return None
    cached_start, forecast = entry
    offset = int((start - cached_start).total_seconds() // 3600)
    if offset < 0 or offset + hours > len(forecast):
        return None
    return forecast[offset : offset + hours]


def _cache_key(cell: Cell) -> str:
    return f"weather:{cell[0]:.2f}:{cell[1]:.2f}"
