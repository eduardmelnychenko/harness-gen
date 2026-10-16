from django.contrib import admin
from django.urls import path

from harness_gen.webapp import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", views.login_view, name="login"),
    path("accounts/logout/", views.logout_view, name="logout"),
    path("", views.home, name="home"),
    path("provider/", views.provider_settings, name="provider-settings"),
    path("session/", views.session_view, name="session"),
]
