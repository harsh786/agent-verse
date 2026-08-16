"""Geofence trigger — detects when a device enters or exits a polygon."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)


@dataclass
class LatLng:
    lat: float
    lng: float


@dataclass
class GeofenceRegion:
    region_id: str
    name: str
    # Polygon as list of [lat, lng] pairs
    polygon: list[LatLng] = field(default_factory=list)
    radius_meters: float | None = None  # circle if set
    center: LatLng | None = None


def point_in_polygon(point: LatLng, polygon: list[LatLng]) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    x, y = point.lng, point.lat
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i].lng, polygon[i].lat
        xj, yj = polygon[j].lng, polygon[j].lat
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-10) + xi):
            inside = not inside
        j = i
    return inside


def haversine_meters(a: LatLng, b: LatLng) -> float:
    """Great-circle distance between two LatLng points in metres."""
    R = 6_371_000  # noqa: N806
    φ1, φ2 = math.radians(a.lat), math.radians(b.lat)
    dφ = math.radians(b.lat - a.lat)
    dλ = math.radians(b.lng - a.lng)
    h = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.asin(math.sqrt(h))


class GeofenceTriggerEvaluator:
    """Evaluate a location event against geofence regions for a tenant."""

    def __init__(
        self,
        *,
        trigger_store: Any = None,
        dispatcher: Any = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher

    def is_inside(self, region: GeofenceRegion, point: LatLng) -> bool:
        """Return True if point is inside the geofence region."""
        if region.radius_meters is not None and region.center is not None:
            return haversine_meters(region.center, point) <= region.radius_meters
        if region.polygon:
            return point_in_polygon(point, region.polygon)
        return False

    async def evaluate(
        self,
        device_id: str,
        point: LatLng,
        regions: list[GeofenceRegion],
        *,
        tenant_id: str,
        plan: str = "free",
    ) -> list[Any]:
        """Check device position against all regions and fire geofence triggers."""
        fired: list[Any] = []
        if self._store is None or self._dispatcher is None:
            return fired

        triggers = await self._store.find_by_type_async(
            "geofence", tenant_id=tenant_id
        )
        exit_triggers = await self._store.find_by_type_async(
            "geofence", tenant_id=tenant_id
        )

        from types import SimpleNamespace
        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)

        for region in regions:
            inside = self.is_inside(region, point)
            event_type = "enter" if inside else "exit"
            pool = triggers if inside else exit_triggers
            payload = {
                "device_id": device_id,
                "region_id": region.region_id,
                "region_name": region.name,
                "lat": point.lat,
                "lng": point.lng,
                "event_type": event_type,
            }
            for trigger in pool:
                spec = trigger.get("spec", trigger)
                watch_region = (
                    getattr(spec, "geofence_region_id", "")
                    or trigger.get("geofence_region_id", "")
                )
                if watch_region and watch_region != region.region_id:
                    continue
                # Filter by geofence_action ("enter" | "exit" | "both")
                action = getattr(spec, "geofence_action", "both") or "both"
                if action not in (event_type, "both"):
                    continue
                try:
                    result = await self._dispatcher.dispatch(spec, payload, tenant_ctx)
                    fired.append(result)
                except Exception as exc:
                    _log.warning("geofence_dispatch_error: %s", exc)

        return fired
