from django.urls import path

from c4rsys.warehouse import views

urlpatterns = [
    path("catalog/manufacturers", views.manufacturers),
    path("catalog/warehouse-options", views.options),
    path("c/<str:cmp>/warehouse/storages", views.storages),
    path("c/<str:cmp>/warehouse/items", views.item_list),
    path("c/<str:cmp>/warehouse/items/<uuid:item_id>", views.item_detail),
    path("c/<str:cmp>/warehouse/items/<uuid:item_id>/archive", views.item_archive),
    path("c/<str:cmp>/warehouse/items/<uuid:item_id>/restore", views.item_restore),
    path("c/<str:cmp>/warehouse/items/<uuid:item_id>/packagings", views.packaging_add),
    path(
        "c/<str:cmp>/warehouse/items/<uuid:item_id>/packagings/<uuid:packaging_id>/archive",
        views.packaging_archive,
    ),
    path("c/<str:cmp>/warehouse/items/<uuid:item_id>/barcodes", views.barcode_add),
    path(
        "c/<str:cmp>/warehouse/items/<uuid:item_id>/barcodes/<uuid:barcode_id>",
        views.barcode_remove,
    ),
    path("c/<str:cmp>/warehouse/barcodes/<str:code>", views.barcode_lookup),
]
