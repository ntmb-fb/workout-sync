"""Strava sport_type -> FIT sport/sub-sport and Polar sport id.

Polar ids come from the community list at
https://github.com/pcolby/bipolar/wiki/Polar-Sport-Types (Polar does not publish them).
"""
from __future__ import annotations

from dataclasses import dataclass

from fit_tool.profile.profile_type import Sport, SubSport

POLAR_OTHER_OUTDOOR = 16
POLAR_OTHER_INDOOR = 83


@dataclass(frozen=True)
class SportMap:
    fit_sport: Sport
    fit_sub_sport: SubSport
    polar: int


G = SubSport.GENERIC

# (fit sport, fit sub-sport, polar id)
_MAP: dict[str, SportMap] = {
    "Run": SportMap(Sport.RUNNING, G, 1),
    "TrailRun": SportMap(Sport.RUNNING, SubSport.TRAIL, 27),
    "VirtualRun": SportMap(Sport.RUNNING, SubSport.TREADMILL, 17),
    "Ride": SportMap(Sport.CYCLING, G, 2),
    "MountainBikeRide": SportMap(Sport.CYCLING, SubSport.MOUNTAIN, 5),
    "GravelRide": SportMap(Sport.CYCLING, SubSport.GRAVEL_CYCLING, 2),
    "EBikeRide": SportMap(Sport.E_BIKING, G, 2),
    "EMountainBikeRide": SportMap(Sport.E_BIKING, SubSport.E_BIKE_MOUNTAIN, 5),
    "VirtualRide": SportMap(Sport.CYCLING, SubSport.VIRTUAL_ACTIVITY, 18),
    "Velomobile": SportMap(Sport.CYCLING, G, 2),
    "Handcycle": SportMap(Sport.CYCLING, G, 2),
    "Walk": SportMap(Sport.WALKING, G, 3),
    "Hike": SportMap(Sport.HIKING, G, 11),
    "Snowshoe": SportMap(Sport.HIKING, G, 116),
    "Swim": SportMap(Sport.SWIMMING, SubSport.LAP_SWIMMING, 103),
    "WeightTraining": SportMap(Sport.TRAINING, SubSport.STRENGTH_TRAINING, 15),
    "Crossfit": SportMap(Sport.TRAINING, G, 34),
    "HighIntensityIntervalTraining": SportMap(Sport.HIIT, G, 20),
    "Workout": SportMap(Sport.TRAINING, G, POLAR_OTHER_INDOOR),
    "Yoga": SportMap(Sport.TRAINING, SubSport.YOGA, 33),
    "Pilates": SportMap(Sport.TRAINING, G, 65),
    "Elliptical": SportMap(Sport.FITNESS_EQUIPMENT, G, 55),
    "StairStepper": SportMap(Sport.FITNESS_EQUIPMENT, G, POLAR_OTHER_INDOOR),
    "Rowing": SportMap(Sport.ROWING, G, 8),
    "VirtualRow": SportMap(Sport.ROWING, SubSport.INDOOR_ROWING, 117),
    "AlpineSki": SportMap(Sport.ALPINE_SKIING, G, 7),
    "BackcountrySki": SportMap(Sport.ALPINE_SKIING, G, 113),
    "NordicSki": SportMap(Sport.CROSS_COUNTRY_SKIING, G, 25),
    "RollerSki": SportMap(Sport.CROSS_COUNTRY_SKIING, G, 60),
    "Snowboard": SportMap(Sport.SNOWBOARDING, G, 22),
    "IceSkate": SportMap(Sport.ICE_SKATING, G, 28),
    "InlineSkate": SportMap(Sport.INLINE_SKATING, G, 29),
    "Kayaking": SportMap(Sport.KAYAKING, G, 95),
    "Canoeing": SportMap(Sport.PADDLING, G, 96),
    "StandUpPaddling": SportMap(Sport.STAND_UP_PADDLEBOARDING, G, POLAR_OTHER_OUTDOOR),
    "Surfing": SportMap(Sport.SURFING, G, 102),
    "Windsurf": SportMap(Sport.WINDSURFING, G, 101),
    "Kitesurf": SportMap(Sport.KITESURFING, G, 100),
    "Sail": SportMap(Sport.SAILING, G, 88),
    "RockClimbing": SportMap(Sport.ROCK_CLIMBING, G, 94),
    "Golf": SportMap(Sport.GOLF, G, 35),
    "Soccer": SportMap(Sport.SOCCER, G, 39),
    "Tennis": SportMap(Sport.TENNIS, G, 12),
    "Badminton": SportMap(Sport.RACKET, G, 14),
    "Squash": SportMap(Sport.RACKET, G, 13),
    "TableTennis": SportMap(Sport.RACKET, G, 91),
    "Wheelchair": SportMap(Sport.GENERIC, G, 89),
}

_GENERIC_OUTDOOR = SportMap(Sport.GENERIC, G, POLAR_OTHER_OUTDOOR)
_GENERIC_INDOOR = SportMap(Sport.GENERIC, G, POLAR_OTHER_INDOOR)


def map_sport(sport_type: str, *, trainer: bool = False, has_gps: bool = False) -> SportMap:
    m = _MAP.get(sport_type)
    if m is None:
        return _GENERIC_OUTDOOR if has_gps else _GENERIC_INDOOR
    # Strava has no separate types for these; derive them from the trainer flag / GPS.
    if sport_type == "Run" and trainer:
        return _MAP["VirtualRun"]
    if sport_type == "Ride" and trainer:
        return SportMap(Sport.CYCLING, SubSport.INDOOR_CYCLING, 18)
    if sport_type == "Swim" and has_gps:
        return SportMap(Sport.SWIMMING, SubSport.OPEN_WATER, 105)
    return m
