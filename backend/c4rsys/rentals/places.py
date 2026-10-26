"""The place of the issue (ТЗ § 14.2.1): «in the location» or «other» of the chosen location. An
«other» place is described by an address, coordinates (also from the device's geolocation) or a
comment — at least one of them (customer's decision В1). It never creates a location and does not
change the vehicle's location."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from c4rsys.platform import errors

LOCATION = "location"
OTHER = "other"


@dataclass(frozen=True)
class Place:
    kind: str = LOCATION
    note: str = ""
    address: str = ""
    lat: Decimal | None = None
    lng: Decimal | None = None

    def checked(self) -> "Place":
        if self.kind not in (LOCATION, OTHER):
            raise errors.validation_error("place_kind")
        if self.kind == LOCATION:
            return Place()
        if (self.lat is None) != (self.lng is None):
            raise errors.validation_error("place_lng", code="COORDINATES_INCOMPLETE")
        if self.lat is not None and self.lng is not None:
            if not (-90 <= self.lat <= 90 and -180 <= self.lng <= 180):
                raise errors.validation_error("place_lat", code="COORDINATES_INVALID")
        place = Place(OTHER, self.note.strip(), self.address.strip(), self.lat, self.lng)
        if not (place.note or place.address or place.lat is not None):
            raise errors.validation_error("place_note", code="PLACE_NOT_DESCRIBED")
        return place

    def summary(self) -> str:
        """One line for records that keep only a note, such as the issue inspection."""
        parts = [self.address, self.note]
        if self.lat is not None:
            parts.append(f"{self.lat}, {self.lng}")
        return " · ".join(p for p in parts if p)[:500]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "note": self.note,
            "address": self.address,
            "lat": str(self.lat) if self.lat is not None else None,
            "lng": str(self.lng) if self.lng is not None else None,
        }


def of_issue(rental: Any) -> Place:
    return Place(
        rental.issue_place_kind or LOCATION,
        rental.issue_place_note,
        rental.issue_place_address,
        rental.issue_place_lat,
        rental.issue_place_lng,
    )


def set_issue(rental: Any, place: Place | None) -> None:
    """Writes the place of the issue; None — no location chosen yet (a booking)."""
    rental.issue_place_kind = place.kind if place else ""
    rental.issue_place_note = place.note if place else ""
    rental.issue_place_address = place.address if place else ""
    rental.issue_place_lat = place.lat if place else None
    rental.issue_place_lng = place.lng if place else None
