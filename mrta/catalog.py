"""Robot types, task templates, locations and the phrases used to describe them.

Everything the scenario generator and the natural-language renderer need to
know about the world lives here, so new robot types or task kinds can be added
in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import Mobility, Zone


@dataclass(frozen=True)
class RobotType:
    name: str
    prefix: str
    mobility: Mobility
    capabilities: tuple[str, ...]
    optional: dict[str, float] = field(default_factory=dict)  # capability -> probability
    speed: tuple[float, float] = (0.0, 0.0)  # m/s range, rounded to 0.5
    payload: tuple[int, int] = (0, 0)  # kg range
    endurance: tuple[int, int] = (86400, 86400)  # s range, rounded to 60
    label: str = ""


ROBOT_TYPES: dict[str, RobotType] = {
    "quadcopter": RobotType(
        name="quadcopter", prefix="D", mobility=Mobility.AIR,
        capabilities=("camera", "carry"), optional={"thermal": 0.5},
        speed=(6.0, 10.0), payload=(2, 2), endurance=(900, 1500), label="quadcopter drone",
    ),
    "rover": RobotType(
        name="rover", prefix="G", mobility=Mobility.GROUND,
        capabilities=("carry", "camera"), optional={"thermal": 0.3},
        speed=(1.5, 3.0), payload=(15, 30), endurance=(3600, 5400), label="ground rover",
    ),
    "mobile_manipulator": RobotType(
        name="mobile_manipulator", prefix="M", mobility=Mobility.GROUND,
        capabilities=("carry", "manipulate", "camera"),
        speed=(1.0, 1.5), payload=(5, 10), endurance=(2400, 3600), label="mobile manipulator",
    ),
    "fixed_arm": RobotType(
        name="fixed_arm", prefix="A", mobility=Mobility.FIXED,
        capabilities=("manipulate",), label="fixed robotic arm",
    ),
}

# Capability names as they appear in briefs.
CAPABILITY_TEXT = {
    "camera": "camera",
    "thermal": "thermal camera",
    "carry": "cargo carrier",
    "manipulate": "robotic arm",
}


@dataclass(frozen=True)
class TaskKind:
    name: str
    required: tuple[str, ...]
    zones: tuple[Zone, ...]
    duration: tuple[int, int]  # s range, rounded to 30
    explicit: str
    informal: tuple[str, ...]
    payload: tuple[int, int] = (0, 0)
    items: tuple[str, ...] = ()
    weight: float = 1.0  # sampling weight


TASK_KINDS: dict[str, TaskKind] = {
    "inspect_roof": TaskKind(
        name="inspect_roof", required=("camera",), zones=(Zone.ROOF,), duration=(60, 180),
        explicit="Inspect {loc} with a camera",
        informal=("have a look at {loc}", "get eyes on {loc} and check the panels",
                  "do a visual check of {loc}"),
        weight=1.0,
    ),
    "thermal_scan": TaskKind(
        name="thermal_scan", required=("thermal",), zones=(Zone.OUTDOOR, Zone.ROOF), duration=(90, 240),
        explicit="Run a thermal scan of {loc}",
        informal=("check {loc} for hot spots", "do a heat check over {loc}",
                  "look for overheating equipment around {loc}"),
        weight=0.8,
    ),
    "photo_survey": TaskKind(
        name="photo_survey", required=("camera",), zones=(Zone.OUTDOOR, Zone.INDOOR), duration=(60, 150),
        explicit="Photograph {loc} for the condition report",
        informal=("grab photos of {loc} for the condition report", "document {loc} with pictures",
                  "we need pictures of {loc} for the report"),
        weight=1.0,
    ),
    "deliver": TaskKind(
        name="deliver", required=("carry",), zones=(Zone.OUTDOOR, Zone.INDOOR), duration=(30, 60),
        payload=(1, 14),
        items=("toolkit", "spare pump", "sensor box", "first-aid kit", "cable reel", "battery pack",
               "pressure gauge", "replacement filter"),
        explicit="Deliver the {item} ({w} kg) to {loc}",
        informal=("get the {item} ({w} kg) over to {loc}", "{loc} needs the {item}, which weighs {w} kg",
                  "drop the {item} off at {loc}; it weighs {w} kg"),
        weight=1.4,
    ),
    "valve_service": TaskKind(
        name="valve_service", required=("manipulate",), zones=(Zone.INDOOR, Zone.OUTDOOR), duration=(120, 300),
        explicit="Tighten the valve at {loc}",
        informal=("someone has to tighten the valve at {loc}", "the valve at {loc} needs tightening"),
        weight=1.0,
    ),
    "part_swap": TaskKind(
        name="part_swap", required=("manipulate",), zones=(Zone.INDOOR,), duration=(150, 330),
        explicit="Replace the filter cartridge at {loc}",
        informal=("swap the filter cartridge at {loc}", "change the filter at {loc}"),
        weight=0.8,
    ),
    "sample_pickup": TaskKind(
        name="sample_pickup", required=("manipulate", "carry"), zones=(Zone.OUTDOOR,), duration=(60, 180),
        payload=(1, 3),
        explicit="Collect a soil sample ({w} kg) at {loc} and keep it on board",
        informal=("pick up a soil sample at {loc} and keep it on board; it weighs {w} kg",),
        weight=0.5,
    ),
}

MANIPULATION_KINDS = ("valve_service", "part_swap")

LOCATION_NAMES: dict[Zone, tuple[str, ...]] = {
    Zone.ROOF: ("north roof", "warehouse roof", "solar canopy", "office roof", "water tower top"),
    Zone.INDOOR: ("pump house", "control room", "workshop", "electrical room", "storage hall"),
    Zone.OUTDOOR: ("site B", "east fence", "loading dock", "water tank", "parking lot", "gate 2",
                   "fuel depot", "antenna mast", "south yard", "transformer pad"),
}
WORKCELLS = ("workcell West", "workcell East")  # indoor stations for fixed arms
BASE = "base"
