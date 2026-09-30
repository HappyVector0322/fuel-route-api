import random
from dataclasses import dataclass

from django.test import SimpleTestCase

from routing.services.optimizer import InfeasibleRouteError, plan_fuel_stops

RANGE, MPG = 500.0, 10.0


@dataclass(frozen=True)
class S:
    mile: float
    price: float
    station: dict = None
    off_route_miles: float = 0.0


def brute_force_cost(stations, total, range_miles, mpg, stop_penalty=0.0, step=10):
    """Exact DP over (station, fuel level in `step`-mile units) as a reference."""
    stations = sorted(stations, key=lambda s: s.mile)
    cap = int(range_miles // step)
    first = stations[0]
    approach = first.mile / mpg * first.price
    nodes = stations + [S(total, 0.0)]
    INF = float('inf')
    best = [[INF] * (cap + 1) for _ in nodes]
    best[0][0] = approach
    # Driving past a station == stopping and buying nothing, so only i -> i+1 moves.
    for i, node in enumerate(nodes[:-1]):
        need = int(round((nodes[i + 1].mile - node.mile) / step))
        for fuel in range(cap + 1):
            if best[i][fuel] == INF:
                continue
            for extra in range(max(0, need - fuel), cap - fuel + 1):
                cost = best[i][fuel] + extra * step / mpg * node.price + (stop_penalty if extra else 0)
                left = fuel + extra - need
                best[i + 1][left] = min(best[i + 1][left], cost)
    return best[-1][0]


class OptimizerTests(SimpleTestCase):
    def test_short_trip_buys_just_enough_at_first_station(self):
        plan = plan_fuel_stops([S(0, 3.0), S(100, 2.0)], 300, RANGE, MPG)
        # Cheaper station at mile 100: buy 10 gal at $3, then 20 gal at $2.
        self.assertEqual([round(s.gallons, 6) for s in plan.stops], [10, 20])
        self.assertAlmostEqual(plan.total_cost, 10 * 3 + 20 * 2)
        self.assertAlmostEqual(plan.total_gallons, 30)

    def test_fills_up_when_current_station_is_cheapest(self):
        plan = plan_fuel_stops([S(0, 2.0), S(400, 4.0), S(450, 3.5)], 800, RANGE, MPG)
        # Fill 50 gal at $2, drive to the cheapest in reach ($3.5 @450), top up 30 gal.
        self.assertAlmostEqual(plan.stops[0].gallons, 50)
        self.assertAlmostEqual(plan.stops[1].candidate.mile, 450)
        self.assertAlmostEqual(plan.stops[1].gallons, 30)
        self.assertAlmostEqual(plan.total_cost, 50 * 2 + 30 * 3.5)
        self.assertAlmostEqual(plan.total_gallons, 80)

    def test_approach_to_first_station_is_charged(self):
        plan = plan_fuel_stops([S(20, 3.0)], 200, RANGE, MPG)
        self.assertAlmostEqual(plan.approach_gallons, 2)
        self.assertAlmostEqual(plan.total_gallons, 20)
        self.assertAlmostEqual(plan.total_cost, 60)

    def test_gap_longer_than_range_is_infeasible(self):
        with self.assertRaises(InfeasibleRouteError) as ctx:
            plan_fuel_stops([S(0, 3.0), S(600, 3.0)], 1000, RANGE, MPG)
        self.assertEqual(ctx.exception.gap_start_mile, 0)

    def test_no_stations_is_infeasible(self):
        with self.assertRaises(InfeasibleRouteError):
            plan_fuel_stops([], 100, RANGE, MPG)

    def test_stop_penalty_skips_penny_saving_stops(self):
        stations = [S(0, 3.00), S(100, 2.99), S(200, 2.98), S(300, 2.97)]
        cheapest = plan_fuel_stops(stations, 450, RANGE, MPG)
        practical = plan_fuel_stops(stations, 450, RANGE, MPG, stop_penalty=5)
        self.assertEqual(len(cheapest.stops), 4)
        self.assertEqual(len(practical.stops), 1)
        self.assertAlmostEqual(practical.total_gallons, 45)

    def test_matches_brute_force_on_random_routes(self):
        rng = random.Random(42)
        for case in range(60):
            penalty = [0, 2, 10][case % 3]
            total = rng.randrange(300, 1500, 10)
            miles = sorted({0, *rng.sample(range(10, total, 10), k=min(12, total // 10 - 1))})
            stations = [S(m, round(rng.uniform(2.8, 4.2), 2)) for m in miles]
            try:
                plan = plan_fuel_stops(stations, total, RANGE, MPG, stop_penalty=penalty)
            except InfeasibleRouteError:
                continue
            self.assertAlmostEqual(
                plan.total_cost + penalty * len(plan.stops),
                brute_force_cost(stations, total, RANGE, MPG, stop_penalty=penalty),
                places=6,
            )
            self.assertAlmostEqual(plan.total_gallons, total / MPG, places=6)
