"""ZIP code centroids (Census 2024 ZCTA gazetteer) and distances in miles."""

from __future__ import annotations

import functools
import math
from pathlib import Path

GAZETTEER = Path(__file__).resolve().parent / "data" / "2024_Gaz_zcta_national.txt"


@functools.lru_cache(maxsize=None)
def centroids() -> dict[str, tuple[float, float]]:
    points = {}
    with open(GAZETTEER, encoding="utf-8") as f:
        next(f)
        for line in f:
            p = line.rstrip("\n").split("\t")
            points[p[0].strip()] = (float(p[5]), float(p[6]))
    return points


def zip_known(zip5: str) -> bool:
    return zip5 in centroids()


def centroid(zip5: str) -> tuple[float, float] | None:
    return centroids().get(zip5)


def miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def zips_within(zip5: str, radius: float, limit: int) -> list[tuple[float, str]]:
    """Nearest ZIPs first, the origin included."""
    origin = centroids()[zip5]
    dlat = radius / 69.0
    dlon = radius / (69.0 * max(math.cos(math.radians(origin[0])), 0.1))
    near = [(miles(origin, p), z) for z, p in centroids().items()
            if abs(p[0] - origin[0]) <= dlat and abs(p[1] - origin[1]) <= dlon]
    return sorted((d, z) for d, z in near if d <= radius)[:limit]
