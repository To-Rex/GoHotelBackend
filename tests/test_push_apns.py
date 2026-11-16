"""iOS (APNs) push tarkibi: yangi vazifa budilnigi va oddiy xabarlar."""
from firebase_admin import _messaging_encoder, messaging

from app.infrastructure.push import firebase


def _encoded_aps(kind: str | None) -> dict:
    message = messaging.Message(
        token="test-token",
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(aps=firebase._aps("Yangi vazifa", "101-xona", kind))
        ),
    )
    encoded = _messaging_encoder.MessageEncoder().default(message)
    return encoded["apns"]["payload"]["aps"]


def test_task_alarm_uses_long_sound_and_time_sensitive_level():
    aps = _encoded_aps("task_alarm")
    assert aps["sound"] == firebase.IOS_TASK_ALARM_SOUND == "task_alarm.caf"
    assert aps["interruption-level"] == "time-sensitive"
    assert aps["alert"] == {"title": "Yangi vazifa", "body": "101-xona"}
    assert aps["content-available"] == 1


def test_regular_notification_keeps_default_sound():
    aps = _encoded_aps(None)
    assert aps["sound"] == "default"
    assert "interruption-level" not in aps
    assert aps["alert"] == {"title": "Yangi vazifa", "body": "101-xona"}
    assert aps["content-available"] == 1
