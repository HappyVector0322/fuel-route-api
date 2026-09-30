"""In-memory spatial index of fuel stations.

All ~6.7k stations are loaded from the DB once per process into numpy arrays, so
matching stations to a route is pure in-memory math (a few milliseconds).
"""
import threading
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from routing.models import FuelStation

from .geo import chord_to_miles, densify, miles_to_chord, to_unit_vectors

DENSIFY_STEP_MILES = 0.25

_lock = threading.Lock()
_index = None


@dataclass(frozen=True)
class RouteStation:
    station: dict
    mile: float  # distance along the route from the start
    off_route_miles: float
    price: float


class StationIndex:
    def __init__(self, stations):
        self.stations = [
            {
                'opis_id': s.opis_id,
                'name': s.name,
                'address': s.address,
                'city': s.city,
                'state': s.state,
                'latitude': s.latitude,
                'longitude': s.longitude,
            }
            for s in stations
        ]
        self.lat = np.array([s.latitude for s in stations], dtype=float)
        self.lng = np.array([s.longitude for s in stations], dtype=float)
        self.price = np.array([float(s.retail_price) for s in stations], dtype=float)
        self.xyz = to_unit_vectors(self.lat, self.lng) if stations else np.empty((0, 3))

    def __len__(self):
        return len(self.stations)

    def along_route(self, route_lats, route_lngs, max_distance_miles):
        """Stations within `max_distance_miles` of the route, with their mile marker."""
        if not len(self):
            return []
        lats, lngs, cum_miles = densify(
            np.asarray(route_lats, dtype=float), np.asarray(route_lngs, dtype=float),
            DENSIFY_STEP_MILES,
        )
        # Cheap bounding-box pre-filter (~0.1 deg lat ~= 7 mi margin) before the KD-tree.
        margin = max_distance_miles / 69.0 + 0.1
        in_box = np.flatnonzero(
            (self.lat >= lats.min() - margin) & (self.lat <= lats.max() + margin)
            & (self.lng >= lngs.min() - margin * 1.5) & (self.lng <= lngs.max() + margin * 1.5)
        )
        if not len(in_box):
            return []
        tree = cKDTree(to_unit_vectors(lats, lngs))
        chord, nearest = tree.query(
            self.xyz[in_box], distance_upper_bound=miles_to_chord(max_distance_miles)
        )
        hit = np.isfinite(chord)
        return [
            RouteStation(
                station=self.stations[i],
                mile=float(cum_miles[n]),
                off_route_miles=float(chord_to_miles(c)),
                price=float(self.price[i]),
            )
            for i, n, c in zip(in_box[hit], nearest[hit], chord[hit])
        ]


def get_station_index():
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = StationIndex(list(FuelStation.objects.all()))
    return _index


def invalidate_station_index():
    global _index
    with _lock:
        _index = None
