from dataclasses import dataclass
from typing import cast

from trips import wording
from trips.domain import (
    Assessment,
    Checkpoint,
    Level,
    Levels,
    MilesByLevel,
    Route,
    RouteSummary,
    Weather,
)


@dataclass(frozen=True)
class Limits:
    """One condition's limits: the level each value reaches, and where No Travel starts.

    No Travel starts above its limit for rain and snow. For wind it starts at the limit,
    which is the only place these tables differ.
    """

    steps: tuple[tuple[float, Level], ...]
    no_travel: float
    no_travel_at_limit: bool = False


WIND = Limits(
    steps=((25.0, Level.MODERATE), (35.0, Level.HIGH), (45.0, Level.SEVERE)),
    no_travel=55.0,
    no_travel_at_limit=True,
)
RAIN = Limits(
    steps=((0.10, Level.MODERATE), (0.25, Level.HIGH), (0.50, Level.SEVERE)),
    no_travel=1.00,
)
SNOW = Limits(
    steps=((0.5, Level.MODERATE), (1.0, Level.HIGH), (2.0, Level.SEVERE)),
    no_travel=3.0,
)


def _level_for(value: float, limits: Limits) -> Level:
    level = Level.LOW
    for limit, band in limits.steps:
        if value >= limit:
            level = band
    if value > limits.no_travel or (limits.no_travel_at_limit and value == limits.no_travel):
        return Level.NO_TRAVEL
    return level


@dataclass(frozen=True)
class LoadRule:
    """Wind in this range, with a load over this many pounds, lifts the wind level."""

    from_mph: float
    to_mph: float
    over_lb: int
    level: Level


LOAD_RULES = (
    LoadRule(45.0, 55.0, 30_000, Level.NO_TRAVEL),
    LoadRule(35.0, 45.0, 40_000, Level.SEVERE),
)


def _load_level(mph: float, load_lb: int) -> Level | None:
    for rule in LOAD_RULES:
        if rule.from_mph <= mph < rule.to_mph and load_lb > rule.over_lb:
            return rule.level
    return None


def _wind_level(mph: float, load_lb: int) -> Level:
    return max(_level_for(mph, WIND), _load_level(mph, load_lb) or Level.LOW)


def level_of(wind_mph: float, rain_in_hr: float, snow_in_hr: float, load_lb: int) -> Level:
    """The level `classify` gives, without the per-condition levels or the cause."""
    return max(
        _wind_level(wind_mph, load_lb),
        _level_for(rain_in_hr, RAIN),
        _level_for(snow_in_hr, SNOW),
    )


def classify(weather: Weather, load_lb: int) -> Assessment:
    levels = Levels(
        wind=_wind_level(weather.wind_mph, load_lb),
        rain=_level_for(weather.rain_in_hr, RAIN),
        snow=_level_for(weather.snow_in_hr, SNOW),
    )
    level = max(levels.wind, levels.rain, levels.snow)

    if level == Level.LOW:
        cause = "no condition above Low"
    elif levels.wind == level:
        cause = f"wind {weather.wind_mph:.1f} mph"
        if _load_level(weather.wind_mph, load_lb) is not None:
            cause += f" with {load_lb:,} lb load"
    elif levels.snow == level:
        cause = f"snow {weather.snow_in_hr:.2f} in/hr"
    else:
        cause = f"rain {weather.rain_in_hr:.2f} in/hr"
    return Assessment(levels=levels, level=level, cause=cause)


def covered_miles(miles: list[float], total_mi: float) -> list[float]:
    covers = []
    for index, mile in enumerate(miles):
        previous = miles[index - 1] if index else mile
        following = miles[index + 1] if index + 1 < len(miles) else total_mi
        covers.append((following - previous) / 2)
    return covers


def summarize(checkpoints: list[Checkpoint], distance_mi: float) -> RouteSummary:
    by_level = [0.0] * len(Level)
    score = 0.0
    for checkpoint in checkpoints:
        by_level[checkpoint.level] += checkpoint.covers_mi
        score += int(checkpoint.level) * checkpoint.covers_mi
    worst = max(checkpoint.level for checkpoint in checkpoints)
    miles_by_level = cast("MilesByLevel", tuple(round(miles, 1) for miles in by_level))
    return RouteSummary(
        miles_by_level=miles_by_level,
        avg_risk=round(score / distance_mi, 2),
        worst=worst,
    )


def _rank_key(route: Route) -> tuple[float, ...]:
    miles = route.summary.miles_by_level
    return (
        miles[Level.NO_TRAVEL],
        miles[Level.SEVERE],
        miles[Level.HIGH],
        route.summary.avg_risk,
        route.duration_s,
    )


def rank(routes: list[Route]) -> tuple[list[Route], str]:
    ranked = sorted(routes, key=_rank_key)
    return ranked, wording.reason(ranked)
