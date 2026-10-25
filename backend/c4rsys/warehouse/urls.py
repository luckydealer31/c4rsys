from django.urls import path

from c4rsys.warehouse import views

urlpatterns = [
    path("catalog/manufacturers", views.manufacturers),
    path("catalog/warehouse-options", views.options),
    path("c/<str:cmp>/warehouse/storages", views.storages),
    path("c/<str:cmp>/warehouse/categories", views.categories),
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
    path("c/<str:cmp>/warehouse/imports/template", views.import_template),
    path("c/<str:cmp>/warehouse/imports", views.import_list),
    path("c/<str:cmp>/warehouse/imports/<uuid:operation_id>", views.import_detail),
    path("c/<str:cmp>/warehouse/imports/<uuid:operation_id>/file", views.import_file),
    path("c/<str:cmp>/warehouse/imports/<uuid:operation_id>/decisions", views.import_decisions),
    path("c/<str:cmp>/warehouse/imports/<uuid:operation_id>/post", views.import_post),
    path("c/<str:cmp>/warehouse/imports/<uuid:operation_id>/cancel", views.import_cancel),
]
