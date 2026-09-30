#!/bin/sh
# Prepare the SQLite database, then run the given command (gunicorn by default).
set -e

python manage.py migrate --noinput
python manage.py load_fuel_stations --if-empty

exec "$@"
