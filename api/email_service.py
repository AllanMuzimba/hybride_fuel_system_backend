"""SMTP delivery for account recovery emails."""
from email.message import EmailMessage
from urllib.parse import urlencode
import os
import smtplib
import ssl


def configured() -> bool:
    return all(os.getenv(name) for name in (
        "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "PASSWORD_RESET_URL",
    ))


def send(to_address: str, full_name: str, token: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.getenv("SMTP_FROM_EMAIL", username)
    reset_base = os.environ["PASSWORD_RESET_URL"]
    separator = "&" if "?" in reset_base else "?"
    reset_url = f"{reset_base}{separator}{urlencode({'reset_token': token})}"

    message = EmailMessage()
    message["Subject"] = "Reset your Hybrid Fuel password"
    message["From"] = sender
    message["To"] = to_address
    message.set_content(
        f"Hello {full_name},\n\n"
        "We received a request to reset your Hybrid Fuel password. "
        "Use this link within 30 minutes to choose a new password:\n\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can ignore this email."
    )

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls(context=context)
            smtp.login(username, password)
            smtp.send_message(message)
