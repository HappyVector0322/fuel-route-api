from decimal import Decimal

import numpy as np
from django.test import SimpleTestCase

from routing.models import FuelStation
from routing.services.geo import densify, haversine_miles
from routing.services.geocoding import GeocodingError, geocode
from routing.services.places import normalize_name
from routing.services.stations import StationIndex


class GeoTests(SimpleTestCase):
    def test_haversine_nyc_to_la(self):
        self.assertAlmostEqual(haversine_miles(40.7128, -74.006, 34.0522, -118.2437), 2445, delta=5)

    def test_densify_preserves_length_and_limits_step(self):
        lats, lngs = np.array([35.0, 35.0, 36.0]), np.array([-100.0, -99.0, -99.0])
        d_lat, d_lng, cum = densify(lats, lngs, 0.25)
        total = haversine_miles(lats[:-1], lngs[:-1], lats[1:], lngs[1:]).sum()
        self.assertAlmostEqual(cum[-1], total, places=6)
        self.assertLessEqual(np.diff(cum).max(), 0.25 + 1e-9)
        self.assertEqual((d_lat[-1], d_lng[-1]), (36.0, -99.0))

    def test_name_normalization(self):
        self.assertEqual(normalize_name('Mc Lean'), normalize_name('McLean'))
        self.assertEqual(normalize_name('Saint Louis'), normalize_name('St. Louis'))
        self.assertEqual(normalize_name('Fort Worth'), normalize_name('Ft Worth'))


class StationIndexTests(SimpleTestCase):
    def test_finds_nearby_stations_with_mile_markers(self):
        def station(opis_id, lat, lng, price):
            return FuelStation(opis_id=opis_id, name=f'S{opis_id}', address='', city='X', state='TX',
                               retail_price=Decimal(price), latitude=lat, longitude=lng)

        index = StationIndex([
            station(1, 35.0, -99.5, '3.00'),   # on the route, ~28 mi in
            station(2, 35.03, -99.0, '3.10'),  # ~2 mi off the route's end
            station(3, 36.0, -99.5, '2.00'),   # ~69 mi away
        ])
        found = {rs.station['opis_id']: rs for rs in index.along_route([35.0, 35.0], [-100.0, -99.0], 5)}
        self.assertEqual(set(found), {1, 2})
        self.assertAlmostEqual(found[1].mile, 28.3, delta=0.5)
        self.assertAlmostEqual(found[2].off_route_miles, 2.07, delta=0.1)


class GeocodingTests(SimpleTestCase):
    def test_lat_lng_input(self):
        loc = geocode('40.7128, -74.0060')
        self.assertEqual((loc.lat, loc.lng, loc.source), (40.7128, -74.006, 'coordinates'))

    def test_city_state_uses_offline_gazetteer(self):
        for query in ('Dallas, TX', 'dallas, texas', 'Dallas, TX, USA'):
            loc = geocode(query)
            self.assertEqual(loc.source, 'gazetteer')
            self.assertAlmostEqual(loc.lat, 32.8, delta=0.3)

    def test_rejects_points_outside_usa(self):
        with self.assertRaises(GeocodingError):
            geocode('48.8566, 2.3522')  # Paris
