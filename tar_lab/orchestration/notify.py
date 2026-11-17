"""Notifications: console always, email when SMTP settings exist."""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

DEFAULT_EMAIL_ENV = Path.home() / ".config" / "tar-lab" / "email.env"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    user: str
    password: str
    to: str

    @classmethod
    def load(cls, path: Path | None = None,
             environ: Mapping[str, str] | None = None) -> SmtpSettings | None:
        """``TARLAB_SMTP_*`` from the environment override ``~/.config/tar-lab/email.env``."""
        env = {**read_env_file(path or Path(os.environ.get("TARLAB_EMAIL_ENV",
                                                           DEFAULT_EMAIL_ENV))),
               **{k: v for k, v in (environ or os.environ).items()
                  if k.startswith("TARLAB_SMTP_")}}
        user, password = env.get("TARLAB_SMTP_USER"), env.get("TARLAB_SMTP_PASSWORD")
        if not user or not password:
            return None
        return cls(env.get("TARLAB_SMTP_HOST", "smtp.163.com"),
                   int(env.get("TARLAB_SMTP_PORT", "465")), user, password,
                   env.get("TARLAB_SMTP_TO", user))


@dataclass
class ConsoleNotifier:
    def notify(self, subject: str, body: str) -> None:
        print(f"[TAR-Lab] {subject}\n{body}\n", file=sys.stderr)


@dataclass
class EmailNotifier:
    settings: SmtpSettings
    timeout: float = 30.0

    def notify(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[TAR-Lab] {subject}"
        msg["From"] = self.settings.user
        msg["To"] = self.settings.to
        msg.set_content(body)
        context = ssl.create_default_context()
        if self.settings.port == 465:
            with smtplib.SMTP_SSL(self.settings.host, self.settings.port, context=context,
                                  timeout=self.timeout) as smtp:
                smtp.login(self.settings.user, self.settings.password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(self.settings.host, self.settings.port,
                              timeout=self.timeout) as smtp:
                smtp.starttls(context=context)
                smtp.login(self.settings.user, self.settings.password)
                smtp.send_message(msg)


class Notifier(Protocol):
    def notify(self, subject: str, body: str) -> None: ...


@dataclass
class FanoutNotifier:
    """Try every channel; raise afterwards if any of them failed."""

    channels: Sequence[Notifier]

    def notify(self, subject: str, body: str) -> None:
        errors = []
        for channel in self.channels:
            try:
                channel.notify(subject, body)
            except (OSError, smtplib.SMTPException) as exc:
                errors.append(f"{type(channel).__name__}: {exc}")
        if errors:
            raise NotificationError("; ".join(errors))


class NotificationError(RuntimeError):
    pass


def default_notifier() -> Notifier:
    settings = SmtpSettings.load()
    channels: list[Notifier] = [ConsoleNotifier()]
    if settings is not None:
        channels.append(EmailNotifier(settings))
    return FanoutNotifier(channels)
