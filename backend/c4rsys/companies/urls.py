from django.urls import path

from c4rsys.companies import views

urlpatterns = [
    path("companies", views.companies),
    path("c/<str:cmp>", views.company),
    path("c/<str:cmp>/locations", views.location_list),
    path("c/<str:cmp>/locations/<uuid:location_id>", views.location_detail),
    path("c/<str:cmp>/locations/<uuid:location_id>/contexts", views.location_contexts),
]
