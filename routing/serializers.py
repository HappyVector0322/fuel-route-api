from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=200, help_text='"City, ST", "lat,lng", a ZIP code, or an address in the USA.'
    )
    finish = serializers.CharField(max_length=200, help_text='Same formats as start.')
    geometry = serializers.ChoiceField(
        choices=['simplified', 'full', 'none'], default='simplified', required=False,
        help_text='Route line to include in the GeoJSON: simplified (default), full, or none.',
    )
    stop_penalty = serializers.FloatField(
        min_value=0, max_value=1000, required=False,
        help_text='Dollars of "inconvenience" charged per stop when optimising (default 5). '
                  '0 returns the absolute cheapest plan, however many stops it takes.',
    )
