"""Turn user input into coordinates, avoiding external calls whenever possible.

Accepted inputs, in the order they are tried:
  1. "lat,lng"            e.g. "40.7128,-74.0060"          -> no API call
  2. "City, ST[, USA]"    e.g. "Dallas, TX" / "Dallas, Texas" -> offline gazetteer, no API call
  3. anything else        e.g. a street address or ZIP code -> one Nominatim call
"""
import re
from dataclasses import dataclass

from django.conf import settings

from .http import get_json
from .places import lookup_city, parse_state

LATLNG_RE = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$')
ZIP_RE = re.compile(r'^\s*(\d{5})(?:-\d{4})?\s*$')


class GeocodingError(Exception):
    pass


@dataclass
class Location:
    query: str
    label: str
    lat: float
    lng: float
    source: str  # 'coordinates' | 'gazetteer' | 'nominatim'

    def as_dict(self):
        return {
            'query': self.query, 'label': self.label,
            'latitude': round(self.lat, 6), 'longitude': round(self.lng, 6),
            'geocoded_by': self.source,
        }


def in_usa(lat, lng):
    """Rough bounding boxes for the contiguous US, Alaska and Hawaii."""
    return (
        (24.3 <= lat <= 49.5 and -125.0 <= lng <= -66.8)
        or (51.0 <= lat <= 71.6 and -180.0 <= lng <= -129.9)
        or (18.8 <= lat <= 22.4 and -160.4 <= lng <= -154.7)
    )


def geocode(query):
    query = (query or '').strip()
    if not query:
        raise GeocodingError('Location is empty.')

    location = _from_coordinates(query) or _from_gazetteer(query) or _from_nominatim(query)
    if not in_usa(location.lat, location.lng):
        raise GeocodingError(f'"{query}" resolves to a point outside the USA ({location.label}).')
    return location


def _from_coordinates(query):
    m = LATLNG_RE.match(query)
    if not m:
        return None
    lat, lng = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise GeocodingError(f'"{query}" is not a valid "lat,lng" pair.')
    return Location(query, f'{lat:.5f}, {lng:.5f}', lat, lng, 'coordinates')


def _from_gazetteer(query):
    parts = [p.strip() for p in query.split(',') if p.strip()]
    if parts and parts[-1].lower() in ('usa', 'us', 'united states', 'united states of america'):
        parts = parts[:-1]
    if len(parts) != 2:
        return None
    city, state = parts
    coords = lookup_city(city, state)
    if coords is None:
        return None
    state_code = parse_state(state)
    return Location(query, f'{city.title()}, {state_code}', coords[0], coords[1], 'gazetteer')


def _from_nominatim(query):
    params = {'format': 'jsonv2', 'limit': 1, 'countrycodes': 'us'}
    zip_match = ZIP_RE.match(query)
    if zip_match:
        params.update(postalcode=zip_match.group(1), country='us')
        params.pop('countrycodes')
    else:
        params['q'] = query
    status, data = get_json(settings.FUELROUTE['NOMINATIM_URL'], params=params)
    if status != 200 or not isinstance(data, list):
        raise GeocodingError(f'Geocoding service error (HTTP {status}) for "{query}".')
    if not data:
        raise GeocodingError(f'Could not find "{query}" in the USA. Try "City, ST" or "lat,lng".')
    hit = data[0]
    return Location(query, hit.get('display_name', query), float(hit['lat']), float(hit['lon']), 'nominatim')

