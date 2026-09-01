"""Deterministic ROI geometry conversion and validation."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence

from spectral_api.domain.models import ROIGeometryType

Point = tuple[float, float]


class InvalidGeometryError(ValueError):
    """Geometry cannot be persisted without changing its scientific meaning."""


def canonicalize_geometry(
    geometry_type: ROIGeometryType, coordinates: Sequence[Sequence[float]]
) -> list[Point]:
    points = [(float(point[0]), float(point[1])) for point in coordinates if len(point) == 2]
    if len(points) != len(coordinates):
        raise InvalidGeometryError("Every vertex must contain exactly two coordinates")
    if any(not math.isfinite(value) for point in points for value in point):
        raise InvalidGeometryError("ROI vertices must be finite")
    if any(value < 0 or value > 1 for point in points for value in point):
        raise InvalidGeometryError("ROI must stay within the reference image")

    deduplicated: list[Point] = []
    for point in points:
        if not deduplicated or point != deduplicated[-1]:
            deduplicated.append(point)
    if deduplicated and deduplicated[0] == deduplicated[-1]:
        deduplicated.pop()
    if len(set(deduplicated)) < 3:
        raise InvalidGeometryError("ROI must contain at least three distinct vertices")

    if geometry_type is ROIGeometryType.RECTANGLE:
        xs = {point[0] for point in deduplicated}
        ys = {point[1] for point in deduplicated}
        if len(deduplicated) != 4 or len(xs) != 2 or len(ys) != 2:
            raise InvalidGeometryError("Rectangle must contain four axis-aligned corners")
        expected = {(x, y) for x in xs for y in ys}
        if set(deduplicated) != expected:
            raise InvalidGeometryError("Rectangle vertices are not axis aligned")

    closed = [*deduplicated, deduplicated[0]]
    if polygon_area(closed) <= 0:
        raise InvalidGeometryError("ROI area must be positive")
    if polygon_self_intersects(closed):
        raise InvalidGeometryError("ROI polygon must not intersect itself")
    return closed


def normalized_to_pixels(points: Sequence[Point], width: int, height: int) -> list[Point]:
    if width <= 0 or height <= 0:
        raise InvalidGeometryError("Reference image dimensions are invalid")
    return [(x * width, y * height) for x, y in points]


def pixels_to_spatial(
    points: Sequence[Point], transform: Sequence[float] | None
) -> list[Point] | None:
    if transform is None or len(transform) < 6:
        return None
    a, b, c, d, e, f = (float(value) for value in transform[:6])
    if not all(math.isfinite(value) for value in (a, b, c, d, e, f)):
        return None
    return [(a * x + b * y + c, d * x + e * y + f) for x, y in points]


def polygon_area(points: Sequence[Point]) -> float:
    return (
        abs(
            sum(
                left[0] * right[1] - right[0] * left[1]
                for left, right in zip(points, points[1:], strict=False)
            )
        )
        / 2
    )


def geometry_checksum(
    geometry_type: ROIGeometryType,
    normalized: Sequence[Point],
    width: int,
    height: int,
) -> str:
    canonical = json.dumps(
        {
            "geometry_type": geometry_type.value,
            "normalized_coordinates": [[round(x, 12), round(y, 12)] for x, y in normalized],
            "reference_width": width,
            "reference_height": height,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def polygon_self_intersects(points: Sequence[Point]) -> bool:
    edges = list(zip(points, points[1:], strict=False))
    for first_index, first in enumerate(edges):
        for second_index, second in enumerate(edges):
            if second_index <= first_index + 1:
                continue
            if first_index == 0 and second_index == len(edges) - 1:
                continue
            if _segments_intersect(first[0], first[1], second[0], second[1]):
                return True
    return False


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orientation(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    values = (
        orientation(a, b, c),
        orientation(a, b, d),
        orientation(c, d, a),
        orientation(c, d, b),
    )
    epsilon = 1e-12
    return values[0] * values[1] < -epsilon and values[2] * values[3] < -epsilon
