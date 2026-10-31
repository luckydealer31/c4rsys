from django.urls import path

from c4rsys.fleet import identity_views, lifecycle_views, owner_views, views

V = "c/<str:cmp>/vehicles/<uuid:card_id>/"

urlpatterns = [
    # Block 12: merge and split of cards, proposals of corrections (§ 8.6.5–8.6.6, § 8.2.1).
    path(f"{V}merge", identity_views.merge),
    path(f"{V}merge/preview", identity_views.merge_preview),
    path(f"{V}merge/code", identity_views.merge_code),
    path(f"{V}merges", identity_views.merge_list),
    path(f"{V}merges/<uuid:merge_id>/split", identity_views.split),
    path(f"{V}merges/<uuid:merge_id>/split/preview", identity_views.split_preview),
    path(f"{V}proposals", identity_views.propose),
    # Block 12, part Б: lifecycle of the card (§ 8.1.6, § 8.2.10–8.2.13, § 8.3, § 8.4.2, § 8.7,
    # § 8.8).
    path(f"{V}restrictions", lifecycle_views.restriction_set),
    path(f"{V}restrictions/<uuid:restriction_id>", lifecycle_views.restriction_change),
    path(f"{V}restrictions/<uuid:restriction_id>/lift", lifecycle_views.restriction_lift),
    path("c/<str:cmp>/vehicle-restriction-reasons", lifecycle_views.restriction_reasons),
    path(f"{V}archive", lifecycle_views.archive),
    path(f"{V}restore", lifecycle_views.restore),
    path(f"{V}end-management", lifecycle_views.end_management),
    path(f"{V}take-into-management", lifecycle_views.take_into_management),
    path(f"{V}dispose", lifecycle_views.dispose),
    path(f"{V}transfer-reversal", lifecycle_views.transfer_reversal),
    path(f"{V}odometer-unit", lifecycle_views.unit_change),
    path(f"{V}odometer-unit/preview", lifecycle_views.unit_preview),
    path(f"{V}odometer/replacement-preview", lifecycle_views.replacement_preview),
    path(f"{V}disclosure", lifecycle_views.disclosure_set),
    path(
        f"{V}global-documents/versions/<uuid:version_id>/file",
        lifecycle_views.global_document_file,
    ),
    path("catalog/vehicle-options", views.catalog_options),
    path("catalog/vehicle-makes", views.catalog_makes),
    path("catalog/vehicle-makes/<str:make>/models", views.catalog_models),
    path("c/<str:cmp>/vehicles", views.vehicle_list),
    path("c/<str:cmp>/vehicles/schedule", views.vehicle_schedule),
    path("c/<str:cmp>/vehicles/<uuid:card_id>", views.vehicle_detail),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/details", views.vehicle_details),
    # В-12.2: the card applies the owner's newer data.
    path("c/<str:cmp>/vehicles/<uuid:card_id>/owner-data", views.vehicle_owner_data),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/acquisition", views.vehicle_acquisition),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/to-warehouse", views.vehicle_to_warehouse),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/to-garage", views.vehicle_to_garage),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/move", views.vehicle_move),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/moves", views.vehicle_moves),
    path("c/<str:cmp>/drivers", views.drivers),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/owner-requests", owner_views.card_requests),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/owner-requests/cancel", owner_views.cancel),
    path("c/<str:cmp>/incoming-owner-requests", owner_views.incoming),
    path(
        "c/<str:cmp>/incoming-owner-requests/<uuid:request_id>/answer",
        owner_views.incoming_answer,
    ),
    path("owner-requests", owner_views.mine),
    path("owner-requests/<uuid:request_id>/answer", owner_views.my_answer),
]
