# Fuel Route API

A Django REST API that takes a start and finish anywhere in the USA and returns:

- the driving route, as GeoJSON plus a link to an interactive map;
- the **cheapest fuel stops** along the route, for a vehicle with a **500-mile range** and **10 mpg**;
- the **total fuel cost** of the trip.

It makes **1 external API call per request** (OSRM routing). Repeat requests make none, because results are cached.

**Speed:** the API's own work (geocoding, station matching, optimisation) takes about 5–70 ms, even coast to coast. The rest is the single routing call, about 1.5–2.5 s on the free OSRM demo server. A cached repeat returns in about 10 ms.

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # Django 6.1 needs Python >= 3.12
pip install -r requirements.txt
python manage.py migrate
python manage.py load_fuel_stations     # imports the pre-geocoded station list (~1 s)
python manage.py runserver
```

- JSON API: `GET http://127.0.0.1:8000/api/route/?start=New York, NY&finish=Los Angeles, CA`
- Map UI: open `http://127.0.0.1:8000/` and submit the form, or follow `map_url` from any API response.
- Tests: `python manage.py test routing`

## API

### `GET /api/route/` or `POST /api/route/`

| Parameter  | Required | Description |
|------------|----------|-------------|
| `start`    | yes | `"City, ST"`, `"City, State"`, `"lat,lng"`, a ZIP code, or a street address in the USA |
| `finish`   | yes | same formats as `start` |
| `geometry` | no  | route line in the GeoJSON: `simplified` (default), `full`, or `none` |
| `stop_penalty` | no | dollars charged per stop while optimising (default `5`), so the plan avoids stops that only save pennies; `0` gives the absolute cheapest plan. It is never added to the reported cost. |

`POST` takes the same fields as a JSON body: `{"start": "Chicago, IL", "finish": "Miami, FL"}`.

**Response (abridged):**

```json
{
  "start":  {"query": "New York, NY", "label": "New York, NY", "latitude": 40.66, "longitude": -73.94, "geocoded_by": "gazetteer"},
  "finish": {"query": "Los Angeles, CA", "label": "Los Angeles, CA", "...": "..."},
  "route":  {"distance_miles": 2810.4, "duration_hours": 50.31, "stations_considered": 349},
  "vehicle": {"range_miles": 500.0, "mpg": 10.0, "tank_gallons": 50.0},
  "optimization": {"stop_penalty": 5.0},
  "fuel_stops": [
    {
      "stop_number": 1,
      "station": {"opis_id": 12345, "name": "PILOT #123", "address": "I-80, EXIT 1", "city": "...", "state": "NJ",
                  "latitude": 40.9, "longitude": -74.1},
      "mile_marker": 12.4,
      "distance_off_route_miles": 1.8,
      "price_per_gallon": 3.199,
      "gallons": 21.3,
      "cost": 68.14,
      "fuel_on_arrival_gallons": 0.0
    }
  ],
  "summary": {"total_fuel_cost": 865.27, "total_gallons": 281.04, "number_of_stops": 8, "average_price_per_gallon": 3.079},
  "approach": {"miles_to_first_station": 8.8, "gallons": 0.88, "cost": 2.85},
  "assumptions": ["..."],
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA",
  "geojson": {"type": "FeatureCollection", "features": ["route LineString, start/finish and fuel-stop Points"]},
  "meta": {"cached": false, "external_api_calls": {"routing": 1, "geocoding": 0},
           "timings": {"geocoding_ms": 0.1, "routing_api_ms": 2497.0, "station_matching_ms": 27.5, "optimization_ms": 37.4}}
}
```

The `geojson` can be pasted into [geojson.io](https://geojson.io) as is. `map_url` opens a Leaflet/OpenStreetMap page with the route and numbered fuel stops.

**Errors:** `400` for invalid input or an unresolvable or non-US location. `422` when there is no drivable route, or when a stretch of the route has no station within 500 miles (the response includes the gap). `502` when the routing provider fails.

## How it works

```
start, finish ──► geocode (offline) ──► OSRM route (1 HTTP call) ──► match stations to route ──► optimise ──► JSON + map
```

1. **Geocoding without API calls.** `"City, ST"` and `"lat,lng"` inputs are resolved against a bundled US Census gazetteer (`data/us_places.csv`, 51k places). Only free-form addresses and ZIP codes fall back to one Nominatim call.
2. **Routing.** One call to the free, keyless [OSRM](https://project-osrm.org/) demo server (`overview=full`), which returns distance, duration and the full road geometry. Set `OSRM_BASE_URL` to use a self-hosted instance.
3. **Stations on the route.** At startup, stations are loaded once from the DB into numpy arrays. For each request, the route is densified to 0.25-mile spacing and put in a KD-tree (on unit-sphere coordinates, so distances are correct everywhere). All stations are then queried in one vectorised call. Stations within 5 miles of the route are kept, each with a *mile marker*. This takes about 10 ms for a coast-to-coast route.
4. **Optimisation.** This is the *gas station problem*, solved exactly in [`optimizer.py`](routing/services/optimizer.py). The optimiser minimises `fuel cost + stop_penalty × stops`.
   - **Why the penalty:** with pure price minimisation, New York → Los Angeles has 18 stops, several of them buying a gallon to save a cent. With the default $5 penalty it has 8 stops, for $5.67 more fuel.
   - **The algorithm:** Khuller, Malekian & Mestre, *To Fill or not to Fill* (2007), show that at every stop of an optimal plan the driver either fills the tank or buys just enough to reach the next stop. Each (stop → next stop) pair therefore creates at most two states. The optimiser runs a forward DP over those states, vectorised with numpy per station and pruned by dominance.
   - **Speed:** about 10–40 ms coast to coast.
   - **Verification:** tests compare it with a brute-force DP on 60 random routes, at several penalties.
5. **Caching.** Plans are cached per (start, finish), so the map page and repeat requests are instant and make no external calls.

### Data preparation (already done; outputs are committed)

The price list has only city and state, no coordinates, and geocoding 8,000 rows at request time would be far too slow. `python manage.py build_station_data` geocoded them once:

- 95% of cities matched offline against the US Census 2024 gazetteer (places + county subdivisions);
- the remaining small unincorporated places were looked up once with Nominatim (1 req/s, cached in `data/raw/nominatim_cache.json`);
- Canadian stations are excluded, since the routes are USA-only;
- duplicate OPIS IDs keep their lowest price.

The result, `data/fuel_stations_geocoded.csv`, is what `load_fuel_stations` imports. To rebuild it, download the 2024 Census gazetteer place and county-subdivision files into `data/raw/`.

## Assumptions

- The vehicle has a 500-mile range and gets 10 mpg, so the tank holds 50 gallons.
- The trip starts with an empty tank, and the first stop is the first station on the route. Fuel burned before reaching it, normally a few miles, is charged at that station's price. The total therefore covers every gallon burned on the trip.
- The vehicle arrives at the destination on empty, so no fuel is bought that the trip does not use.
- Stops are chosen to minimise fuel cost plus `stop_penalty` (default $5) per stop. `total_fuel_cost` is the actual fuel spend only.
- Station locations are city-level, so a station "on the route" is one within 5 miles of it. Detours to reach a station are not counted.

## Project layout

```
config/                      Django settings (FUELROUTE block holds vehicle and API config)
routing/
  models.py                  FuelStation
  views.py                   /api/route/ (DRF), /api/route/map/ (HTML), /
  services/
    planner.py               orchestration, caching, response shaping, GeoJSON
    geocoding.py             lat/lng -> gazetteer -> Nominatim fallback
    routing_api.py           OSRM client
    stations.py              in-memory station index + route matching (KD-tree)
    optimizer.py             cheapest-refuelling DP (fill-or-not-to-fill)
    geo.py, places.py        geometry helpers, gazetteer lookup
  management/commands/       build_station_data, load_fuel_stations
  tests/                     optimizer (incl. brute-force check), geo, API (OSRM mocked)
postman_collection.json      ready-made requests for Postman
data/                        price list, geocoded stations, gazetteer
```
