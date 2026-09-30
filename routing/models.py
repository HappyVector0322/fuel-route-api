from django.db import models


class FuelStation(models.Model):
    """A truck stop from the OPIS price list, geocoded to its city.

    The source file lists some stations more than once (same OPIS id, sometimes
    different prices); the loader keeps one row per id with the lowest price.
    """

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=6, decimal_places=3)
    latitude = models.FloatField()
    longitude = models.FloatField()
    # 'gazetteer' (US Census place centroid) or 'nominatim' (OSM lookup).
    geocode_source = models.CharField(max_length=20, default='gazetteer')

    class Meta:
        ordering = ['retail_price']

    def __str__(self):
        return f'{self.name} ({self.city}, {self.state}) ${self.retail_price}'
