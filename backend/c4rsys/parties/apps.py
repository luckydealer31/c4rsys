from django.apps import AppConfig


class PartiesConfig(AppConfig):
    """Clients and counterparties: local cards of the company (ТЗ гл. 6)."""

    name = "c4rsys.parties"
    label = "parties"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # § 2.8.2: the records of the module in the single search line.
        from c4rsys.parties import search as parties_search
        from c4rsys.platform import search as platform_search

        platform_search.register("client", parties_search.clients)
        platform_search.register("counterparty", parties_search.counterparty_hits)
        from c4rsys.notifications import rules
        from c4rsys.notifications import services as notifications
        from c4rsys.parties import notices

        # § 20.3.4: requests to link with a profile and the answers are mandatory notifications.
        notifications.register_check(notices.requests)
        rules.on_journal("profile_link.accepted", notices.on_answer)
        rules.on_journal("profile_link.declined", notices.on_answer)
        # § 6.4.4: the answers to requests of fresh data.
        rules.on_journal("profile_data_request.accepted", notices.on_data_answer)
        rules.on_journal("profile_data_request.declined", notices.on_data_answer)
        # § 24.1.8: the cards linked with a profile move with the merge of accounts.
        from c4rsys.parties.userlinks import PartyLinks
        from c4rsys.platform import userlinks

        userlinks.register(PartyLinks())
