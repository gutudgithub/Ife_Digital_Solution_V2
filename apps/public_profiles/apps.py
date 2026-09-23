from django.apps import AppConfig


class PublicProfilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.public_profiles"

    def ready(self) -> None:
        from apps.public_profiles import checks  # noqa: F401
