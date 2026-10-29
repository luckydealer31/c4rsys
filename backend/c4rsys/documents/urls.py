from django.urls import path

from c4rsys.documents import views

urlpatterns = [
    path("c/<str:cmp>/document-kinds", views.kind_list),
    path("c/<str:cmp>/documents", views.document_list),
    path("c/<str:cmp>/documents/<uuid:document_id>", views.document_detail),
    path("c/<str:cmp>/documents/<uuid:document_id>/versions", views.document_version),
    path("c/<str:cmp>/documents/<uuid:document_id>/links", views.document_link),
    path("c/<str:cmp>/documents/<uuid:document_id>/unlink", views.document_unlink),
    path("c/<str:cmp>/documents/<uuid:document_id>/archive", views.document_archive),
]
