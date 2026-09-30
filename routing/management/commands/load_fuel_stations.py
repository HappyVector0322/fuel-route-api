import csv
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routing.models import FuelStation
from routing.services.stations import invalidate_station_index

GEOCODED_CSV = settings.BASE_DIR / 'data' / 'fuel_stations_geocoded.csv'


class Command(BaseCommand):
    help = 'Load (or reload) fuel stations from data/fuel_stations_geocoded.csv'

    def add_arguments(self, parser):
        parser.add_argument('--path', default=str(GEOCODED_CSV))

    def handle(self, *args, path, **options):
        stations = {}
        with open(path, newline='') as f:
            for row in csv.DictReader(f):
                opis_id = int(row['opis_id'])
                price = Decimal(row['retail_price']).quantize(Decimal('0.001'))
                # Duplicate OPIS ids exist in the source; keep the cheapest listing.
                if opis_id in stations and stations[opis_id].retail_price <= price:
                    continue
                stations[opis_id] = FuelStation(
                    opis_id=opis_id,
                    name=row['name'],
                    address=row['address'],
                    city=row['city'],
                    state=row['state'],
                    rack_id=int(row['rack_id']) if row['rack_id'] else None,
                    retail_price=price,
                    latitude=float(row['latitude']),
                    longitude=float(row['longitude']),
                    geocode_source=row['geocode_source'],
                )

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations.values(), batch_size=1000)
        invalidate_station_index()
        self.stdout.write(self.style.SUCCESS(f'Loaded {len(stations)} fuel stations'))
