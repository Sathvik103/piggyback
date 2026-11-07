"""Data model for a Piggyback scenario.

All times are hours since scenario start (t=0). `Scenario.now` is the moment the
operator is looking at the network; anything after it is unknown to them.
Field order of every dataclass is the CSV column order and the column order of
the matching table in db/schema.sql (tests enforce this).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional

# Scan event types an operator can see.
SCAN_INDUCTED = "INDUCTED"      # accepted at origin hub
SCAN_LOADED = "LOADED"          # loaded onto a trip leg
SCAN_UNLOADED = "UNLOADED"      # unloaded from a trip leg at a hub
SCAN_HELD = "HELD"              # put on hold at a hub (docs, damage, inspection)
SCAN_DELIVERED = "DELIVERED"    # handed over at destination hub
SCAN_TYPES = (SCAN_INDUCTED, SCAN_LOADED, SCAN_UNLOADED, SCAN_HELD, SCAN_DELIVERED)

# Ground-truth anomaly types (never visible to detection code).
ANOMALY_MISROUTE = "MISROUTE"   # loaded onto the wrong trip, now at an off-plan hub
ANOMALY_LOST_SCAN = "LOST_SCAN"  # physically moved on, but scans stopped
ANOMALY_HELD = "HELD"           # held at a hub and never released
ANOMALY_MISSORT = "MISSORT"     # caught in a burst incident at one hub
ANOMALY_TYPES = (ANOMALY_MISROUTE, ANOMALY_LOST_SCAN, ANOMALY_HELD, ANOMALY_MISSORT)

DISRUPTION_TRIP_BREAKDOWN = "TRIP_BREAKDOWN"
DISRUPTION_HUB_CLOSURE = "HUB_CLOSURE"
DISRUPTION_LANE_DELAY = "LANE_DELAY"
DISRUPTION_TYPES = (DISRUPTION_TRIP_BREAKDOWN, DISRUPTION_HUB_CLOSURE, DISRUPTION_LANE_DELAY)

PRIORITIES = ("EXPRESS", "STANDARD", "ECONOMY")


@dataclass(frozen=True)
class ScenarioConfig:
    seed: int = 42
    n_shipments: int = 3000
    horizon_h: float = 72.0
    now_h: float = 36.0
    # 0 = trucks run mostly empty, 1 = almost no spare room on any leg.
    capacity_tightness: float = 0.5
    # Share of in-flight shipments that get an individual stranding.
    anomaly_rate: float = 0.04
    n_incidents: int = 2          # MISSORT bursts
    n_disruptions: int = 8


@dataclass(frozen=True)
class Hub:
    hub_id: str
    name: str
    state: str
    lat: float
    lon: float
    sort_capacity_per_h: int


@dataclass(frozen=True)
class Lane:
    lane_id: str
    from_hub: str
    to_hub: str
    distance_km: float
    travel_h: float


@dataclass(frozen=True)
class VehicleType:
    code: str
    capacity_kg: float
    capacity_m3: float
    cost_per_km_inr: float


@dataclass(frozen=True)
class Corridor:
    corridor_id: str
    hub_sequence: str          # "BLR>HYD>NAG", the stops in order
    vehicle_type: str
    frequency_h: int           # 12 or 24
    first_departure_h: float
    capacity_kg: float
    capacity_m3: float
    cost_per_km_inr: float


@dataclass(frozen=True)
class Trip:
    trip_id: str
    corridor_id: str
    departure_h: float
    arrival_h: float


@dataclass(frozen=True)
class TripLeg:
    leg_id: str
    trip_id: str
    seq: int
    lane_id: str
    from_hub: str
    to_hub: str
    departure_h: float
    arrival_h: float
    spare_kg: float
    spare_m3: float


@dataclass(frozen=True)
class Shipment:
    shipment_id: str
    origin_hub: str
    dest_hub: str
    priority: str
    weight_kg: float
    volume_m3: float
    created_h: float
    deadline_h: float
    planned_legs: str          # leg_ids joined by ";" (the booked itinerary)
    planned_arrival_h: float


@dataclass(frozen=True)
class ScanEvent:
    scan_id: str
    shipment_id: str
    ts_h: float
    hub_id: str
    event_type: str
    leg_id: Optional[str] = None
    note: Optional[str] = None


@dataclass(frozen=True)
class Disruption:
    disruption_id: str
    kind: str
    start_h: float
    end_h: float
    trip_id: Optional[str] = None
    hub_id: Optional[str] = None
    lane_id: Optional[str] = None
    delay_h: Optional[float] = None


@dataclass(frozen=True)
class TruthRecord:
    """What really happened to a stranded shipment. Scoring only."""
    shipment_id: str
    anomaly_type: str
    stranded_at_h: float
    true_hub_id: str           # where the parcel physically is at `now`
    incident_id: Optional[str] = None


@dataclass
class Scenario:
    config: ScenarioConfig
    hubs: list[Hub]
    lanes: list[Lane]
    corridors: list[Corridor]
    trips: list[Trip]
    legs: list[TripLeg]
    shipments: list[Shipment]
    scans: list[ScanEvent]           # visible scans only (ts_h <= now)
    disruptions: list[Disruption]
    truth: list[TruthRecord] = field(default_factory=list)

    @property
    def now(self) -> float:
        return self.config.now_h


def column_names(cls) -> list[str]:
    return [f.name for f in fields(cls)]
