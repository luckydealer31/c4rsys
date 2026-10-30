from django.urls import path

from c4rsys.cabinet import views

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
    path("cabinet/approvals/<uuid:approval_id>/answer", views.approval_answer),
]
