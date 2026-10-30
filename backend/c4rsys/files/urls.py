from django.urls import path

from c4rsys.files import views

urlpatterns = [
    path("files", views.upload),
    path("files/<uuid:file_id>", views.download),
    path("files/<uuid:file_id>/details", views.details),
]
