"""Static network inputs: hub cities, vehicle types and road-time assumptions."""

from __future__ import annotations

import math

from .models import Hub, VehicleType

# (code, city, state, lat, lon, sort capacity parcels/h)
_HUBS = [
    ("DEL", "Delhi", "Delhi", 28.6139, 77.2090, 9000),
    ("MUM", "Mumbai", "Maharashtra", 19.0760, 72.8777, 9000),
    ("BLR", "Bengaluru", "Karnataka", 12.9716, 77.5946, 8000),
    ("HYD", "Hyderabad", "Telangana", 17.3850, 78.4867, 7000),
    ("MAA", "Chennai", "Tamil Nadu", 13.0827, 80.2707, 7000),
    ("CCU", "Kolkata", "West Bengal", 22.5726, 88.3639, 7000),
    ("AMD", "Ahmedabad", "Gujarat", 23.0225, 72.5714, 6000),
    ("PNQ", "Pune", "Maharashtra", 18.5204, 73.8567, 6000),
    ("JAI", "Jaipur", "Rajasthan", 26.9124, 75.7873, 4500),
    ("LKO", "Lucknow", "Uttar Pradesh", 26.8467, 80.9462, 4500),
    ("NAG", "Nagpur", "Maharashtra", 21.1458, 79.0882, 5000),
    ("IDR", "Indore", "Madhya Pradesh", 22.7196, 75.8577, 4000),
    ("BHO", "Bhopal", "Madhya Pradesh", 23.2599, 77.4126, 3500),
    ("PAT", "Patna", "Bihar", 25.5941, 85.1376, 3500),
    ("BBI", "Bhubaneswar", "Odisha", 20.2961, 85.8245, 3500),
    ("VTZ", "Visakhapatnam", "Andhra Pradesh", 17.6868, 83.2185, 3500),
    ("CJB", "Coimbatore", "Tamil Nadu", 11.0168, 76.9558, 3500),
    ("COK", "Kochi", "Kerala", 9.9312, 76.2673, 3500),
    ("GAU", "Guwahati", "Assam", 26.1445, 91.7362, 3000),
    ("CHD", "Chandigarh", "Chandigarh", 30.7333, 76.7794, 3500),
    ("STV", "Surat", "Gujarat", 21.1702, 72.8311, 4000),
    ("RPR", "Raipur", "Chhattisgarh", 21.2514, 81.6296, 3000),
    ("VNS", "Varanasi", "Uttar Pradesh", 25.3176, 82.9739, 3000),
    ("IXR", "Ranchi", "Jharkhand", 23.3441, 85.3096, 3000),
    ("IXM", "Madurai", "Tamil Nadu", 9.9252, 78.1198, 3000),
]

HUBS: list[Hub] = [Hub(c, n, s, lat, lon, cap) for c, n, s, lat, lon, cap in _HUBS]

# Assumed INR/km figures for Indian line-haul, not quotes from real carriers.
VEHICLE_TYPES: list[VehicleType] = [
    VehicleType("TATA407_14FT", 2500.0, 18.0, 22.0),
    VehicleType("LCV_20FT", 5000.0, 32.0, 32.0),
    VehicleType("MXL_32FT", 9000.0, 60.0, 48.0),
]

ROAD_CIRCUITY = 1.3          # road km per great-circle km
AVG_SPEED_KMH = 45.0         # line-haul average incl. stops
HUB_DWELL_H = 1.0            # stop time at an intermediate corridor hub
MIN_CONNECTION_H = 2.0       # unload + sort + reload at a transfer hub


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def road_km(a: Hub, b: Hub) -> float:
    return haversine_km(a.lat, a.lon, b.lat, b.lon) * ROAD_CIRCUITY


def road_hours(km: float) -> float:
    return km / AVG_SPEED_KMH
