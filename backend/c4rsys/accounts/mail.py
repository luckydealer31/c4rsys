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
