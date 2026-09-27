from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText

from akad.models.contract import DataContract
from akad.models.result import ValidationResult
from akad.notifiers.base import Notifier

log = logging.getLogger(__name__)


def _collect_recipients(contract: DataContract) -> list[str]:
    recipients = [contract.metadata.owner.email, *(c.email for c in contract.consumers)]
    if contract.notifications and contract.notifications.email:
        recipients.extend(contract.notifications.email.recipients)
    return list(dict.fromkeys(recipients))  # deduplicate, preserve order


def _build_email_body(result: ValidationResult) -> str:
    lines = [
        f"Akad {result.overall_status.value.title()} Alert",
        "",
        f"Contract : {result.contract_name} v{result.contract_version}",
        f"Dataset  : {result.dataset_location}",
        f"Time     : {result.validated_at.isoformat()}",
        f"Rows     : {result.row_count}",
    ]
    if result.error_message:
        lines += ["", f"Error    : {result.error_message}"]
    for heading, clauses in (("Failed", result.failed_clauses), ("Errored", result.errored_clauses)):
        if clauses:
            lines += ["", f"{heading} Clauses ({len(clauses)}):"]
            for c in clauses:
                target = f" [{c.clause_target}]" if c.clause_target else ""
                lines.append(f"  • {c.clause_type}{target}: {c.message}")
    return "\n".join(lines)


class EmailNotifier(Notifier):
    def notify(self, contract: DataContract, result: ValidationResult) -> None:
        if not (contract.notifications and contract.notifications.email):
            return
        cfg = contract.notifications.email
        recipients = _collect_recipients(contract)
        if not recipients:
            return
        body = _build_email_body(result)
        try:
            msg = MIMEText(body)
            msg["Subject"] = f"[Akad {result.overall_status.value}] {contract.metadata.name} v{contract.metadata.version}"
            msg["From"]    = cfg.smtp_user
            msg["To"]      = ", ".join(recipients)
            password = os.environ.get(cfg.smtp_password_env, "")
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as srv:
                srv.starttls()
                srv.login(cfg.smtp_user, password)
                srv.sendmail(cfg.smtp_user, recipients, msg.as_string())
        except Exception as exc:
            log.warning("Email notification failed: %s", exc)
