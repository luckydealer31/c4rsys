"""The data of a list of vehicles read once for the list, not once for every vehicle (audit item
12: the list of vehicles read about 12 queries a vehicle).

The summary of a vehicle is the same as without it: the helpers of the card take a `Batch` and
read from it what they would query. The match of identifiers (§ 8.6.1) is only pre-filtered
here: an identifier no other global vehicle has cannot match; one that another vehicle has is
checked by the full rule of `identity.candidates` (regions, exceptions, countries).
"""

from collections import defaultdict
from uuid import UUID

from c4rsys.companies import locations
from c4rsys.fleet import characteristics
from c4rsys.fleet.models import (
    IdentifierConflict,
    IdentifierConflictParty,
    OdometerReading,
    OdometerReplacement,
    Vehicle,
    VehicleCard,
    VehicleOwnerAnswer,
    VehicleOwnerClaim,
    VehicleOwnerRequest,
    VehiclePlate,
    VehicleRestriction,
)

SharedKey = tuple[str, str, str]


class Batch:
    def __init__(self, cards: list[VehicleCard]) -> None:
        card_ids = [c.id for c in cards]
        vehicles = [c.vehicle for c in cards]
        vehicle_ids = [v.id for v in vehicles]
        self.plates: dict[UUID, VehiclePlate] = {}
        for plate in VehiclePlate.objects.filter(
            vehicle_id__in=vehicle_ids, valid_to__isnull=True
        ).order_by("pk"):
            self.plates.setdefault(plate.vehicle_id, plate)
        self.claims: dict[UUID, VehicleOwnerClaim] = {}
        for claim in (
            VehicleOwnerClaim.objects.select_related("client")
            .filter(vehicle_card_id__in=card_ids)
            .order_by("-created_at")
        ):
            self.claims.setdefault(claim.vehicle_card_id, claim)
        self.requests: dict[UUID, VehicleOwnerRequest] = {}
        for request in VehicleOwnerRequest.objects.filter(vehicle_card_id__in=card_ids).order_by(
            "-created_at"
        ):
            self.requests.setdefault(request.vehicle_card_id, request)
        self.answers: dict[UUID, dict[str, bool]] = defaultdict(dict)
        for answer in VehicleOwnerAnswer.objects.filter(
            request_id__in=[r.id for r in self.requests.values()]
        ):
            self.answers[answer.request_id][answer.question] = answer.answer
        # The latest measurement of every card: one row a card (PostgreSQL DISTINCT ON).
        self.readings: dict[UUID, OdometerReading] = {
            r.vehicle_card_id: r
            for r in OdometerReading.objects.filter(
                vehicle_card_id__in=card_ids, replaced_by__isnull=True
            )
            .order_by("vehicle_card_id", "-measured_at", "-entered_at")
            .distinct("vehicle_card_id")
        }
        self.replacements: dict[UUID, list[OdometerReplacement]] = defaultdict(list)
        for rep in OdometerReplacement.objects.filter(vehicle_card_id__in=card_ids).order_by(
            "replaced_at"
        ):
            self.replacements[rep.vehicle_card_id].append(rep)
        self.restrictions: dict[UUID, list[VehicleRestriction]] = defaultdict(list)
        for r in (
            VehicleRestriction.objects.select_related("custom_reason")
            .filter(vehicle_card_id__in=card_ids)
            .order_by("-starts_at")
        ):
            self.restrictions[r.vehicle_card_id].append(r)
        # The enabled contexts of each accounting location; the cards are of one company.
        self.enabled: dict[UUID, set[str]] = {}
        for company_id in {c.company_id for c in cards}:
            self.enabled.update(
                locations.enabled_context_types_of(
                    company_id,
                    {
                        c.accounting_location_id
                        for c in cards
                        if c.company_id == company_id and c.accounting_location_id
                    },
                )
            )
        kinds: dict[UUID, set[str]] = defaultdict(set)
        for card_id, kind in IdentifierConflictParty.objects.filter(
            vehicle_card_id__in=card_ids, left_at__isnull=True, conflict__is_open=True
        ).values_list("vehicle_card_id", "conflict__kind"):
            kinds[card_id].add(kind)
        self.conflict_kinds = {card_id: sorted(found) for card_id, found in kinds.items()}
        self.shared = self._shared(vehicles)

    def _shared(self, vehicles: list[Vehicle]) -> dict[SharedKey, set[UUID]]:
        """The global vehicles holding each identifier of the list, these vehicles included."""
        found: dict[SharedKey, set[UUID]] = defaultdict(set)
        vins = {v.vin for v in vehicles if v.vin}
        if vins:
            for vin, vehicle_id in Vehicle.objects.filter(
                merged_into__isnull=True, vin__in=vins
            ).values_list("vin", "id"):
                found[(IdentifierConflict.Kind.VIN, vin, "")].add(vehicle_id)
        numbers = {p.number_normalized for p in self.plates.values() if p.number_normalized}
        if numbers:
            for number, country, vehicle_id in VehiclePlate.objects.filter(
                number_normalized__in=numbers, valid_to__isnull=True
            ).values_list("number_normalized", "country", "vehicle_id"):
                found[(IdentifierConflict.Kind.PLATE, number, country)].add(vehicle_id)
        for key in characteristics.NUMBERS:
            values = {getattr(v, key) for v in vehicles if getattr(v, key)}
            if values:
                for value, vehicle_id in Vehicle.objects.filter(
                    merged_into__isnull=True, **{f"{key}__in": values}
                ).values_list(key, "id"):
                    found[(key, value, "")].add(vehicle_id)
        return found

    def plate(self, vehicle: Vehicle) -> VehiclePlate | None:
        return self.plates.get(vehicle.id)

    def may_match(self, vehicle: Vehicle, kind: str, value: str, country: str) -> bool:
        """False when no other global vehicle has the identifier; True — check it fully."""
        plate_country = country if kind == IdentifierConflict.Kind.PLATE else ""
        return bool(self.shared.get((kind, value, plate_country), set()) - {vehicle.id})
