"""Outgoing messages with one-time codes and invitations.

"console" writes them to the backend log (development); "email" sends them through Django's
default mailer, on the test server Amazon SES through django-anymail (decision on T20, 23.10.2026).
Messages are sent only after the transaction commits. A failed delivery is logged without the
code and does not undo the committed flow: the user may request a new code.

The texts are a PROPOSAL (not confirmed): Russian and English in one message, because the
language of the person is not known before registration.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger("c4rsys.mail")

SUBJECT_CODE = "c4rsys: код подтверждения / verification code"
BODY_CODE = (
    "Ваш код: {code}\n"
    "Никому его не сообщайте. Если вы не запрашивали код, просто проигнорируйте письмо.\n\n"
    "Your code: {code}\n"
    "Do not share it. If you did not request a code, ignore this message.\n"
)
SUBJECT_INVITATION = "c4rsys: приглашение в компанию / company invitation"
BODY_INVITATION = (
    "Компания «{company}» приглашает вас в c4rsys. Войдите или зарегистрируйтесь с этим адресом:\n"
    "{url}\n\n"
    "The company «{company}» invites you to c4rsys. Sign in or register with this address:\n"
    "{url}\n"
)

SUBJECT_SIGNING = "c4rsys: документ на подпись / document to sign"
BODY_SIGNING = (
    "Компания «{company}» просит вас подписать документ «{title}»:\n"
    "{url}\n"
    "Ссылка персональная, не пересылайте её.\n\n"
    "The company «{company}» asks you to sign the document «{title}»:\n"
    "{url}\n"
    "The link is personal, do not forward it.\n"
)


SUBJECT_LINK = "c4rsys: запрос на связь / link request"
BODY_LINK = (
    "Компания «{company}» просит связать вашу карточку клиента с вашим профилем c4rsys. "
    "Войдите или зарегистрируйтесь с этим адресом, чтобы принять или отклонить запрос:\n"
    "{url}\n\n"
    "The company «{company}» asks to link your client card with your c4rsys profile. "
    "Sign in or register with this address to accept or decline:\n"
    "{url}\n"
)


SUBJECT_OWNER = "c4rsys: подтверждение владения ТС / vehicle ownership"
BODY_OWNER = (
    "Компания «{company}» указала вас владельцем транспортного средства и просит подтвердить "
    "связь с компанией, владение и согласие на управление. Войдите или зарегистрируйтесь с этим "
    "адресом:\n{url}\n\n"
    "The company «{company}» named you the owner of a vehicle and asks you to confirm the link, "
    "the ownership and the consent to its management. Sign in or register with this address:\n"
    "{url}\n"
)


def _deliver(email: str, subject: str, body: str, kind: str) -> None:
    try:
        send_mail(subject, body, None, [email])
    except Exception:
        # The address is personal data and the body may hold a code: neither goes to the log.
        logger.exception("Mail delivery failed: %s", kind)


def send_code(email: str, code: str, purpose: str) -> None:
    if settings.C4R_MAIL_MODE == "console":
        logger.warning("DEV MAIL to %s: %s code %s", email, purpose, code)
        return
    _deliver(email, SUBJECT_CODE, BODY_CODE.format(code=code), f"code {purpose}")


def send_invitation(email: str, company_name: str) -> None:
    """Notice of an employee invitation with the link to register or sign in (§ 5.1.3)."""
    if settings.C4R_MAIL_MODE == "console":
        logger.warning("DEV MAIL to %s: invitation from %s", email, company_name)
        return
    body = BODY_INVITATION.format(company=company_name, url=settings.C4R_PUBLIC_URL)
    _deliver(email, SUBJECT_INVITATION, body, "invitation")


def send_link_request(email: str, company_name: str) -> None:
    """Notice of a request to link a card with the profile, or to register first (§ 6.2.5–6.2.6,
    § 20.3.4)."""
    if settings.C4R_MAIL_MODE == "console":
        logger.warning("DEV MAIL to %s: link request from %s", email, company_name)
        return
    body = BODY_LINK.format(company=company_name, url=settings.C4R_CABINET_URL)
    _deliver(email, SUBJECT_LINK, body, "link request")


def send_owner_request(email: str, company_name: str) -> None:
    """Notice of a request to the owner of a vehicle (§ 8.3.1, § 20.3.4)."""
    if settings.C4R_MAIL_MODE == "console":
        logger.warning("DEV MAIL to %s: owner request from %s", email, company_name)
        return
    body = BODY_OWNER.format(company=company_name, url=settings.C4R_CABINET_URL)
    _deliver(email, SUBJECT_OWNER, body, "owner request")


def send_signing_link(email: str, company_name: str, title: str, url: str) -> None:
    """A personal link to sign a document (§ 18.3.8; decision Р-4: email only)."""
    if settings.C4R_MAIL_MODE == "console":
        logger.warning("DEV MAIL to %s: signing link %s", email, url)
        return
    body = BODY_SIGNING.format(company=company_name, title=title, url=url)
    _deliver(email, SUBJECT_SIGNING, body, "signing link")


SUBJECT_BLOCK = "c4rsys: блокировка / block"
BODY_BLOCK = (
    "{what_ru} заблокирован администрацией c4rsys.\n"
    "Причина: {reason}\n"
    "Обращение в поддержку: {support}\n\n"
    "{what_en} has been blocked by the c4rsys administration.\n"
    "Reason: {reason}\n"
    "Contact support: {support}\n"
)
# PROPOSAL: the texts of the categories of § 24.2.2 when no reason to disclose is written.
BLOCK_CATEGORY_TEXT = {
    "violation": "нарушение правил / violation of the rules",
    "security_threat": "угроза безопасности / security threat",
}


def send_block_notice(email: str, *, company_name: str | None, reason: str, support: str) -> None:
    """The notice of a system block with the reason that may be disclosed and the way to reach
    the support (§ 24.2.2)."""
    if settings.C4R_MAIL_MODE == "console":
        logger.warning("DEV MAIL to %s: block notice (%s)", email, company_name or "account")
        return
    body = BODY_BLOCK.format(
        what_ru=f"Компания «{company_name}»" if company_name else "Ваш аккаунт",
        what_en=f"The company «{company_name}»" if company_name else "Your account",
        reason=BLOCK_CATEGORY_TEXT.get(reason, reason),
        support=support or settings.C4R_PUBLIC_URL,
    )
    _deliver(email, SUBJECT_BLOCK, body, "block notice")


# PROPOSAL: the texts of the recovery through the support (§ 2.3.1).
SUBJECT_RECOVERY = "c4rsys: восстановление доступа / access recovery"
BODY_RECOVERY_LINK = (
    "Доступ к вашему аккаунту c4rsys восстановлен поддержкой. Установите новый облачный пароль "
    "по персональной ссылке:\n{url}\n"
    "Все прежние сессии завершены. Не пересылайте ссылку.\n\n"
    "Access to your c4rsys account has been recovered by the support. Set a new cloud password "
    "with this personal link:\n{url}\n"
    "All previous sessions have ended. Do not forward the link.\n"
)
BODY_RECOVERY_NOTICE = (
    "Поддержка c4rsys одобрила восстановление доступа к аккаунту, связанному с этим адресом. "
    "Доступ будет восстановлен после {when} (UTC). Если вы не обращались в поддержку, срочно "
    "свяжитесь с ней: {support}\n\n"
    "The c4rsys support approved the recovery of access to the account linked with this address. "
    "Access will be recovered after {when} (UTC). If you did not contact the support, reach it at "
    "once: {support}\n"
)
BODY_RECOVERY_DONE = (
    "Доступ к аккаунту c4rsys, связанному с этим адресом, восстановлен поддержкой, все сессии "
    "завершены. Если вы не обращались в поддержку, срочно свяжитесь с ней: {support}\n\n"
    "Access to the c4rsys account linked with this address has been recovered by the support and "
    "all sessions have ended. If you did not contact the support, reach it at once: {support}\n"
)
BODY_RECOVERY_REFUSED = (
    "Поддержка c4rsys рассмотрела ваше обращение о восстановлении доступа № {number} и не может "
    "его удовлетворить.\nПричина: {reason}\nОбращение в поддержку: {support}\n\n"
    "The c4rsys support reviewed your access recovery request No. {number} and cannot grant it.\n"
    "Reason: {reason}\nContact support: {support}\n"
)


def send_recovery_link(email: str, url: str) -> None:
    """The personal link to set a new cloud password after the recovery (§ 2.3.1)."""
    if settings.C4R_MAIL_MODE == "console":
        logger.warning("DEV MAIL to %s: recovery link %s", email, url)
        return
    _deliver(email, SUBJECT_RECOVERY, BODY_RECOVERY_LINK.format(url=url), "recovery link")


def send_recovery_notice(email: str, *, done: bool, when: str, support: str) -> None:
    """The notice to a previous contact of the account (§ 2.3.1: «if available»)."""
    if settings.C4R_MAIL_MODE == "console":
        logger.warning("DEV MAIL to %s: recovery notice (done=%s)", email, done)
        return
    support = support or settings.C4R_PUBLIC_URL
    body = (
        BODY_RECOVERY_DONE.format(support=support)
        if done
        else BODY_RECOVERY_NOTICE.format(when=when, support=support)
    )
    _deliver(email, SUBJECT_RECOVERY, body, "recovery notice")


def send_recovery_refused(email: str, *, number: int, reason: str, support: str) -> None:
    """The refusal with its reason, without closed data of the account (§ 2.3.1)."""
    if settings.C4R_MAIL_MODE == "console":
        logger.warning("DEV MAIL to %s: recovery request %s refused", email, number)
        return
    body = BODY_RECOVERY_REFUSED.format(
        number=number, reason=reason, support=support or settings.C4R_PUBLIC_URL
    )
    _deliver(email, SUBJECT_RECOVERY, body, "recovery refused")
