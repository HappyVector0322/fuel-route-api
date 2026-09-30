import sys
import threading

from django.apps import AppConfig
from django.conf import settings


class RoutingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'routing'

    def ready(self):
        # Warm the in-memory station index and gazetteer so the first API request
        # doesn't pay for loading them. Skipped for management commands other than
        # runserver (migrate, test, ...); always done under gunicorn/uwsgi.
        command = sys.argv[1] if len(sys.argv) > 1 and sys.argv[0].endswith('manage.py') else None
        if settings.FUELROUTE['WARM_UP_ON_START'] and command in (None, 'runserver'):
            threading.Thread(target=_warm_up, daemon=True).start()


def _warm_up():
    from .services.places import load_places
    from .services.stations import get_station_index

    load_places()
    try:
        get_station_index()
    except Exception:  # e.g. tables not migrated yet; the first request will retry
        from .services import stations
        stations.invalidate_station_index()
