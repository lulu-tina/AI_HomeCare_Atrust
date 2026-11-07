"""Offline stand-in for OSRM/Google when they are unreachable: minutes = max(3, straight-line km x 3.0)."""
from unittest.mock import patch
import numpy as np
import caregiver_engine as e


def minutes(lat1, lon1, lat2, lon2, *a, **k):
    if (lat1, lon1) == (lat2, lon2):
        return 0.0
    return float(max(3.0, e._distance_m(float(lat1), float(lon1), float(lat2), float(lon2)) / 1000.0 * 3.0))


def patches():
    return [patch.object(e, '_get_travel_provider', return_value='local'),
            patch.object(e, 'get_osrm_travel_time', side_effect=lambda la1, lo1, la2, lo2, *a, **k: minutes(la1, lo1, la2, lo2)),
            patch.object(e, 'get_google_travel_time', side_effect=lambda la1, lo1, la2, lo2, *a, **k: minutes(la1, lo1, la2, lo2)),
            patch.object(e, 'prefetch_google_transit_pairs', lambda *a, **k: None)]
