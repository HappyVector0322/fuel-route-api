"""Client for the OSRM routing API (free public demo server by default, no key).

Exactly one HTTP call per route: it returns distance, duration and the full
road geometry, which is all the planner needs.
"""
from dataclasses import dataclass

from django.conf import settings

from .http import ExternalServiceError, get_json

METERS_PER_MILE = 1609.344


class RouteNotFoundError(Exception):
    pass


@dataclass
class Route:
    distance_miles: float
    duration_seconds: float
    lats: list
    lngs: list


def fetch_route(start, finish):
    """`start`/`finish` are Location objects (anything with .lat/.lng)."""
    coords = f'{start.lng:.6f},{start.lat:.6f};{finish.lng:.6f},{finish.lat:.6f}'
    url = f"{settings.FUELROUTE['OSRM_BASE_URL'].rstrip('/')}/route/v1/driving/{coords}"
    status, data = get_json(url, params={'overview': 'full', 'geometries': 'geojson'})

    code = data.get('code')
    if code in ('NoRoute', 'NoSegment'):
        raise RouteNotFoundError(data.get('message') or 'No drivable route between these points.')
    if status != 200 or code != 'Ok' or not data.get('routes'):
        raise ExternalServiceError(
            f"Routing API error (HTTP {status}, code={code}): {data.get('message', '')}".strip()
        )

    route = data['routes'][0]
    coordinates = route['geometry']['coordinates']  # GeoJSON order: [lng, lat]
    return Route(
        distance_miles=route['distance'] / METERS_PER_MILE,
        duration_seconds=route['duration'],
        lats=[c[1] for c in coordinates],
        lngs=[c[0] for c in coordinates],
    )
