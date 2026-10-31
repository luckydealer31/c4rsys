"""Goods in the single search: by a part of the name, the article, the barcode and the supplier's
article (ТЗ § 2.8.3). The catalog is shared by the company, so «View stock» anywhere is needed
(§ 11.1.1)."""

from uuid import UUID

from django.db.models import Q

from c4rsys.companies import access
from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query, exact_text
from c4rsys.warehouse import catalog
from c4rsys.warehouse.models import Item, SupplierArticle


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    if not access.RightsResolver().has_permission(actor, "warehouse.view"):
        return []
    text = query.text
    by_supplier = set(
        SupplierArticle.objects.filter(item__company_id=company_id, article__iexact=text)
        .values_list("item_id", flat=True)
        .distinct()
    )
    ids = set(
        catalog.search(company_id, text, Item.State.ACTIVE).values_list("id", flat=True)[
            : limit * 5
        ]
    )
    ids |= by_supplier
    if query.uuid is not None:
        ids.add(query.uuid)
    found = catalog.items_of(company_id).filter(pk__in=ids)
    exact_ids = set(
        catalog.items_of(company_id)
        .filter(Q(article__iexact=text) | Q(barcodes__code=text) | Q(pk__in=by_supplier))
        .values_list("id", flat=True)
    )
    hits = []
    for item in found[: limit * 2]:
        exact = item.id in exact_ids or item.pk == query.uuid or exact_text(item.name, query)
        hits.append(Hit("item", str(item.id), item.name, item.article, exact=exact))
    return hits
