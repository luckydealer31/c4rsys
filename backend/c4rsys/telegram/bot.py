"""The talk with the bot (customer's decisions of 22.11.2026). The first part: binding the
Telegram account by the link of the profile and the shared number, the choice of the company, and
a vehicle made by a photo of its paper — the bot shows what was read and makes the vehicle after
«Confirm» in the chat; and a document of a supplier, which becomes a draft of a warehouse
receipt to be checked and posted on the site.

The bot acts as the employee it is bound to: every right is checked as on the site, and what it
makes goes through the same commands with the same journal."""

import hashlib
import logging
from typing import Any
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from c4rsys.accounts.models import UserAccount
from c4rsys.companies import access, locations
from c4rsys.companies import services as companies
from c4rsys.companies.models import ActivityContext, Employment
from c4rsys.files import checks
from c4rsys.files import services as files
from c4rsys.files.models import StoredFile
from c4rsys.finance import expense_drafts
from c4rsys.fleet import characteristics, owners, recognition
from c4rsys.fleet import services as fleet
from c4rsys.fleet.catalog.match import in_text as catalog_match
from c4rsys.fleet.models import VehicleCard
from c4rsys.parties import counterparties
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.telegram import api, links
from c4rsys.telegram.models import TelegramLink
from c4rsys.telegram.texts import FIELDS, field, language, t
from c4rsys.warehouse import catalog, stock
from c4rsys.warehouse import recognition as invoices
from c4rsys.warehouse.models import Item, Receipt

logger = logging.getLogger(__name__)

RELATIONS = ("own", "managed", "client")
# What of a paper goes into the card as it is read.
PLAIN = (
    "plate",
    "plate_country",
    "vin",
    "body_number",
    "chassis_number",
    "frame_number",
    "engine_number",
    "color",
    "body_type",
    "fuel_type",
    "transmission",
    "drive",
)


def _buttons(*rows: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row] for row in rows
        ]
    }


def _save(link: TelegramLink, state: dict[str, Any]) -> None:
    link.state = state
    link.save(update_fields=["state"])


def _contexts(user: UserAccount) -> list[companies.CompanyContext]:
    """The companies the person works in now."""
    found = []
    employed = Employment.objects.select_related("company").filter(
        user=user, state=Employment.State.ACTIVE
    )
    for employment in employed.order_by("company__display_name"):
        try:
            found.append(companies.resolve_company(user, employment.company.public_id, None))
        except errors.ApiError:
            continue
    return found


def _context(link: TelegramLink) -> companies.CompanyContext | None:
    if link.company is None:
        return None
    try:
        return companies.resolve_company(link.user, link.company.public_id, None)
    except errors.ApiError:
        return None


def _may_create(actor: Actor, location_id: UUID | None) -> bool:
    return access.RightsResolver().has_permission(actor, "vehicle.create", location_id=location_id)


def _places(context: companies.CompanyContext, actor: Actor) -> list[tuple[str, str]]:
    """The locations the employee may create a vehicle in: id and name."""
    return [
        (str(place.id), place.name)
        for place in locations.locations_of(context.company.id)
        if _may_create(actor, place.id)
    ]


def _warehouses(context: companies.CompanyContext, actor: Actor) -> list[ActivityContext]:
    """The warehouses the employee may receive goods into."""
    return [
        place
        for place in locations.storage_contexts(context.company.id)
        if access.RightsResolver().has_permission(
            actor, "warehouse.receive", location_id=place.location_id, context_id=place.id
        )
    ]


def _clear(chat_id: int, message_id: Any) -> None:
    """The message whose button was pressed is taken away (customer's remark of 23.11.2026): the
    answered questions do not pile up in the chat and cannot be pressed twice. Telegram does not
    delete a message older than two days: then only its buttons are taken off."""
    if not isinstance(message_id, int):
        return
    try:
        api.call("deleteMessage", chat_id=chat_id, message_id=message_id)
    except Exception:  # noqa: BLE001 -- an old message stays, without its buttons
        try:
            api.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id)
        except Exception:  # noqa: BLE001 -- nothing more can be done with it
            logger.warning("Telegram did not let the bot clear a message")


class Talk:
    """One update of a bound Telegram account."""

    def __init__(self, link: TelegramLink, update_id: int, spoken: str | None = None) -> None:
        self.link = link
        self.update_id = update_id
        # The language of the profile; while it is not chosen — the one Telegram names.
        self.lang = language(link.user.language or spoken)

    def say(self, key: str, markup: dict[str, Any] | None = None, **params: object) -> None:
        api.send(self.link.chat_id, t(self.lang, key, **params), markup)

    def key(self, part: str) -> str:
        return f"tg-{self.update_id}-{part}"

    # --- The company and the menu ---------------------------------------------------------------

    def menu(self) -> None:
        contexts = _contexts(self.link.user)
        current = _context(self.link)
        if current is None and len(contexts) == 1:
            current = contexts[0]
            self.link.company = current.company
            self.link.save(update_fields=["company"])
        if current is None:
            if not contexts:
                return self.say("no_company")
            rows = [[(c.company.display_name, f"co:{c.company.public_id}")] for c in contexts]
            return self.say("choose_company", _buttons(*rows))
        rows = [
            [(t(self.lang, "menu_vehicle"), "veh")],
            [(t(self.lang, "menu_invoice"), "inv")],
            [(t(self.lang, "menu_expense"), "exp")],
        ]
        if len(contexts) > 1:
            rows.append([(t(self.lang, "menu_company"), "cos")])
        markup = _buttons(*rows)
        # The window of the bot (customer's decision of 23.11.2026): the inspections of the site
        # open inside Telegram, signed in by the bound account. Telegram takes only an https page.
        site = settings.C4R_PUBLIC_URL.rstrip("/")
        if site.startswith("https://"):
            page = f"{site}/tg?to=/c/{current.company.public_id}/inspections"
            markup["inline_keyboard"].insert(
                3, [{"text": t(self.lang, "menu_inspection"), "web_app": {"url": page}}]
            )
        self.say("menu", markup, company=current.company.display_name)

    def choose(self, public_id: str) -> None:
        for context in _contexts(self.link.user):
            if context.company.public_id == public_id:
                self.link.company = context.company
                self.link.state = {}
                self.link.save(update_fields=["company", "state"])
        self.menu()

    def other_company(self) -> None:
        self.link.company = None
        self.link.state = {}
        self.link.save(update_fields=["company", "state"])
        self.menu()

    # --- A vehicle by a photo -------------------------------------------------------------------

    def _acting(self) -> tuple[companies.CompanyContext, Actor] | None:
        context = _context(self.link)
        if context is None:
            self.menu()
            return None
        return context, companies.company_actor(self.link.user, context, None)

    def vehicle(self) -> None:
        acting = self._acting()
        if acting is None:
            return
        context, actor = acting
        if not recognition.configured():
            return self.say("off")
        if not _may_create(actor, None) and not _places(context, actor):
            return self.say("no_right")
        # Whose the vehicle is, is asked first (customer's decision of 23.11.2026): the answer goes
        # both to the vehicle made here and to the form opened on the site.
        _save(self.link, {"step": "whose"})
        row = [(t(self.lang, f"rel_{relation}"), f"rel:{relation}") for relation in RELATIONS]
        self.say("whose", _buttons(row))

    def whose(self, relation: str) -> None:
        if self.link.state.get("step") != "whose" or relation not in RELATIONS:
            return self.say("start_over")
        _save(self.link, {"step": "photo", "relation": relation})
        self.say("send_photo")

    def _file(self, message: dict[str, Any], purpose: str) -> tuple[UUID, bytes, str] | None:
        """The photo or the file of the message, checked and kept: its id, content and type."""
        if message.get("photo"):
            source = message["photo"][-1]
        else:
            source = message["document"]
        if int(source.get("file_size") or 0) > files.MAX_DOCUMENT_BYTES:
            self.say("file_wrong")
            return None
        self.say("reading")
        content = api.download(str(source["file_id"]))
        try:
            content_type = files.document_type(content)
        except errors.ApiError:
            self.say("file_wrong")
            return None
        if checks.inspect(content_type, content):
            self.say("file_wrong")
            return None
        user_id = self.link.user_id

        def keep(ctx: CommandContext) -> CommandResult:
            item = files.store(
                purpose=purpose,
                uploaded_by=user_id,
                content=content,
                content_type=content_type,
            )
            files.checked(item)
            ctx.audit(
                "file.uploaded",
                object_type="stored_file",
                object_id=item.id,
                after={
                    "purpose": item.purpose,
                    "content_type": content_type,
                    "size": item.size,
                    "state": item.state,
                    "through": "telegram",
                },
            )
            return CommandResult(201, {"id": str(item.id)})

        kept = run_command(
            command="file.upload",
            actor=Actor(user_id=user_id),
            idempotency_key=self.key("file"),
            payload={"sha256": hashlib.sha256(content).hexdigest()},
            handler=keep,
        )
        return UUID(kept.body["id"]), content, content_type

    def photo(self, message: dict[str, Any]) -> None:
        step = self.link.state.get("step")
        if step == "invoice":
            return self.invoice_photo(message)
        if step == "expense":
            return self.expense_photo(message)
        if step != "photo":
            return self.menu()
        relation = str(self.link.state.get("relation") or VehicleCard.Relation.OWN)
        taken = self._file(message, StoredFile.Purpose.VEHICLE_PAPER)
        if taken is None:
            return None
        file_id, content, content_type = taken
        try:
            paper = recognition.read_kept(file_id, content, content_type)
        except errors.ApiError as exc:
            return self.say("off" if exc.code == "RECOGNITION_NOT_CONFIGURED" else "read_failed")
        read: dict[str, Any] = {**paper.texts, **paper.numbers}
        make, model = read["make"], read["model"]
        name = " ".join(filter(None, [make, model, read["plate"]])) or read["vin"]
        if not name:
            return self.say("read_failed")
        data: dict[str, Any] = {key: read[key] for key in PLAIN if read[key]}
        data.update({key: value for key, value in paper.numbers.items() if value is not None})
        data["vehicle_type"] = read["vehicle_type"] or "passenger_car"
        match = next(iter(catalog_match(f"{make} {model}")), None) if make else None
        if match is not None:
            data["vehicle_type"] = match.vehicle_type
            data["make"] = match.make
            data["model" if match.model else "model_other"] = match.model or model
        else:
            data["make_other"] = make
            data["model_other"] = model
        state = {
            "step": "confirm",
            "relation": relation,
            "data": data,
            "name": name,
            "owner": {"name": read["owner_name"], "kind": read["owner_kind"] or "person"},
        }
        _save(self.link, state)
        lines = [
            f"{field(self.lang, key)}: {read[key]}"
            for key in FIELDS
            if read.get(key) not in ("", None)
        ]
        text = "\n".join([t(self.lang, "read_title"), *lines, "", t(self.lang, "read_ask")])
        # Two ways (customer's decision of 22.11.2026): the vehicle is made here at once, or the
        # form of a new vehicle is opened on the site, filled from the same paper, to be checked.
        acting = self._acting()
        if acting is None:
            return None
        site = (
            f"{settings.C4R_PUBLIC_URL.rstrip('/')}/c/{acting[0].company.public_id}"
            f"/vehicles/new?relation={relation}&paper={file_id}"
        )
        markup = _buttons([(t(self.lang, "create_now"), "ok")], [(t(self.lang, "cancel"), "no")])
        markup["inline_keyboard"].insert(1, [{"text": t(self.lang, "check_on_site"), "url": site}])
        api.send(self.link.chat_id, text, markup)

    def confirmed(self) -> None:
        """«Create now»: the location is asked when there are several, then the vehicle is made."""
        state = self.link.state
        acting = self._acting()
        relation = str(state.get("relation"))
        if state.get("step") != "confirm" or relation not in RELATIONS or acting is None:
            return self.say("start_over")
        context, actor = acting
        if relation == VehicleCard.Relation.CLIENT:
            return self.create(relation, None)
        places = _places(context, actor)
        if not places:
            return self.say("no_location")
        if len(places) == 1:
            return self.create(relation, UUID(places[0][0]))
        _save(self.link, {**state, "step": "where"})
        rows = [[(name, f"loc:{place_id}")] for place_id, name in places]
        self.say("where", _buttons(*rows))

    def cancel(self) -> None:
        _save(self.link, {})
        self.say("cancelled")
        self.menu()

    def where(self, place_id: str) -> None:
        state = self.link.state
        if state.get("step") != "where":
            return self.say("start_over")
        try:
            location_id = UUID(place_id)
        except ValueError:
            return self.say("start_over")
        self.create(str(state.get("relation")), location_id)

    def create(self, relation: str, location_id: UUID | None) -> None:
        state = self.link.state
        acting = self._acting()
        if acting is None:
            return
        context, actor = acting
        body: dict[str, Any] = {**state["data"], "relation": relation, "name": state["name"]}
        owner = state.get("owner") or {}
        if relation != VehicleCard.Relation.OWN:
            # The owner named in the paper; without one the owner is not given.
            body["owner_kind"] = owner.get("kind") if owner.get("name") else "none"
            body["owner_name"] = owner.get("name") or ""
        try:
            if not _may_create(actor, location_id):
                raise errors.permission_denied("vehicle.create")
            details = characteristics.parse(body, timezone.localdate().year)
            new = fleet.NewVehicle(
                name=str(state["name"])[:200],
                location_id=location_id,
                # PROPOSAL: the bot does not ask the unit of the odometer.
                odometer_unit=VehicleCard.OdometerUnit.KM,
                details=details,
                relation=relation,
                owner=owners.parse(body, relation),
            )

            def handler(ctx: CommandContext) -> CommandResult:
                card = fleet.create_vehicle(ctx, context.company, new)
                return CommandResult(201, {"id": str(card.id), "name": card.name})

            made = run_command(
                command="vehicle.create",
                actor=actor,
                idempotency_key=self.key("vehicle"),
                payload={
                    "name": new.name,
                    "location_id": str(location_id) if location_id else None,
                    "relation": relation,
                    "owner": new.owner.payload(),
                    "through": "telegram",
                    **details.payload(),
                },
                handler=handler,
            )
        except errors.ApiError as exc:
            _save(self.link, {})
            return self.say("create_failed", code=exc.code)
        _save(self.link, {})
        url = (
            f"{settings.C4R_PUBLIC_URL.rstrip('/')}/c/{context.company.public_id}"
            f"/vehicles/{made.body['id']}"
        )
        self.say("created", name=made.body["name"], url=url)
        self.menu()

    # --- A document of a supplier ---------------------------------------------------------------

    def invoice(self, chosen: str = "") -> None:
        """«A delivery note by a photo»: the warehouse is asked when there are several."""
        acting = self._acting()
        if acting is None:
            return
        context, actor = acting
        if not invoices.configured():
            return self.say("off")
        places = _warehouses(context, actor)
        if not places:
            return self.say("no_right_receive")
        if len(places) > 1 and not chosen:
            rows = [[(place.location.name, f"wh:{place.id}")] for place in places]
            return self.say("which_warehouse", _buttons(*rows))
        place = next((p for p in places if str(p.id) == chosen), places[0])
        _save(self.link, {"step": "invoice", "warehouse": str(place.id)})
        self.say("send_invoice", warehouse=place.location.name)

    def invoice_photo(self, message: dict[str, Any]) -> None:
        """The document is read into a draft of a receipt (customer's decision of 22.11.2026):
        the lines whose goods are found in the catalog, with the quantity and the price read. It
        is checked, completed and posted on the site; the stock does not change here."""
        acting = self._acting()
        if acting is None:
            return
        context, actor = acting
        place = next(
            (
                p
                for p in _warehouses(context, actor)
                if str(p.id) == self.link.state.get("warehouse")
            ),
            None,
        )
        if place is None:
            return self.say("no_right_receive")
        taken = self._file(message, StoredFile.Purpose.WAREHOUSE_DOCUMENT)
        if taken is None:
            return None
        file_id, content, content_type = taken
        try:
            document = invoices.read(content, content_type)
        except errors.ApiError as exc:
            return self.say("off" if exc.code == "RECOGNITION_NOT_CONFIGURED" else "read_failed")
        active = catalog.items_of(context.company.id).filter(state=Item.State.ACTIVE)
        lines: list[stock.LineData] = []
        missed: list[str] = []
        for line in document.lines:
            same = (
                active.filter(article__iexact=line.article)
                if line.article
                else active.filter(name__iexact=line.name)
            )
            item = same.first()
            whole = line.quantity is not None and line.quantity == line.quantity.to_integral_value()
            # Not found in the catalog, without a quantity, or a part of a piece: left to the site.
            if (
                item is None
                or line.quantity is None
                or (item.accounting == Item.Accounting.INDIVIDUAL and not whole)
            ):
                missed.append(line.name)
                continue
            lines.append(
                stock.LineData(item_id=item.id, quantity=line.quantity, unit_cost=line.unit_cost)
            )
        _save(self.link, {})
        if not lines:
            self.say("invoice_nothing", missed="\n".join(f"— {name}" for name in missed[:30]))
            return self.menu()
        named = document.supplier_name
        suppliers = (
            [
                card
                for card in counterparties.search(context.company.id, named)[:5]
                if card.company_name.casefold() == named.casefold()
            ]
            if named
            else []
        )
        supplier = suppliers[0] if len(suppliers) == 1 else None
        about = " ".join(
            filter(
                None,
                [
                    document.document_number,
                    document.document_date.isoformat() if document.document_date else "",
                    "" if supplier or not named else named,
                ],
            )
        )
        data = stock.ReceiptData(
            context_id=place.id,
            source=Receipt.Source.PURCHASE,
            supplier_id=supplier.id if supplier else None,
            document_id=file_id,
            comment=about[:500],
            lines=tuple(lines),
        )

        def handler(ctx: CommandContext) -> CommandResult:
            receipt = stock.create_receipt(ctx, context.company, data)
            return CommandResult(201, {"id": str(receipt.id), "number": receipt.number})

        try:
            made = run_command(
                command="warehouse.create_receipt",
                actor=actor,
                idempotency_key=self.key("receipt"),
                payload={**data.as_dict(), "through": "telegram"},
                handler=handler,
            )
        except errors.ApiError as exc:
            self.say("invoice_failed", code=exc.code)
            return self.menu()
        url = (
            f"{settings.C4R_PUBLIC_URL.rstrip('/')}/c/{context.company.public_id}"
            f"/warehouse/receipts/{made.body['id']}"
        )
        self.say(
            "invoice_made",
            number=made.body["number"],
            added=len(lines),
            total=len(document.lines),
            supplier=supplier.company_name if supplier else t(self.lang, "invoice_no_supplier"),
            url=url,
        )
        if missed:
            self.say("invoice_missed", missed="\n".join(f"— {name}" for name in missed[:30]))
        self.menu()

    # --- A receipt of an expense ----------------------------------------------------------------

    def expense(self) -> None:
        if self._acting() is None:
            return
        if not expense_drafts.configured():
            return self.say("off")
        _save(self.link, {"step": "expense"})
        self.say("send_receipt")

    def expense_photo(self, message: dict[str, Any]) -> None:
        """The receipt becomes a draft of an expense (customer's decisions of 23.11.2026): what
        is read, and the photo. The account and the article are chosen on the site by the author,
        who records the expense there; no money changes here."""
        acting = self._acting()
        if acting is None:
            return
        context, actor = acting
        taken = self._file(message, StoredFile.Purpose.EXPENSE_RECEIPT)
        if taken is None:
            return None
        file_id, content, content_type = taken
        try:
            read = expense_drafts.read(content, content_type)
        except errors.ApiError as exc:
            if exc.code == "RECOGNITION_NOT_CONFIGURED":
                return self.say("off")
            # The photo is kept all the same: the draft is filled in by hand on the site.
            read = expense_drafts.Read()

        def handler(ctx: CommandContext) -> CommandResult:
            draft = expense_drafts.create(ctx, context.company, file_id, read, "telegram")
            return CommandResult(201, {"id": str(draft.id)})

        run_command(
            command="expense_draft.create",
            actor=actor,
            idempotency_key=self.key("expense"),
            payload={"file_id": str(file_id)},
            handler=handler,
        )
        _save(self.link, {})
        amount = f"{read.amount.normalize():f} {read.currency}".strip() if read.amount else "—"
        site = settings.C4R_PUBLIC_URL.rstrip("/")
        url = f"{site}/c/{context.company.public_id}/finance?tab=drafts"
        self.say(
            "receipt_made",
            date=read.spent_on.isoformat() if read.spent_on else "—",
            amount=amount,
            seller=read.seller or "—",
            purpose=read.purpose or "—",
            url=url,
        )
        self.menu()


def _unbound(update_id: int, sender: dict[str, Any], chat_id: int, message: dict[str, Any]) -> None:
    """Somebody the bot does not know: only the link of the profile and the number are taken."""
    lang = language(sender.get("language_code"))
    telegram_user_id = int(sender["id"])
    text = str(message.get("text") or "")
    if text.startswith("/start ") and links.open_code(
        text.split(maxsplit=1)[1].strip(), telegram_user_id
    ):
        keyboard = {
            "keyboard": [[{"text": t(lang, "share_button"), "request_contact": True}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }
        return api.send(chat_id, t(lang, "share_ask"), keyboard)
    contact = message.get("contact")
    code = links.pending(telegram_user_id)
    if contact is None or code is None:
        key = "code_wrong" if text.startswith("/start ") else "not_linked"
        return api.send(chat_id, t(lang, key))
    if contact.get("user_id") != telegram_user_id:
        return api.send(chat_id, t(lang, "share_other"))

    def handler(ctx: CommandContext) -> CommandResult:
        link = links.bind(
            ctx,
            code.id,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=str(sender.get("username") or ""),
            phone=str(contact.get("phone_number") or ""),
        )
        return CommandResult(200, {"id": str(link.id)})

    try:
        run_command(
            command="telegram.link",
            actor=Actor(user_id=code.user_id),
            idempotency_key=f"tg-{update_id}-link",
            payload={"telegram_user_id": telegram_user_id},
            handler=handler,
        )
    except errors.ApiError as exc:
        key = "taken" if exc.code == "TELEGRAM_TAKEN" else "code_wrong"
        return api.send(chat_id, t(lang, key))
    link = links.link_of_telegram(telegram_user_id)
    if link is None:
        return None
    talk = Talk(link, update_id, sender.get("language_code"))
    talk.say("linked", {"remove_keyboard": True}, phone=link.phone)
    talk.menu()


def handle(update: dict[str, Any]) -> None:
    update_id = int(update.get("update_id") or 0)
    query = update.get("callback_query")
    message = (query or {}).get("message") or update.get("message") or {}
    sender = (query or update.get("message") or {}).get("from") or {}
    chat = message.get("chat") or {}
    # The bot talks with a person, not in groups.
    if chat.get("type") != "private" or "id" not in sender:
        return
    if query is not None:
        try:
            api.call("answerCallbackQuery", callback_query_id=query["id"])
        except Exception:  # noqa: BLE001 -- the button only stops blinking
            logger.warning("Telegram did not take the answer to a button")
        _clear(int(chat["id"]), message.get("message_id"))
    link = links.link_of_telegram(int(sender["id"]))
    text = str(message.get("text") or "") if query is None else ""
    # A new link of the profile opened by a bound account binds anew.
    relinking = text.startswith("/start ") or (
        query is None and "contact" in message and links.pending(int(sender["id"])) is not None
    )
    if link is None or link.user.state != UserAccount.State.ACTIVE or relinking:
        if query is None:
            _unbound(update_id, sender, int(chat["id"]), message)
        return
    if link.chat_id != chat["id"]:
        link.chat_id = int(chat["id"])
        link.save(update_fields=["chat_id"])
    talk = Talk(link, update_id, sender.get("language_code"))
    if query is not None:
        data = str(query.get("data") or "")
        if data.startswith("co:"):
            talk.choose(data[3:])
        elif data == "cos":
            talk.other_company()
        elif data == "veh":
            talk.vehicle()
        elif data == "inv":
            talk.invoice()
        elif data == "exp":
            talk.expense()
        elif data.startswith("wh:"):
            talk.invoice(data[3:])
        elif data == "ok":
            talk.confirmed()
        elif data == "no":
            talk.cancel()
        elif data.startswith("rel:"):
            talk.whose(data[4:])
        elif data.startswith("loc:"):
            talk.where(data[4:])
        return
    if message.get("photo") or message.get("document"):
        return talk.photo(message)
    talk.menu()
