from django.shortcuts import render
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.geocoding import GeocodingError
from .services.http import ExternalServiceError
from .services.optimizer import InfeasibleRouteError
from .services.planner import build_response, plan_trip
from .services.routing_api import RouteNotFoundError


def _plan_or_error(data):
    """Validate input and plan the trip.

    Returns (cached_plan, from_cache, geometry, error_response); error_response is
    None on success.
    """
    serializer = RouteRequestSerializer(data=data)
    if not serializer.is_valid():
        return None, None, None, Response(
            {'error': 'invalid_request', 'details': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    v = serializer.validated_data
    try:
        cached, from_cache = plan_trip(v['start'], v['finish'], v.get('stop_penalty'))
    except GeocodingError as exc:
        return None, None, None, _error('geocoding_failed', str(exc), status.HTTP_400_BAD_REQUEST)
    except RouteNotFoundError as exc:
        return None, None, None, _error('no_route', str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
    except InfeasibleRouteError as exc:
        return None, None, None, _error(
            'no_fuel_coverage', str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY,
            gap={'from_mile': round(exc.gap_start_mile, 1), 'to_mile': round(exc.gap_end_mile, 1)},
        )
    except ExternalServiceError as exc:
        return None, None, None, _error('upstream_unavailable', str(exc), status.HTTP_502_BAD_GATEWAY)
    return cached, from_cache, v.get('geometry', 'simplified'), None


def _error(code, message, http_status, **extra):
    return Response({'error': code, 'message': message, **extra}, status=http_status)


class RoutePlanView(APIView):
    """Plan the cheapest fuel stops between two US locations.

    GET  /api/route/?start=New York, NY&finish=Los Angeles, CA
    POST /api/route/  {"start": "...", "finish": "..."}
    """

    def get(self, request):
        return self._handle(request, request.query_params)

    def post(self, request):
        return self._handle(request, request.data)

    def _handle(self, request, data):
        cached, from_cache, geometry, error = _plan_or_error(data)
        if error:
            return error
        return Response(build_response(cached, from_cache, request, geometry=geometry))


def route_map(request):
    """HTML map (Leaflet + OpenStreetMap) of the route and its fuel stops."""
    cached, from_cache, _, error = _plan_or_error(request.GET)
    if error:
        return render(request, 'routing/map.html', {'error': error.data}, status=error.status_code)
    plan = build_response(cached, from_cache, request, geometry='full')
    return render(request, 'routing/map.html', {'plan': plan})


def index(request):
    return render(request, 'routing/index.html')
