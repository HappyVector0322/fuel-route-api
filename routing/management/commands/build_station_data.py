"""One-off data preparation (outputs are committed, so reviewers never need to run it).

1. Builds data/us_places.csv, a compact US place gazetteer, from the US Census
   2024 Gazetteer files (places + county subdivisions) in data/raw/.
2. Geocodes every fuel station in the price list to its city. Cities that are
   not in the gazetteer (small unincorporated places) are looked up once with
   Nominatim (rate limited to 1 req/s, results cached in data/raw/).
3. Writes data/fuel_stations_geocoded.csv, which `load_fuel_stations` imports.
"""
import csv
import json
import re
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from routing.services.places import PLACES_CSV, US_STATES, lookup_city, load_places, normalize_name

DATA = settings.BASE_DIR / 'data'
RAW = DATA / 'raw'
GAZETTEER_FILES = ['2024_Gaz_place_national.txt', '2024_Gaz_cousubs_national.txt']
PRICES_CSV = DATA / 'fuel-prices-for-be-assessment.csv'
GEOCODED_CSV = DATA / 'fuel_stations_geocoded.csv'
NOMINATIM_CACHE = RAW / 'nominatim_cache.json'

_SUFFIX = re.compile(
    r'\s+(city and borough|city|town|township|village|borough|CDP|municipality|'
    r'plantation|urban county|consolidated government|metropolitan government|'
    r'unified government|government)$',
    re.I,
)


def census_name_variants(name):
    """'Indianapolis city (balance)' -> ['Indianapolis']; 'Lexington-Fayette urban county'
    -> ['Lexington-Fayette', 'Lexington']; 'Boise City city' -> ['Boise City', 'Boise']."""
    base = re.sub(r'\s*\(balance\)', '', name).strip()
    base = _SUFFIX.sub('', base)
    variants = [base]
    if '-' in base:
        variants.append(base.split('-')[0])
    stripped = _SUFFIX.sub('', base)
    if stripped != base:
        variants.append(stripped)
    return variants


class Command(BaseCommand):
    help = 'Build data/us_places.csv and data/fuel_stations_geocoded.csv'

    def handle(self, *args, **options):
        self.build_places()
        self.geocode_stations()

    def build_places(self):
        places = {}
        for filename in GAZETTEER_FILES:  # places take priority over county subdivisions
            with open(RAW / filename, encoding='latin-1', newline='') as f:
                for row in csv.DictReader(f, delimiter='\t'):
                    row = {k.strip(): (v or '').strip() for k, v in row.items()}
                    state = row['USPS']
                    if state not in US_STATES:
                        continue
                    for variant in census_name_variants(row['NAME']):
                        places.setdefault(
                            (normalize_name(variant), state),
                            (variant, row['INTPTLAT'], row['INTPTLONG']),
                        )
        with open(PLACES_CSV, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['key', 'state', 'name', 'lat', 'lng'])
            for (key, state), (name, lat, lng) in sorted(places.items()):
                writer.writerow([key, state, name, lat, lng])
        load_places.cache_clear()
        self.stdout.write(f'Wrote {len(places)} places to {PLACES_CSV.name}')

    def geocode_stations(self):
        cache = json.loads(NOMINATIM_CACHE.read_text()) if NOMINATIM_CACHE.exists() else {}
        session = requests.Session()
        session.headers['User-Agent'] = settings.FUELROUTE['USER_AGENT']

        with open(PRICES_CSV, newline='') as f:
            rows = list(csv.DictReader(f))

        out, skipped_non_us, unresolved = [], 0, set()
        for row in rows:
            city, state = row['City'].strip(), row['State'].strip()
            if state not in US_STATES:
                skipped_non_us += 1  # Canadian provinces: out of scope (USA-only routes)
                continue
            coords, source = lookup_city(city, state), 'gazetteer'
            if coords is None:
                key = f'{city}|{state}'
                if key not in cache:
                    cache[key] = self.nominatim(session, city, state)
                    NOMINATIM_CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))
                    time.sleep(1.1)  # Nominatim usage policy: max 1 request/second
                coords, source = cache[key], 'nominatim'
            if coords is None:
                unresolved.add((city, state))
                continue
            out.append({
                'opis_id': row['OPIS Truckstop ID'].strip(),
                'name': row['Truckstop Name'].strip(),
                'address': row['Address'].strip(),
                'city': city,
                'state': state,
                'rack_id': row['Rack ID'].strip(),
                'retail_price': row['Retail Price'].strip(),
                'latitude': f'{coords[0]:.6f}',
                'longitude': f'{coords[1]:.6f}',
                'geocode_source': source,
            })

        with open(GEOCODED_CSV, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(out[0]))
            writer.writeheader()
            writer.writerows(out)
        self.stdout.write(
            f'Geocoded {len(out)} station rows -> {GEOCODED_CSV.name} '
            f'(skipped {skipped_non_us} non-US rows, {len(unresolved)} unresolved cities: '
            f'{sorted(unresolved)})'
        )

    def nominatim(self, session, city, state):
        params = {
            'city': city, 'state': US_STATES[state], 'country': 'USA',
            'format': 'jsonv2', 'limit': 1,
        }
        resp = session.get(settings.FUELROUTE['NOMINATIM_URL'], params=params, timeout=20)
        resp.raise_for_status()
        results = resp.json()
        if not results:  # retry as free text, e.g. for hamlets Nominatim doesn't tag as city
            resp = session.get(
                settings.FUELROUTE['NOMINATIM_URL'],
                params={'q': f'{city}, {US_STATES[state]}, USA', 'format': 'jsonv2', 'limit': 1},
                timeout=20,
            )
            resp.raise_for_status()
            results = resp.json()
            time.sleep(1.1)
        if not results:
            return None
        return [float(results[0]['lat']), float(results[0]['lon'])]
