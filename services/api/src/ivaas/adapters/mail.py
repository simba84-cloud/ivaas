"""Sending email through an SMTP relay (the standard library's smtplib, in a thread).

The relay this was built for (Liquid's) accepts mail from allowlisted addresses with
no login, so there is no password here to keep or leak. STARTTLS is used whenever
the relay offers it, and can be required.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from ivaas.ports.mail import MailResult, OutgoingMail

log = logging.getLogger(__name__)


class SmtpMailSender:
    configured = True

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        *,
        starttls: str = "opportunistic",
        timeout_s: float = 15.0,
        smtp_factory: type[smtplib.SMTP] = smtplib.SMTP,
    ) -> None:
        self.host, self.port, self.sender = host, port, sender
        self._starttls, self._timeout = starttls, timeout_s
        self._smtp = smtp_factory

    def message(self, mail: OutgoingMail) -> EmailMessage:
        msg = EmailMessage()  # the default policy refuses a header with a line break
        msg["From"] = self.sender
        msg["To"] = ", ".join(mail.to)
        msg["Subject"] = mail.subject
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=self.sender.rpartition("@")[2] or None)
        msg.set_content(mail.text)
        if mail.html:
            msg.add_alternative(mail.html, subtype="html")
        return msg

    def _send_now(self, mail: OutgoingMail) -> MailResult:
        msg = self.message(mail)
        with self._smtp(self.host, self.port, timeout=self._timeout) as smtp:
            smtp.ehlo()
            if self._starttls != "off":
                if smtp.has_extn("starttls"):
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                elif self._starttls == "required":
                    return MailResult(False, f"{self.host} does not offer STARTTLS; not sent")
            refused = smtp.send_message(msg)
        if refused:
            return MailResult(True, f"the relay refused {len(refused)} recipient(s)")
        return MailResult(True)

    async def send(self, mail: OutgoingMail) -> MailResult:
        try:
            return await asyncio.to_thread(self._send_now, mail)
        except smtplib.SMTPRecipientsRefused as exc:
            return MailResult(False, f"the relay refused every recipient ({len(exc.recipients)})")
        except smtplib.SMTPResponseException as exc:
            detail = exc.smtp_error.decode(errors="replace") if exc.smtp_error else ""
            return MailResult(False, f"{self.host} answered {exc.smtp_code}: {detail}"[:300])
        except (smtplib.SMTPException, OSError, ValueError) as exc:
            return MailResult(False, f"{type(exc).__name__}: {exc}"[:300])


class DisabledMailSender:
    """No relay configured: nothing is queued, and a test says why."""

    configured = False
    host, port, sender = "", 0, ""

    async def send(self, mail: OutgoingMail) -> MailResult:
        return MailResult(False, "email is not configured: IVAAS_SMTP_HOST is empty")
