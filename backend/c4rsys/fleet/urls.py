from django.urls import path

from c4rsys.fleet import views

urlpatterns = [
    path("catalog/vehicle-options", views.catalog_options),
    path("catalog/vehicle-makes", views.catalog_makes),
    path("catalog/vehicle-makes/<str:make>/models", views.catalog_models),
    path("c/<str:cmp>/vehicles", views.vehicle_list),
    path("c/<str:cmp>/vehicles/<uuid:card_id>", views.vehicle_detail),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/details", views.vehicle_details),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/acquisition", views.vehicle_acquisition),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/to-warehouse", views.vehicle_to_warehouse),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/to-garage", views.vehicle_to_garage),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/move", views.vehicle_move),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/moves", views.vehicle_moves),
    path("c/<str:cmp>/drivers", views.drivers),
]
