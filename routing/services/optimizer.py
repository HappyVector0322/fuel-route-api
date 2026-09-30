"""Minimum-cost refuelling plan along a fixed route.

Pure price-minimisation tends to produce silly plans: a dozen stops buying one
gallon each to save a cent. So each stop also carries a small fixed cost
(`stop_penalty`, in dollars, standing in for the time a stop takes). The optimiser
minimises  fuel cost + stop_penalty * number_of_stops;  only real fuel dollars are
reported as the trip cost. With stop_penalty=0 the plan is the pure cheapest one.

Algorithm (exact, from Khuller, Malekian & Mestre, "To Fill or not to Fill: The
Gas Station Problem", 2007): in some optimal plan, at every stop the driver either
fills the tank or buys just enough to reach the next stop. So the fuel left on
arriving at station j is either 0 or U - d(k, j) for an earlier station k, where
U is the tank range. So each (stop i -> next stop j) pair yields at most two
states at j, and each station's transitions are evaluated as one numpy matrix op.
States are pruned by dominance (more fuel for less money wins). ~10-30 ms for a
coast-to-coast route with ~300 candidate stations.

Fuel is tracked in miles of range internally and converted to gallons on output.
"""
from dataclasses import dataclass, field

import numpy as np

EPS = 1e-6


class InfeasibleRouteError(Exception):
    def __init__(self, message, gap_start_mile, gap_end_mile):
        super().__init__(message)
        self.gap_start_mile = gap_start_mile
        self.gap_end_mile = gap_end_mile


@dataclass
class FuelStop:
    candidate: object  # the RouteStation (anything with .mile and .price)
    gallons: float
    cost: float
    fuel_on_arrival_gallons: float


@dataclass
class FuelPlan:
    stops: list = field(default_factory=list)
    # Fuel burned between the start and the first station on the route; the vehicle
    # is assumed to arrive there on fumes, and it is charged at that station's price.
    approach_gallons: float = 0.0
    approach_cost: float = 0.0

    @property
    def total_gallons(self):
        return self.approach_gallons + sum(s.gallons for s in self.stops)

    @property
    def total_cost(self):
        return self.approach_cost + sum(s.cost for s in self.stops)


def _dedupe_by_position(candidates):
    """Stations geocoded to the same spot (same town) are interchangeable except
    for price, so keep only the cheapest one per position."""
    best = {}
    for c in candidates:
        key = round(c.mile, 3)
        if key not in best or c.price < best[key].price:
            best[key] = c
    return sorted(best.values(), key=lambda c: c.mile)


def _check_coverage(stations, total_miles, range_miles):
    if not stations:
        raise InfeasibleRouteError('No fuel stations found along this route.', 0.0, total_miles)
    if stations[0].mile > range_miles:
        raise InfeasibleRouteError('No fuel station within range of the start.', 0.0, stations[0].mile)
    points = [s.mile for s in stations] + [total_miles]
    for a, b in zip(points, points[1:]):
        if b - a > range_miles + EPS:
            raise InfeasibleRouteError(
                f'No fuel station within {range_miles:.0f} miles after mile {a:.0f}.', a, b
            )


def plan_fuel_stops(candidates, total_miles, range_miles, mpg, stop_penalty=0.0):
    """Return the cheapest FuelPlan for driving `total_miles` past `candidates`.

    The vehicle starts with an empty tank and fuels at the first station on the
    route (normally within a few miles of the start), and arrives empty.
    """
    stations = [c for c in _dedupe_by_position(candidates) if c.mile <= total_miles]
    _check_coverage(stations, total_miles, range_miles)
    n, U = len(stations), range_miles
    miles = np.array([s.mile for s in stations])
    rates = np.array([s.price for s in stations]) / mpg  # dollars per mile of fuel

    # incoming[j]: list of (arrival_fuel, cost, back) for plans whose latest stop
    # leads straight to a stop at j; back = (prev_station, prev_state_index, miles_bought).
    incoming = [[] for _ in range(n)]
    incoming[0].append((0.0, 0.0, None))
    expanded = [None] * n  # per station: list of (fuel, cost, back) actually expanded
    best_finish = (np.inf, None)

    for i in range(n):
        if not incoming[i]:
            continue
        states = _undominated(incoming[i])
        expanded[i] = states
        fuel = np.array([st[0] for st in states])
        cost = np.array([st[1] for st in states])
        rate = rates[i]

        # Finish from here, buying only what is still needed.
        to_end = total_miles - miles[i]
        if to_end <= U + EPS:
            buy = np.maximum(0.0, to_end - fuel)
            total = cost + buy * rate + np.where(buy > EPS, stop_penalty, 0.0)
            s = int(np.argmin(total))
            if total[s] < best_finish[0]:
                best_finish = (total[s], (i, s, buy[s]))

        hi = int(np.searchsorted(miles, miles[i] + U + EPS, side='right'))
        if hi <= i + 1:
            continue
        d = miles[i + 1:hi] - miles[i]  # leg lengths to each reachable next stop

        # "Just enough": arrive at j empty. Only a real stop if we must buy.
        need = d[None, :] - fuel[:, None]
        just = np.where(need > EPS, cost[:, None] + need * rate + stop_penalty, np.inf)
        s_just = np.argmin(just, axis=0)
        # "Fill up": arrive at j with U - d.
        fill_cost = cost + (U - fuel) * rate + stop_penalty
        s_fill = int(np.argmin(fill_cost))

        for k, j in enumerate(range(i + 1, hi)):
            sj = s_just[k]
            if np.isfinite(just[sj, k]):
                incoming[j].append((0.0, just[sj, k], (i, sj, need[sj, k])))
            if U - fuel[s_fill] > need[s_fill, k] + EPS:
                incoming[j].append((U - d[k], fill_cost[s_fill], (i, s_fill, U - fuel[s_fill])))

    if best_finish[1] is None:  # unreachable given _check_coverage, but be explicit
        raise InfeasibleRouteError('Could not build a fuel plan for this route.', 0.0, total_miles)
    return _build_plan(stations, expanded, best_finish[1], mpg)


def _undominated(states):
    """Drop states that have less (or equal) fuel *and* cost more than another."""
    kept, cheapest = [], np.inf
    for st in sorted(states, key=lambda st: (-st[0], st[1])):
        if st[1] < cheapest - 1e-9:
            kept.append(st)
            cheapest = st[1]
    return kept


def _build_plan(stations, expanded, last, mpg):
    purchases = []
    node = last
    while node is not None:
        i, s, bought = node
        fuel, _, back = expanded[i][s]
        purchases.append((i, fuel, bought))
        node = back
    purchases.reverse()

    first = stations[0]
    plan = FuelPlan(approach_gallons=first.mile / mpg, approach_cost=first.mile / mpg * first.price)
    for i, fuel, bought in purchases:
        if bought <= EPS:
            continue
        gallons = bought / mpg
        plan.stops.append(FuelStop(
            candidate=stations[i],
            gallons=gallons,
            cost=gallons * stations[i].price,
            fuel_on_arrival_gallons=fuel / mpg,
        ))
    return plan
