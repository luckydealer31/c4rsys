from django.urls import path

from c4rsys.cabinet import garage_views, views

G = "cabinet/garage/<str:vehicle_id>"

urlpatterns = [
    path("site", views.site),
    path("cabinet/companies", views.companies),
    path("cabinet/companies/<uuid:link_id>/history", views.history),
    path("cabinet/documents", views.document_list),
    path("cabinet/documents/<uuid:document_id>", views.document_detail),
    path("cabinet/documents/<uuid:document_id>/file", views.document_file),
    path("cabinet/documents/<uuid:document_id>/code", views.document_code),
    path("cabinet/documents/<uuid:document_id>/decide", views.document_decide),
    path("cabinet/garage", views.garage),
    # A vehicle of the personal garage (block 12, part Б).
    path(G, garage_views.vehicle),
    path(f"{G}/documents", garage_views.add_document),
    path(f"{G}/documents/versions/<uuid:version_id>/file", garage_views.document_file),
    path(f"{G}/copies/<uuid:card_id>/<uuid:version_id>/file", garage_views.copy_file),
    path(f"{G}/copies/<uuid:card_id>/<uuid:version_id>/decide", garage_views.copy_decide),
    path("cabinet/reversals", garage_views.reversals),
    path("cabinet/reversals/<uuid:reversal_id>/answer", garage_views.reversal_answer),
    path("cabinet/approvals/<uuid:approval_id>/answer", views.approval_answer),
]
