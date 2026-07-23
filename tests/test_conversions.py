"""Tests for Conversions API PII hashing."""

import hashlib

from pinloop.api.conversions import normalize_user_data


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def test_email_is_lowercased_and_hashed_into_array():
    out = normalize_user_data({"em": "User@Example.com"})
    assert out["em"] == [_sha("user@example.com")]


def test_already_hashed_value_passthrough():
    already = _sha("user@example.com")
    out = normalize_user_data({"em": already})
    assert out["em"] == [already]


def test_phone_strips_non_digits_before_hashing():
    out = normalize_user_data({"ph": "+1 (555) 123-4567"})
    assert out["ph"] == [_sha("15551234567")]


def test_non_pii_fields_are_untouched():
    out = normalize_user_data(
        {"client_ip_address": "10.0.0.1", "client_user_agent": "Mozilla/5.0"}
    )
    assert out["client_ip_address"] == "10.0.0.1"
    assert out["client_user_agent"] == "Mozilla/5.0"


def test_list_of_emails_each_hashed():
    out = normalize_user_data({"em": ["a@x.com", "b@x.com"]})
    assert out["em"] == [_sha("a@x.com"), _sha("b@x.com")]
