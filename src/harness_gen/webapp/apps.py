from django.apps import AppConfig


class WebAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "harness_gen.webapp"

    def ready(self) -> None:
        from django.conf import settings

        from harness_gen.logging_config import configure_logging

        configure_logging(log_directory=settings.BASE_DIR / "logs")
