"""The talk with the bot (customer's decisions of 22.11.2026). The first part: binding the
Telegram account by the link of the profile and the shared number, the choice of the company, and
a vehicle made by a photo of its paper — the bot shows what was read and makes the vehicle after
«Confirm» in the chat.

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
from c4rsys.companies.models import Employment
from c4rsys.files import checks
from c4rsys.files import services as files
from c4rsys.files.models import StoredFile
from c4rsys.fleet import characteristics, owners, recognition
from c4rsys.fleet import services as fleet
from c4rsys.fleet.catalog.match import in_text as catalog_match
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.telegram import api, links
from c4rsys.telegram.models import TelegramLink
from c4rsys.telegram.texts import FIELDS, field, language, t

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
        rows = [[(t(self.lang, "menu_vehicle"), "veh")]]
        if len(contexts) > 1:
            rows.append([(t(self.lang, "menu_company"), "cos")])
        self.say("menu", _buttons(*rows), company=current.company.display_name)

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
        _save(self.link, {"step": "photo"})
        self.say("send_photo")

    def photo(self, message: dict[str, Any]) -> None:
        if self.link.state.get("step") != "photo":
            return self.menu()
        if message.get("photo"):
            source = message["photo"][-1]
        else:
            source = message["document"]
        if int(source.get("file_size") or 0) > files.MAX_DOCUMENT_BYTES:
            return self.say("file_wrong")
        self.say("reading")
        content = api.download(str(source["file_id"]))
        try:
            content_type = files.document_type(content)
        except errors.ApiError:
            return self.say("file_wrong")
        if checks.inspect(content_type, content):
            return self.say("file_wrong")
        user_id = self.link.user_id

        def keep(ctx: CommandContext) -> CommandResult:
            item = files.store(
                purpose=StoredFile.Purpose.VEHICLE_PAPER,
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

        run_command(
            command="file.upload",
            actor=Actor(user_id=user_id),
            idempotency_key=self.key("file"),
            payload={"sha256": hashlib.sha256(content).hexdigest()},
            handler=keep,
        )
        try:
            paper = recognition.read(content, content_type)
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
        api.send(
            self.link.chat_id,
            text,
            _buttons([(t(self.lang, "confirm"), "ok"), (t(self.lang, "cancel"), "no")]),
        )

    def confirmed(self) -> None:
        state = self.link.state
        if state.get("step") != "confirm":
            return self.say("start_over")
        _save(self.link, {**state, "step": "whose"})
        row = [(t(self.lang, f"rel_{relation}"), f"rel:{relation}") for relation in RELATIONS]
        self.say("whose", _buttons(row))

    def cancel(self) -> None:
        _save(self.link, {})
        self.say("cancelled")
        self.menu()

    def whose(self, relation: str) -> None:
        state = self.link.state
        acting = self._acting()
        if state.get("step") != "whose" or relation not in RELATIONS or acting is None:
            return self.say("start_over")
        context, actor = acting
        if relation == VehicleCard.Relation.CLIENT:
            return self.create(relation, None)
        places = _places(context, actor)
        if not places:
            return self.say("no_location")
        if len(places) == 1:
            return self.create(relation, UUID(places[0][0]))
        _save(self.link, {**state, "step": "where", "relation": relation})
        rows = [[(name, f"loc:{place_id}")] for place_id, name in places]
        self.say("where", _buttons(*rows))

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
