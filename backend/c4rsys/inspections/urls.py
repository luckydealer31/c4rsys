from django.urls import path

from c4rsys.inspections import views

urlpatterns = [
    path("c/<str:cmp>/inspections", views.inspection_list),
    path("c/<str:cmp>/inspections/<uuid:inspection_id>", views.inspection_detail),
    path("c/<str:cmp>/inspections/<uuid:inspection_id>/history", views.inspection_history),
    path("c/<str:cmp>/inspections/<uuid:inspection_id>/start", views.inspection_start),
    path("c/<str:cmp>/inspections/<uuid:inspection_id>/complete", views.inspection_complete),
    path("c/<str:cmp>/inspections/<uuid:inspection_id>/cancel", views.inspection_cancel),
    path("c/<str:cmp>/inspections/<uuid:inspection_id>/terminate", views.inspection_terminate),
    path("c/<str:cmp>/inspections/<uuid:inspection_id>/invalidate", views.inspection_invalidate),
    path("c/<str:cmp>/inspection-templates", views.template_list),
    path("c/<str:cmp>/inspection-templates/<uuid:template_id>", views.template_detail),
    path("c/<str:cmp>/inspection-policies", views.policy_detail),
    path("c/<str:cmp>/rentals/<uuid:rental_id>/inspection-reasons", views.rental_missing),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/inspections", views.vehicle_inspections),
    path("c/<str:cmp>/vehicles/<uuid:card_id>/damages", views.vehicle_damages),
    path("c/<str:cmp>/damages/<uuid:damage_id>", views.damage_detail),
    path("c/<str:cmp>/damages/<uuid:damage_id>/estimate", views.damage_estimate),
]
