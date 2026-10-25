from django.urls import path

from c4rsys.warehouse import stock_views, views

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
    path("c/<str:cmp>/warehouse/items/<uuid:item_id>/stock", stock_views.item_stock),
    path("c/<str:cmp>/warehouse/items/<uuid:item_id>/lots", stock_views.item_lots),
    path(
        "c/<str:cmp>/warehouse/items/<uuid:item_id>/supplier-articles",
        stock_views.supplier_articles,
    ),
    path(
        "c/<str:cmp>/warehouse/items/<uuid:item_id>/supplier-articles/<uuid:entry_id>",
        stock_views.supplier_article_remove,
    ),
    path("c/<str:cmp>/warehouse/supplier-articles/lookup", stock_views.supplier_article_lookup),
    path("c/<str:cmp>/warehouse/stock", stock_views.stock_list),
    path("c/<str:cmp>/warehouse/assets/<uuid:asset_id>/tags", stock_views.asset_tags),
    path("c/<str:cmp>/warehouse/lots/<uuid:lot_id>/tags", stock_views.lot_tags),
    path("c/<str:cmp>/warehouse/receipts", stock_views.receipt_list),
    path("c/<str:cmp>/warehouse/receipts/<uuid:receipt_id>", stock_views.receipt_detail),
    path("c/<str:cmp>/warehouse/receipts/<uuid:receipt_id>/post", stock_views.receipt_post),
    path(
        "c/<str:cmp>/warehouse/receipts/<uuid:receipt_id>/lines/<uuid:line_id>/cost",
        stock_views.receipt_line_cost,
    ),
    path("c/<str:cmp>/warehouse/purchases", stock_views.purchase_list),
    path("c/<str:cmp>/warehouse/purchases/<uuid:purchase_id>", stock_views.purchase_detail),
    path(
        "c/<str:cmp>/warehouse/purchases/<uuid:purchase_id>/cancel-rest",
        stock_views.purchase_cancel_rest,
    ),
    path("c/<str:cmp>/warehouse/imports/template", views.import_template),
    path("c/<str:cmp>/warehouse/imports", views.import_list),
    path("c/<str:cmp>/warehouse/imports/<uuid:operation_id>", views.import_detail),
    path("c/<str:cmp>/warehouse/imports/<uuid:operation_id>/file", views.import_file),
    path("c/<str:cmp>/warehouse/imports/<uuid:operation_id>/decisions", views.import_decisions),
    path("c/<str:cmp>/warehouse/imports/<uuid:operation_id>/post", views.import_post),
    path("c/<str:cmp>/warehouse/imports/<uuid:operation_id>/cancel", views.import_cancel),
]
