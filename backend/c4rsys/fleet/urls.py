from django.urls import path

from c4rsys.fleet import views

urlpatterns = [
    path("c/<str:cmp>/vehicles", views.vehicle_list),
    path("c/<str:cmp>/vehicles/<uuid:card_id>", views.vehicle_detail),
]
