from django.urls import path

from c4rsys.companies import views

urlpatterns = [
    path("companies", views.companies),
    path("c/<str:cmp>", views.company),
]
