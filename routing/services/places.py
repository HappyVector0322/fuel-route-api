"""Offline US place gazetteer (built from US Census data by scripts/build_data.py).

Used to geocode fuel stations (which only have a city/state) and to resolve
"City, ST" inputs without calling any external API.
"""
import csv
import re
from functools import lru_cache

from django.conf import settings

PLACES_CSV = settings.BASE_DIR / 'data' / 'us_places.csv'

US_STATES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California',
    'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'DC': 'District of Columbia',
    'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois',
    'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana',
    'ME': 'Maine', 'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota',
    'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada',
    'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
    'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon',
    'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota',
    'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia',
    'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
}
STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}

_WORD_ALIASES = [
    (r'\bsaint\b', 'st'), (r'\bsainte\b', 'ste'), (r'\bfort\b', 'ft'), (r'\bmount\b', 'mt'),
    (r'\bmc\s+', 'mc'), (r'^s\b', 'south'), (r'^n\b', 'north'), (r'^e\b', 'east'), (r'^w\b', 'west'),
]


def normalize_name(name):
    """Canonical form of a place name, so 'Mc Lean' == 'McLean' and 'Saint X' == 'St. X'."""
    s = name.lower().replace('.', '').replace("'", '').replace('-', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    for pattern, repl in _WORD_ALIASES:
        s = re.sub(pattern, repl, s)
    return s.replace(' ', '')


def parse_state(value):
    value = value.strip()
    if value.upper() in US_STATES:
        return value.upper()
    return STATE_BY_NAME.get(value.lower())


@lru_cache(maxsize=1)
def load_places():
    """{(normalized_name, state): (lat, lng)}"""
    places = {}
    if not PLACES_CSV.exists():
        return places
    with open(PLACES_CSV, newline='') as f:
        for row in csv.DictReader(f):
            places[(row['key'], row['state'])] = (float(row['lat']), float(row['lng']))
    return places


def lookup_city(city, state):
    state = parse_state(state)
    if not state:
        return None
    return load_places().get((normalize_name(city), state))
