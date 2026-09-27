"""Tests for WebhookNotifier, EmailNotifier, and dispatch_notifications.

No real network or SMTP — httpx.post and smtplib.SMTP are patched.
"""
from __future__ import annotations

import logging
from email import message_from_string
from unittest.mock import MagicMock, patch

from akad.models.result import ClauseResult, ClauseStatus, OverallStatus
from akad.notifier import dispatch_notifications
from akad.notifiers.email_notifier import EmailNotifier, _collect_recipients
from akad.notifiers.webhook_notifier import WebhookNotifier
from tests.conftest import RecordingNotifier, make_contract, make_validation_result


def _breach_result():
    return make_validation_result(
        status=OverallStatus.BREACH,
        clauses=[ClauseResult(
            clause_type="schema.allowed_values",
            clause_target="currency_code",
            status=ClauseStatus.FAIL,
            expected="MYR/USD/SGD",
            observed="JPY",
            message="Contains values not in allowed list: ['JPY']",
        )],
    )


def _error_result():
    result = make_validation_result(status=OverallStatus.ERROR)
    result.row_count = None
    result.error_message = "Failed to read dataset: file not found"
    return result


WEBHOOK_CFG = {"webhook": {"url": "https://hooks.example.com/akad",
                           "headers": {"X-Token": "abc"}}}
EMAIL_CFG = {"email": {"smtp_host": "smtp.example.com",
                       "smtp_port": 587,
                       "smtp_user": "alerts@example.com",
                       "smtp_password_env": "SMTP_PW",
                       "recipients": ["data-team@example.com"]}}


class TestWebhookNotifier:
    def test_posts_breach_payload(self):
        contract = make_contract(notifications=WEBHOOK_CFG)
        with patch("akad.notifiers.webhook_notifier.httpx.post") as post:
            WebhookNotifier().notify(contract, _breach_result())

        post.assert_called_once()
        args, kwargs = post.call_args
        assert args[0] == "https://hooks.example.com/akad"
        assert kwargs["headers"] == {"X-Token": "abc"}
        payload = kwargs["json"]
        assert payload["event"] == "DATA_CONTRACT_BREACH"
        assert payload["contract_name"] == "test_contract"
        assert payload["on_breach"] == "warn"
        assert payload["failed_clauses"][0]["clause_target"] == "currency_code"

    def test_skips_when_no_webhook_configured(self):
        contract = make_contract()  # no notifications block at all
        with patch("akad.notifiers.webhook_notifier.httpx.post") as post:
            WebhookNotifier().notify(contract, _breach_result())
        post.assert_not_called()

    def test_skips_when_only_email_configured(self):
        contract = make_contract(notifications=EMAIL_CFG)
        with patch("akad.notifiers.webhook_notifier.httpx.post") as post:
            WebhookNotifier().notify(contract, _breach_result())
        post.assert_not_called()

    def test_http_error_is_swallowed_and_logged(self, caplog):
        contract = make_contract(notifications=WEBHOOK_CFG)
        with caplog.at_level(logging.WARNING), patch("akad.notifiers.webhook_notifier.httpx.post",
                   side_effect=ConnectionError("network down")):
            WebhookNotifier().notify(contract, _breach_result())  # must not raise
        assert "Webhook notification failed" in caplog.text

    def test_non_2xx_response_is_swallowed_and_logged(self, caplog):
        contract = make_contract(notifications=WEBHOOK_CFG)
        bad_resp = MagicMock()
        bad_resp.raise_for_status.side_effect = Exception("500 Server Error")
        with caplog.at_level(logging.WARNING), \
             patch("akad.notifiers.webhook_notifier.httpx.post", return_value=bad_resp):
            WebhookNotifier().notify(contract, _breach_result())
        assert "Webhook notification failed" in caplog.text


class TestErrorNotifications:
    """An ERROR (contract couldn't be evaluated) must not be reported as a BREACH."""

    def test_webhook_labels_error_and_includes_reason(self):
        contract = make_contract(notifications=WEBHOOK_CFG)
        with patch("akad.notifiers.webhook_notifier.httpx.post") as post:
            WebhookNotifier().notify(contract, _error_result())

        payload = post.call_args.kwargs["json"]
        assert payload["event"] == "DATA_CONTRACT_ERROR"
        assert payload["overall_status"] == "ERROR"
        assert payload["error_message"] == "Failed to read dataset: file not found"
        assert payload["failed_clauses"] == []

    def test_webhook_breach_clauses_carry_status(self):
        contract = make_contract(notifications=WEBHOOK_CFG)
        with patch("akad.notifiers.webhook_notifier.httpx.post") as post:
            WebhookNotifier().notify(contract, _breach_result())
        assert post.call_args.kwargs["json"]["failed_clauses"][0]["status"] == "FAIL"

    def test_email_subject_and_body_say_error(self, monkeypatch):
        monkeypatch.setenv("SMTP_PW", "secret")
        contract = make_contract(notifications=EMAIL_CFG)
        with patch("akad.notifiers.email_notifier.smtplib.SMTP") as smtp_cls:
            EmailNotifier().notify(contract, _error_result())

        raw_message = smtp_cls.return_value.__enter__.return_value.sendmail.call_args[0][2]
        msg = message_from_string(raw_message)
        body = msg.get_payload(decode=True).decode("utf-8")
        assert msg["Subject"] == "[Akad ERROR] test_contract v1.0.0"
        assert "Failed to read dataset: file not found" in body
        assert "Failed Clauses" not in body


class TestCollectRecipients:
    def test_includes_owner_consumers_and_configured_recipients(self):
        contract = make_contract(
            notifications=EMAIL_CFG,
            consumers=[{"team": "Analytics", "email": "analytics@example.com"}],
        )
        recipients = _collect_recipients(contract)
        assert recipients == [
            "test@example.com",            # owner (from make_contract)
            "analytics@example.com",       # consumer
            "data-team@example.com",       # notifications.email.recipients
        ]

    def test_deduplicates_preserving_order(self):
        cfg = {"email": {**EMAIL_CFG["email"], "recipients": ["test@example.com"]}}
        contract = make_contract(
            notifications=cfg,
            consumers=[{"team": "Analytics", "email": "test@example.com"}],
        )
        assert _collect_recipients(contract) == ["test@example.com"]


class TestEmailNotifier:
    def test_sends_email_via_smtp(self, monkeypatch):
        monkeypatch.setenv("SMTP_PW", "secret")
        contract = make_contract(notifications=EMAIL_CFG)

        with patch("akad.notifiers.email_notifier.smtplib.SMTP") as smtp_cls:
            srv = smtp_cls.return_value.__enter__.return_value
            EmailNotifier().notify(contract, _breach_result())

        smtp_cls.assert_called_once_with("smtp.example.com", 587)
        srv.starttls.assert_called_once()
        srv.login.assert_called_once_with("alerts@example.com", "secret")
        srv.sendmail.assert_called_once()
        sender, recipients, raw_message = srv.sendmail.call_args[0]
        assert sender == "alerts@example.com"
        assert "test@example.com" in recipients
        assert "data-team@example.com" in recipients

        msg = message_from_string(raw_message)
        assert msg["Subject"] == "[Akad BREACH] test_contract v1.0.0"
        body = msg.get_payload(decode=True).decode("utf-8")
        assert "currency_code" in body
        assert "Contains values not in allowed list" in body

    def test_skips_when_no_email_configured(self):
        contract = make_contract(notifications=WEBHOOK_CFG)
        with patch("akad.notifiers.email_notifier.smtplib.SMTP") as smtp_cls:
            EmailNotifier().notify(contract, _breach_result())
        smtp_cls.assert_not_called()

    def test_smtp_failure_is_swallowed_and_logged(self, caplog):
        contract = make_contract(notifications=EMAIL_CFG)
        with caplog.at_level(logging.WARNING), patch("akad.notifiers.email_notifier.smtplib.SMTP",
                   side_effect=OSError("connection refused")):
            EmailNotifier().notify(contract, _breach_result())  # must not raise
        assert "Email notification failed" in caplog.text


class TestDispatchNotifications:
    def test_calls_every_notifier_in_list(self):
        contract = make_contract()
        result = _breach_result()
        first, second = RecordingNotifier(), RecordingNotifier()

        dispatch_notifications(contract, result, notifiers=[first, second])

        assert first.calls == [(contract, result)]
        assert second.calls == [(contract, result)]

    def test_empty_list_disables_all_notifications(self):
        dispatch_notifications(make_contract(), _breach_result(), notifiers=[])
        # nothing to assert beyond "no exception, nothing sent"

    def test_none_uses_default_notifiers(self):
        # No notification config on the contract → both defaults no-op safely
        with patch("akad.notifiers.webhook_notifier.httpx.post") as post, \
             patch("akad.notifiers.email_notifier.smtplib.SMTP") as smtp_cls:
            dispatch_notifications(make_contract(), _breach_result(), notifiers=None)
        post.assert_not_called()
        smtp_cls.assert_not_called()
