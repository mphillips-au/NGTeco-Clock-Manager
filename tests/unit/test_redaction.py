"""Redaction tests. SECURITY.md forbids emitting credentials anywhere."""

from __future__ import annotations

import pytest

from clockmanager.security.redaction import (
    REDACTED,
    is_sensitive_key,
    mask_secret,
    redact_bytes,
    redact_mapping,
    redact_text,
)


@pytest.mark.parametrize(
    "key",
    [
        "pin",
        "PIN",
        "user_pin",
        "password",
        "passwd",
        "card_number",
        "cardNumber".lower(),
        "credential_region",
        "fingerprint_template",
        "face_template",
        "biometric_data",
        "comm_key",
        "api-key",
        "auth_token",
        "app_secret",
    ],
)
def test_sensitive_keys_detected(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["user_id", "uid", "first_name", "device_name", "punch", "status"])
def test_ordinary_keys_are_not_sensitive(key: str) -> None:
    assert not is_sensitive_key(key)


def test_redact_mapping_replaces_sensitive_values() -> None:
    payload = {
        "user_id": "1001",
        "pin": "4821",
        "card_number": "0012345678",
        "nested": {"password": "hunter2", "first_name": "Ada"},
    }
    result = redact_mapping(payload)
    assert result["user_id"] == "1001"
    assert result["pin"] == REDACTED
    assert result["card_number"] == REDACTED
    assert result["nested"]["password"] == REDACTED
    assert result["nested"]["first_name"] == "Ada"
    assert "4821" not in str(result)
    assert "hunter2" not in str(result)


def test_redact_mapping_handles_sequences() -> None:
    result = redact_mapping({"users": [{"pin": "1234"}, {"user_id": "1002"}]})
    assert "1234" not in str(result)
    assert result["users"][1]["user_id"] == "1002"


def test_redact_mapping_is_depth_bounded() -> None:
    payload: dict[str, object] = {"pin": "1234"}
    for _ in range(50):
        payload = {"nested": payload}
    assert "1234" not in str(redact_mapping(payload))


def test_redact_text_masks_inline_pairs() -> None:
    assert "4821" not in redact_text("connecting with pin=4821 for user 1001")
    assert "hunter2" not in redact_text('{"password": "hunter2"}')
    assert "1001" in redact_text("connecting with pin=4821 for user 1001")


def test_redact_bytes_never_shows_content() -> None:
    result = redact_bytes(b"\x00\x01secret-template")
    assert "secret" not in result
    assert "17 bytes" in result


@pytest.mark.parametrize(
    ("value", "keep", "expected"),
    [
        ("4821", 0, "****"),
        ("482", 1, "***"),
        ("", 0, ""),
        (None, 0, ""),
    ],
)
def test_mask_secret(value: str | None, keep: int, expected: str) -> None:
    assert mask_secret(value, keep=keep) == expected


def test_mask_secret_keeps_at_most_a_third() -> None:
    masked = mask_secret("123456789", keep=8)
    assert masked == "******789"
    assert len(masked) == 9
