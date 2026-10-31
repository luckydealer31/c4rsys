from django.apps import AppConfig


class OrdersConfig(AppConfig):
    """Orders: client, composition of operations, closing conditions (ТЗ гл. 12)."""

    name = "c4rsys.orders"
    label = "orders"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # § 2.8.2: the records of the module in the single search line.
        from c4rsys.orders import search as orders_search
        from c4rsys.platform import search as platform_search

        platform_search.register("order", orders_search.provider)
        from c4rsys.notifications import rules
        from c4rsys.notifications import services as notifications
        from c4rsys.orders import notices

        # § 20.3.5: an order closed with debt (decision З-7).
        rules.on_journal("order.closed", notices.on_order_closed)
        # § 17.2.16 (block 16): a correction of a payment of a closed order brings the debt back.
        for action in ("offset.cancelled", "offset.corrected", "payment.relinked"):
            rules.on_journal(action, notices.on_payment_corrected)
        notifications.register_check(notices.run)
        # § 12.3.1: a position waiting for the client keeps the order open until answered.
        from c4rsys.orders import approvals
        from c4rsys.orders import services as orders

        orders.register_operations(approvals.operations_for_order)
        from c4rsys.orders import cancellers

        cancellers.register()
        # § 12.5.6, З14: a recalculated position recalculates its discount.
        from c4rsys.finance import charges
        from c4rsys.orders import discounts

        charges.register_after_change(discounts.on_charge_changed)
        # § 6.5.2–6.5.3: orders in the merge and the split of client cards.
        from c4rsys.orders import client_cards
        from c4rsys.parties import merges

        merges.register_check("client", client_cards.merge_check)
        merges.register_card_records(client_cards.PROVIDER)
        charges.register_companion(lambda source_type: [discounts.source_of(source_type)])
