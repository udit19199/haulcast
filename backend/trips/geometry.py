from bisect import bisect_right
from itertools import pairwise
from math import asin, ceil, cos, hypot, radians, sin, sqrt

from trips.domain import Cell, TrackPoint

EARTH_RADIUS_MI = 3958.7613
CORRIDOR_RADIUS_MI = 15.0
MILES_PER_DEG_LAT = 69.17
MIN_TRACK_SPACING_MI = 0.2
MAX_DRIVING_S = 11 * 3600
REST_BREAK_S = 10 * 3600


def elapsed_seconds(driving_s: int) -> int:
    if driving_s <= 0:
        return 0
    return driving_s + REST_BREAK_S * (ceil(driving_s / MAX_DRIVING_S) - 1)


def track_with_breaks(track: list[TrackPoint]) -> list[TrackPoint]:
    elapsed_track = [(track[0][0], track[0][1], elapsed_seconds(track[0][2]))]
    boundary = MAX_DRIVING_S
    for start, end in pairwise(track):
        while boundary < end[2]:
            fraction = (boundary - start[2]) / (end[2] - start[2])
            lon = start[0] + (end[0] - start[0]) * fraction
            lat = start[1] + (end[1] - start[1]) * fraction
            break_start = (lon, lat, elapsed_seconds(boundary))
            if elapsed_track[-1] != break_start:
                elapsed_track.append(break_start)
            elapsed_track.append((lon, lat, break_start[2] + REST_BREAK_S))
            boundary += MAX_DRIVING_S
        elapsed_track.append((end[0], end[1], elapsed_seconds(end[2])))
    return elapsed_track


def haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_MI * asin(sqrt(a))


def cumulative_miles(track: list[TrackPoint]) -> list[float]:
    cumulative = [0.0]
    for previous, current in pairwise(track):
        cumulative.append(
            cumulative[-1] + haversine(previous[0], previous[1], current[0], current[1])
        )
    return cumulative


def point_and_time_at(track: list[TrackPoint], cumulative: list[float], mile: float) -> TrackPoint:
    if mile <= 0:
        return track[0]
    if mile >= cumulative[-1]:
        return track[-1]

    index = bisect_right(cumulative, mile) - 1
    span = cumulative[index + 1] - cumulative[index]
    fraction = (mile - cumulative[index]) / span if span else 0.0
    start, end = track[index], track[index + 1]
    lon = start[0] + (end[0] - start[0]) * fraction
    lat = start[1] + (end[1] - start[1]) * fraction
    seconds = round(start[2] + (end[2] - start[2]) * fraction)
    return (lon, lat, seconds)


def thin_track(track: list[TrackPoint]) -> list[TrackPoint]:
    if len(track) < 2:
        return track

    cumulative = cumulative_miles(track)
    stationary = {
        index
        for index in range(len(track))
        if (index > 0 and track[index][:2] == track[index - 1][:2])
        or (index + 1 < len(track) and track[index][:2] == track[index + 1][:2])
    }
    kept = [0]
    for index in range(1, len(track)):
        if index in stationary or cumulative[index] - cumulative[kept[-1]] >= MIN_TRACK_SPACING_MI:
            kept.append(index)
    if kept[-1] != len(track) - 1:
        if (
            cumulative[-1] - cumulative[kept[-1]] < MIN_TRACK_SPACING_MI
            and len(kept) > 1
            and kept[-1] not in stationary
        ):
            kept.pop()
        kept.append(len(track) - 1)
    return [track[index] for index in kept]


def cell_of(lon: float, lat: float, cell_deg: float) -> Cell:
    return (round(lon / cell_deg) * cell_deg, round(lat / cell_deg) * cell_deg)


def _corridor_radius_mi(lat: float, cell_deg: float) -> float:
    height_mi = cell_deg * MILES_PER_DEG_LAT
    width_mi = height_mi * cos(radians(lat))
    return max(CORRIDOR_RADIUS_MI, hypot(height_mi, width_mi) / 2)


def _add_cells_near(cells: set[Cell], lon: float, lat: float, cell_deg: float) -> None:
    radius_mi = _corridor_radius_mi(lat, cell_deg)
    centre_lon, centre_lat = round(lon / cell_deg), round(lat / cell_deg)
    span_lat = ceil(radius_mi / (cell_deg * MILES_PER_DEG_LAT))
    span_lon = ceil(radius_mi / (cell_deg * MILES_PER_DEG_LAT * cos(radians(lat))))
    for offset_lon in range(-span_lon, span_lon + 1):
        cell_lon = (centre_lon + offset_lon) * cell_deg
        for offset_lat in range(-span_lat, span_lat + 1):
            cell = (cell_lon, (centre_lat + offset_lat) * cell_deg)
            if cell not in cells and haversine(lon, lat, *cell) <= radius_mi:
                cells.add(cell)


def corridor_cells(
    tracks: list[list[TrackPoint]], cell_deg: float, limit: int | None = None
) -> set[Cell]:
    """Cells near any track. Stops early, with a partial set, once it has more than `limit`."""
    cells: set[Cell] = set()
    # Alternatives share long stretches, so the same sample point recurs across tracks.
    sampled: set[tuple[float, float]] = set()

    def add_near(lon: float, lat: float) -> bool:
        if (lon, lat) not in sampled:
            sampled.add((lon, lat))
            _add_cells_near(cells, lon, lat, cell_deg)
        return limit is not None and len(cells) > limit

    for track in tracks:
        cells.update(cell_of(lon, lat, cell_deg) for lon, lat, _seconds in track)
        if add_near(track[-1][0], track[-1][1]):
            return cells
        for start, end in pairwise(track):
            steps = max(1, ceil(haversine(start[0], start[1], end[0], end[1]) * 2))
            for step in range(steps):
                fraction = step / steps
                lon = start[0] + (end[0] - start[0]) * fraction
                lat = start[1] + (end[1] - start[1]) * fraction
                if add_near(lon, lat):
                    return cells
    return cells
