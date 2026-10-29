from django.urls import path

from c4rsys.delivery import views

urlpatterns = [
    path("c/<str:cmp>/deliveries", views.delivery_list),
    path("c/<str:cmp>/deliveries/record", views.delivery_record),
    path("c/<str:cmp>/deliveries/drivers", views.drivers),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>", views.delivery_detail),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/assign", views.delivery_assign),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/take", views.delivery_take),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/start", views.delivery_start),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/finish", views.delivery_finish),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/terminate", views.delivery_terminate),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/cancel", views.delivery_cancel),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/attach", views.delivery_attach),
]
