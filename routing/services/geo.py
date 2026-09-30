import numpy as np

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1, lng1, lat2, lng2):
    """Vectorised great-circle distance in miles (accepts scalars or numpy arrays)."""
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def to_unit_vectors(lat, lng):
    """Lat/lng (degrees) -> points on the unit sphere, so a plain Euclidean KD-tree
    gives correct nearest neighbours anywhere on Earth (chord ~= arc for short hops)."""
    lat, lng = np.radians(lat), np.radians(lng)
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lng), cos_lat * np.sin(lng), np.sin(lat)))


def chord_to_miles(chord):
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.clip(chord / 2, 0.0, 1.0))


def miles_to_chord(miles):
    return 2 * np.sin(miles / (2 * EARTH_RADIUS_MILES))


def densify(lats, lngs, step_miles):
    """Insert interpolated points so no segment is longer than `step_miles`.

    OSRM geometries can have vertices tens of miles apart on straight interstates;
    densifying lets a nearest-vertex search stand in for a nearest-segment search.
    Returns (lats, lngs, cumulative_miles).
    """
    seg = haversine_miles(lats[:-1], lngs[:-1], lats[1:], lngs[1:])
    pieces = np.maximum(1, np.ceil(seg / step_miles).astype(int))
    # For every segment, fractions 0, 1/k, ..., (k-1)/k; then append the final vertex.
    seg_idx = np.repeat(np.arange(len(seg)), pieces)
    frac = np.arange(pieces.sum()) - np.repeat(np.cumsum(pieces) - pieces, pieces)
    frac = frac / np.repeat(pieces, pieces)
    out_lat = np.append(lats[seg_idx] + (lats[seg_idx + 1] - lats[seg_idx]) * frac, lats[-1])
    out_lng = np.append(lngs[seg_idx] + (lngs[seg_idx + 1] - lngs[seg_idx]) * frac, lngs[-1])
    seg_start_miles = np.concatenate(([0.0], np.cumsum(seg)))
    cum = np.append(seg_start_miles[seg_idx] + seg[seg_idx] * frac, seg_start_miles[-1])
    return out_lat, out_lng, cum
