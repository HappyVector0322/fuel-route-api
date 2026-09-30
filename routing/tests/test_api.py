from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from routing.models import FuelStation
from routing.services.stations import invalidate_station_index

# A straight west->east route along lat 35 from -105 to -95 (~565 miles).
ROUTE_LNGS = [-105.0 + i * 0.5 for i in range(21)]
OSRM_OK = {
    'code': 'Ok',
    'routes': [{
        'distance': 565 * 1609.344,
        'duration': 9 * 3600,
        'geometry': {'type': 'LineString', 'coordinates': [[lng, 35.0] for lng in ROUTE_LNGS]},
    }],
}


def mock_osrm(payload=OSRM_OK, status=200):
    resp = mock.Mock(status_code=status)
    resp.json.return_value = payload
    return mock.patch('routing.services.http.session', return_value=mock.Mock(get=mock.Mock(return_value=resp)))


class RoutePlanApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for opis_id, lng, price in [(1, -105.0, '3.50'), (2, -102.0, '2.90'), (3, -99.0, '3.20'),
                                    (4, -96.0, '3.80'), (5, -100.0, '1.00')]:
            FuelStation.objects.create(
                opis_id=opis_id, name=f'Stop {opis_id}', address='I-40', city='Town', state='NM',
                retail_price=Decimal(price), latitude=35.0 if opis_id != 5 else 36.0, longitude=lng,
            )

    def setUp(self):
        cache.clear()
        invalidate_station_index()
        self.url = reverse('route-plan')
        self.params = {'start': '35.0,-105.0', 'finish': '35.0,-95.0'}

    def test_plans_cheapest_stops_with_one_routing_call(self):
        with mock_osrm() as session:
            resp = self.client.get(self.url, self.params)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(session.return_value.get.call_count, 1)
        self.assertEqual(body['meta']['external_api_calls'], {'routing': 1, 'geocoding': 0})

        # Station 5 ($1.00) is 69 miles off the route and must be ignored.
        stops = body['fuel_stops']
        self.assertEqual([s['station']['opis_id'] for s in stops], [1, 2])
        self.assertAlmostEqual(sum(s['gallons'] for s in stops), 56.5, places=1)
        self.assertAlmostEqual(body['summary']['total_gallons'], 56.5, places=1)
        self.assertAlmostEqual(
            body['summary']['total_fuel_cost'], sum(s['cost'] for s in stops), places=1
        )
        self.assertEqual(body['geojson']['type'], 'FeatureCollection')
        self.assertIn('/api/route/map/?', body['map_url'])

    def test_repeat_request_is_served_from_cache(self):
        with mock_osrm() as session:
            self.client.get(self.url, self.params)
            body = self.client.get(self.url, self.params).json()
        self.assertEqual(session.return_value.get.call_count, 1)
        self.assertTrue(body['meta']['cached'])
        self.assertEqual(body['meta']['external_api_calls'], {'routing': 0, 'geocoding': 0})

    def test_post_json_and_geometry_none(self):
        with mock_osrm():
            resp = self.client.post(self.url, {**self.params, 'geometry': 'none'}, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('geojson', resp.json())

    def test_stop_penalty_is_part_of_request_and_cache_key(self):
        with mock_osrm() as session:
            default = self.client.get(self.url, self.params).json()
            cheapest = self.client.get(self.url, {**self.params, 'stop_penalty': 0}).json()
        self.assertEqual(session.return_value.get.call_count, 2)
        self.assertEqual(default['optimization']['stop_penalty'], 5.0)
        self.assertEqual(cheapest['optimization']['stop_penalty'], 0.0)
        self.assertLessEqual(cheapest['summary']['total_fuel_cost'], default['summary']['total_fuel_cost'])
        self.assertIn('stop_penalty=0', cheapest['map_url'])

    def test_missing_params_is_400(self):
        resp = self.client.get(self.url, {'start': 'Dallas, TX'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('finish', resp.json()['details'])

    def test_location_outside_usa_is_400(self):
        resp = self.client.get(self.url, {'start': '48.85,2.35', 'finish': '35.0,-95.0'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error'], 'geocoding_failed')

    def test_no_route_is_422(self):
        with mock_osrm({'code': 'NoRoute', 'message': 'Impossible route'}, status=400):
            resp = self.client.get(self.url, self.params)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()['error'], 'no_route')

    def test_upstream_failure_is_502(self):
        with mock_osrm({'message': 'Too many requests'}, status=429):
            resp = self.client.get(self.url, self.params)
        self.assertEqual(resp.status_code, 502)

    def test_map_page_renders(self):
        with mock_osrm():
            resp = self.client.get(reverse('route-map'), self.params)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Stop 2')
        self.assertContains(resp, 'id="plan-data"')
