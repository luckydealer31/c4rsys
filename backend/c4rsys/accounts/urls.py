from django.urls import path

from c4rsys.accounts import views

urlpatterns = [
    path("auth/csrf", views.csrf),
    path("auth/register/start", views.register_start),
    path("auth/register/verify", views.register_verify),
    path("auth/register/complete", views.register_complete),
    path("auth/login/start", views.login_start),
    path("auth/login/verify", views.login_verify),
    path("auth/login/complete", views.login_complete),
    path("auth/code/resend", views.code_resend),
    path("auth/logout", views.logout),
    path("me", views.me),
    path("me/confirmations", views.confirmation_start),
    path("me/personal-data", views.personal_data),
    path("me/username", views.username),
    path("me/password", views.password_change),
    path("me/email/start", views.email_change_start),
    path("me/email/complete", views.email_change_complete),
    path("me/sessions", views.sessions),
    path("me/sessions/end-all", views.sessions_end_all),
    path("me/sessions/<uuid:session_id>/end", views.session_end),
    path("me/session-settings", views.session_settings),
    path("me/session-settings/reset", views.session_settings_reset),
]
