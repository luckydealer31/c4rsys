from django.urls import path

from c4rsys.parties import views

urlpatterns = [
    path("c/<str:cmp>/clients", views.client_list),
    path("c/<str:cmp>/clients/matches", views.client_matches),
    path("c/<str:cmp>/clients/<uuid:client_id>", views.client_detail),
]
