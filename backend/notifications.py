import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from backend import config


class EmailError(Exception):
    """Raised with a safe, user-presentable message (never contains credentials)."""


def send_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    if not config.email_configured():
        raise EmailError("Email is not configured on the server")

    msg = EmailMessage()
    msg["From"] = config.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid()
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    try:
        if config.SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=15, context=context)
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
        with server:
            if not config.SMTP_USE_SSL:
                server.starttls(context=context)
            if config.SMTP_USERNAME:
                server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise EmailError("SMTP login failed. Check SMTP_USERNAME and SMTP_PASSWORD") from None
    except (smtplib.SMTPException, OSError) as e:
        raise EmailError(f"Could not send email ({type(e).__name__})") from None