"""Issuing readable c4rsys IDs (ТЗ § 2.1.4, § 2.6.1)."""

from uuid import UUID

from django.db import IntegrityError, transaction

from c4rsys.platform.ids import generate_public_id
from c4rsys.platform.models import PublicIdRegistry

MAX_ATTEMPTS = 10


def allocate_public_id(prefix: str, *, entity_type: str, entity_id: UUID) -> str:
    """Generate a unique readable ID and record it forever.

    Must be called inside the transaction that creates the entity, so a rolled-back creation does
    not consume the ID. A collision with an existing ID is retried with a new random value.
    """
    for _ in range(MAX_ATTEMPTS):
        candidate = generate_public_id(prefix)
        try:
            with transaction.atomic():
                PublicIdRegistry.objects.create(
                    public_id=candidate, entity_type=entity_type, entity_id=entity_id
                )
        except IntegrityError:
            if PublicIdRegistry.objects.filter(
                entity_type=entity_type, entity_id=entity_id
            ).exists():
                raise
            continue
        return candidate
    raise RuntimeError(f"Could not allocate a unique {prefix} ID after {MAX_ATTEMPTS} attempts")
