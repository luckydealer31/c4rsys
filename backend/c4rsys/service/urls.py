from django.urls import path

from c4rsys.service import views

urlpatterns = [
    path("c/<str:cmp>/service/system-works", views.system_works),
    path("c/<str:cmp>/service/works", views.work_list),
    path("c/<str:cmp>/service/works/<uuid:work_id>", views.work_detail),
    path("c/<str:cmp>/service/works/<uuid:work_id>/archive", views.work_archive),
    path("c/<str:cmp>/service/works/<uuid:work_id>/restore", views.work_restore),
    path("c/<str:cmp>/service/rates", views.rates),
    path("c/<str:cmp>/service/posts", views.post_list),
    path("c/<str:cmp>/service/posts/<uuid:post_id>", views.post_detail),
    path("c/<str:cmp>/service/operations", views.operation_list),
    path("c/<str:cmp>/service/operations/<uuid:operation_id>", views.operation_detail),
    path("c/<str:cmp>/service/operations/<uuid:operation_id>/plan", views.operation_plan),
    path("c/<str:cmp>/service/operations/<uuid:operation_id>/accept", views.operation_accept),
    path("c/<str:cmp>/service/operations/<uuid:operation_id>/place", views.operation_place),
    path("c/<str:cmp>/service/operations/<uuid:operation_id>/jobs", views.operation_add_job),
    path("c/<str:cmp>/service/operations/<uuid:operation_id>/finish", views.operation_finish),
    path("c/<str:cmp>/service/operations/<uuid:operation_id>/cancel", views.operation_cancel),
    path(
        "c/<str:cmp>/service/operations/<uuid:operation_id>/terminate",
        views.operation_terminate,
    ),
    path("c/<str:cmp>/service/jobs/<uuid:job_id>/assign", views.job_assign),
    path("c/<str:cmp>/service/jobs/<uuid:job_id>/take", views.job_take),
    path("c/<str:cmp>/service/jobs/<uuid:job_id>/start", views.job_start),
    path("c/<str:cmp>/service/jobs/<uuid:job_id>/complete", views.job_complete),
    path("c/<str:cmp>/service/jobs/<uuid:job_id>/terminate", views.job_terminate),
    path("c/<str:cmp>/service/jobs/<uuid:job_id>/cancel", views.job_cancel),
    path("c/<str:cmp>/service/jobs/<uuid:job_id>/price", views.job_price),
    path("c/<str:cmp>/service/queue", views.queue),
    path("c/<str:cmp>/service/executors", views.executors),
]
