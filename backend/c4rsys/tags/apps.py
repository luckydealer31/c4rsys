from django.apps import AppConfig


class TagsConfig(AppConfig):
    """Tags of the company and personal tags of an employee, put on records of any section
    (customer's decisions of 17.11.2026)."""

    name = "c4rsys.tags"
    label = "tags"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # A tag is found in the single search line by its name.
        from c4rsys.platform import search as platform_search
        from c4rsys.tags import search as tags_search

        platform_search.register("tag", tags_search.provider)
