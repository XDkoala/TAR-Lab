from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest

from tar_lab.orchestration import notify
from tar_lab.orchestration.notify import (
    EmailNotifier,
    FanoutNotifier,
    NotificationError,
    SmtpSettings,
)


def test_settings_from_file_with_env_override(tmp_path: Path) -> None:
    env = tmp_path / "email.env"
    env.write_text("# 163 mail\nTARLAB_SMTP_USER=me@163.com\nTARLAB_SMTP_PASSWORD='code'\n")
    s = SmtpSettings.load(env, {"TARLAB_SMTP_TO": "other@163.com"})
    assert s == SmtpSettings("smtp.163.com", 465, "me@163.com", "code", "other@163.com")
    assert SmtpSettings.load(tmp_path / "missing.env", {}) is None


class FakeSMTP:
    sent: ClassVar[list[EmailMessage]] = []

    def __init__(self, host: str, port: int, **_: Any) -> None:
        self.host = host

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        if password != "code":
            raise notify.smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, msg: EmailMessage) -> None:
        FakeSMTP.sent.append(msg)


def test_email_notifier_sends_and_fanout_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)
    good = EmailNotifier(SmtpSettings("smtp.163.com", 465, "me@163.com", "code", "me@163.com"))
    good.notify("Approval needed", "body")
    assert FakeSMTP.sent[-1]["Subject"] == "[TAR-Lab] Approval needed"
    bad = EmailNotifier(SmtpSettings("smtp.163.com", 465, "me@163.com", "wrong", "me@163.com"))
    with pytest.raises(NotificationError, match="EmailNotifier"):
        FanoutNotifier([good, bad]).notify("x", "y")
    assert len(FakeSMTP.sent) == 2  # the good channel still delivered
