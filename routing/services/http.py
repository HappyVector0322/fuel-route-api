import threading

import requests
from django.conf import settings

_local = threading.local()


class ExternalServiceError(Exception):
    """An upstream API (routing or geocoding) failed or returned something unusable."""


def session():
    """Per-thread keep-alive session, so repeat calls reuse the TLS connection."""
    if not hasattr(_local, 'session'):
        s = requests.Session()
        s.headers['User-Agent'] = settings.FUELROUTE['USER_AGENT']
        _local.session = s
    return _local.session


def get_json(url, params=None):
    try:
        resp = session().get(url, params=params, timeout=settings.FUELROUTE['HTTP_TIMEOUT_SECONDS'])
    except requests.RequestException as exc:
        raise ExternalServiceError(f'Could not reach {url}: {exc}') from exc
    try:
        data = resp.json()
    except ValueError:
        raise ExternalServiceError(f'{url} returned HTTP {resp.status_code} with a non-JSON body')
    return resp.status_code, data
