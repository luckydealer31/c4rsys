from django.urls import path

from c4rsys.fleet import identity_views, owner_views, views

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
    path("catalog/vehicle-options", views.catalog_options),
    path("catalog/vehicle-makes", views.catalog_makes),
    path("catalog/vehicle-makes/<str:make>/models", views.catalog_models),
    path("c/<str:cmp>/vehicles", views.vehicle_list),
    path("c/<str:cmp>/vehicles/schedule", views.vehicle_schedule),
    path("c/<str:cmp>/vehicles/<uuid:card_id>", views.vehicle_detail),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/details", views.vehicle_details),
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
