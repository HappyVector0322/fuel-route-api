"""Orchestrates a trip plan: geocode -> one routing call -> match stations -> optimise.

External calls per uncached request: 1 (OSRM), plus 1 Nominatim call for each
start/finish that is not a "City, ST" or "lat,lng" string. Results are cached,
so repeating a request (or opening its map) makes no external calls at all.
"""
import hashlib
import time
from urllib.parse import urlencode

import numpy as np
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from . import geocoding, routing_api
from .optimizer import plan_fuel_stops
from .stations import get_station_index

CACHE_VERSION = 'v1'


def _cache_key(start, finish, stop_penalty):
    raw = f'{CACHE_VERSION}|{start.strip().lower()}|{finish.strip().lower()}|{stop_penalty:g}'
    return 'plan:' + hashlib.sha1(raw.encode()).hexdigest()


def plan_trip(start_query, finish_query, stop_penalty=None):
    """Return ({plan, route_lnglat}, from_cache). Raises service exceptions on failure."""
    cfg = settings.FUELROUTE
    if stop_penalty is None:
        stop_penalty = cfg['STOP_PENALTY_DOLLARS']
    key = _cache_key(start_query, finish_query, stop_penalty)
    cached = cache.get(key)
    if cached is not None:
        return cached, True

    timings = {}
    calls = {'routing': 0, 'geocoding': 0}

    t = time.perf_counter()
    start = geocoding.geocode(start_query)
    finish = geocoding.geocode(finish_query)
    calls['geocoding'] = sum(loc.source == 'nominatim' for loc in (start, finish))
    timings['geocoding_ms'] = _ms(t)

    t = time.perf_counter()
    route = routing_api.fetch_route(start, finish)
    calls['routing'] = 1
    timings['routing_api_ms'] = _ms(t)

    t = time.perf_counter()
    candidates = get_station_index().along_route(
        route.lats, route.lngs, cfg['MAX_STATION_DISTANCE_MILES']
    )
    timings['station_matching_ms'] = _ms(t)

    t = time.perf_counter()
    plan = plan_fuel_stops(
        candidates, route.distance_miles, cfg['VEHICLE_RANGE_MILES'], cfg['VEHICLE_MPG'],
        stop_penalty=stop_penalty,
    )
    timings['optimization_ms'] = _ms(t)

    result = {
        'start': start.as_dict(),
        'finish': finish.as_dict(),
        'route': {
            'distance_miles': round(route.distance_miles, 1),
            'duration_hours': round(route.duration_seconds / 3600, 2),
            'stations_considered': len(candidates),
        },
        'vehicle': {
            'range_miles': cfg['VEHICLE_RANGE_MILES'],
            'mpg': cfg['VEHICLE_MPG'],
            'tank_gallons': cfg['VEHICLE_RANGE_MILES'] / cfg['VEHICLE_MPG'],
        },
        'optimization': {'stop_penalty': stop_penalty},
        'fuel_stops': [_stop_dict(n, stop) for n, stop in enumerate(plan.stops, 1)],
        'summary': {
            'total_fuel_cost': round(plan.total_cost, 2),
            'total_gallons': round(plan.total_gallons, 2),
            'number_of_stops': len(plan.stops),
            'average_price_per_gallon': (
                round(plan.total_cost / plan.total_gallons, 3) if plan.total_gallons else None
            ),
        },
        'assumptions': [
            f"The vehicle gets {cfg['VEHICLE_MPG']:g} mpg and has a "
            f"{cfg['VEHICLE_RANGE_MILES']:g}-mile range, so the tank holds "
            f"{cfg['VEHICLE_RANGE_MILES'] / cfg['VEHICLE_MPG']:g} gallons.",
            'The trip starts with an empty tank, and the first stop is the first station on '
            'the route. Fuel burned before that stop is charged at its price.',
            'Stops are chosen to minimise fuel cost plus a '
            f'${stop_penalty:g} penalty per stop, so the plan skips extra stops that would '
            'only save pennies. That penalty is not included in total_fuel_cost. '
            'Pass stop_penalty=0 for the absolute cheapest plan.',
            'The tank is empty on arrival, so no fuel is bought that the trip does not use.',
            'Station positions are approximate (city-level). A station counts as on the route '
            f"if it is within {cfg['MAX_STATION_DISTANCE_MILES']:g} miles of it.",
        ],
        'approach': {
            'miles_to_first_station': round(plan.approach_gallons * cfg['VEHICLE_MPG'], 1),
            'gallons': round(plan.approach_gallons, 2),
            'cost': round(plan.approach_cost, 2),
        },
        'meta': {'external_api_calls': calls, 'timings': timings},
    }
    cached = {
        'plan': result,
        'route_lnglat': np.round(np.column_stack((route.lngs, route.lats)), 5).tolist(),
    }
    cache.set(key, cached)
    return cached, False


def build_response(cached, from_cache, request, geometry='simplified'):
    """Shape the cached plan into the API response (adds map links and geometry)."""
    plan = dict(cached['plan'])
    plan['meta'] = dict(plan['meta'], cached=from_cache)
    if from_cache:
        plan['meta']['external_api_calls'] = {'routing': 0, 'geocoding': 0}

    query = urlencode({
        'start': plan['start']['query'], 'finish': plan['finish']['query'],
        'stop_penalty': f"{plan['optimization']['stop_penalty']:g}",
    })
    plan['map_url'] = request.build_absolute_uri(f"{reverse('route-map')}?{query}")

    if geometry != 'none':
        coords = cached['route_lnglat']
        if geometry == 'simplified':
            coords = simplify(coords, tolerance_deg=0.002)
        plan['geojson'] = to_geojson(plan, coords)
    return plan


def to_geojson(plan, route_lnglat):
    features = [{
        'type': 'Feature',
        'geometry': {'type': 'LineString', 'coordinates': route_lnglat},
        'properties': {'kind': 'route', 'distance_miles': plan['route']['distance_miles']},
    }]
    for kind, loc in (('start', plan['start']), ('finish', plan['finish'])):
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [loc['longitude'], loc['latitude']]},
            'properties': {'kind': kind, 'label': loc['label']},
        })
    for stop in plan['fuel_stops']:
        st = stop['station']
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [st['longitude'], st['latitude']]},
            'properties': {
                'kind': 'fuel_stop', 'stop_number': stop['stop_number'], 'name': st['name'],
                'price_per_gallon': stop['price_per_gallon'], 'gallons': stop['gallons'],
                'cost': stop['cost'], 'mile_marker': stop['mile_marker'],
            },
        })
    return {'type': 'FeatureCollection', 'features': features}


def simplify(coords, tolerance_deg):
    """Ramer-Douglas-Peucker line simplification (iterative, numpy-vectorised)."""
    pts = np.asarray(coords, dtype=float)
    if len(pts) < 3:
        return coords
    keep = np.zeros(len(pts), dtype=bool)
    keep[[0, -1]] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = pts[b] - pts[a]
        rel = pts[a + 1:b] - pts[a]
        norm = np.hypot(*seg)
        if norm == 0:
            dist = np.hypot(rel[:, 0], rel[:, 1])
        else:
            dist = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / norm
        i = int(np.argmax(dist))
        if dist[i] > tolerance_deg:
            mid = a + 1 + i
            keep[mid] = True
            stack.extend(((a, mid), (mid, b)))
    return pts[keep].tolist()


def _stop_dict(number, stop):
    c = stop.candidate
    return {
        'stop_number': number,
        'station': {k: c.station[k] for k in (
            'opis_id', 'name', 'address', 'city', 'state', 'latitude', 'longitude')},
        'mile_marker': round(c.mile, 1),
        'distance_off_route_miles': round(c.off_route_miles, 1),
        'price_per_gallon': round(c.price, 3),
        'gallons': round(stop.gallons, 2),
        'cost': round(stop.cost, 2),
        'fuel_on_arrival_gallons': round(stop.fuel_on_arrival_gallons, 2),
    }


def _ms(t):
    return round((time.perf_counter() - t) * 1000, 1)
