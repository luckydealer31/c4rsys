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
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/price", views.delivery_price),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/correct", views.delivery_correct),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/annul", views.delivery_annul),
    path("c/<str:cmp>/deliveries/<uuid:delivery_id>/expenses", views.delivery_expense),
    path("c/<str:cmp>/delivery-services", views.service_list),
    path("c/<str:cmp>/delivery-services/<uuid:service_id>", views.service_detail),
]
