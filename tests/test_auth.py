"""Tests for the OAuth refresh flow, rotation persistence, and dead-token handling."""

import pytest
import responses

from pinloop import auth
from pinloop.auth import PinterestAuthError, get_access_token
from pinloop.config import AdsConfig, PinloopConfig, SafetyConfig

TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(auth, "_cached_tokens", {})


def _cfg(**ads):
    base = dict(app_id="1234567", app_secret="secret", refresh_token_seed="pinr.seed")
    base.update(ads)
    return PinloopConfig(ads=AdsConfig(**base), safety=SafetyConfig())


@responses.activate
def test_refresh_success_caches_access_token():
    responses.add(
        responses.POST, TOKEN_URL,
        json={"access_token": "pina_new", "expires_in": 2592000, "token_type": "bearer"},
        status=200,
    )
    cfg = _cfg()
    assert get_access_token(cfg) == "pina_new"
    # Sends HTTP Basic auth for app_id:app_secret.
    assert responses.calls[0].request.headers["Authorization"].startswith("Basic ")


@responses.activate
def test_no_rotation_reuses_seed_refresh_token():
    responses.add(
        responses.POST, TOKEN_URL,
        json={"access_token": "pina_new", "expires_in": 2592000},
        status=200,
    )
    cfg = _cfg()
    get_access_token(cfg)
    assert auth._cached_tokens["default"].refresh_token == "pinr.seed"


@responses.activate
def test_rotation_persists_new_refresh_token(tmp_path):
    token_file = tmp_path / "rt.txt"
    responses.add(
        responses.POST, TOKEN_URL,
        json={"access_token": "pina_new", "refresh_token": "pinr.rotated", "expires_in": 2592000},
        status=200,
    )
    cfg = _cfg(token_path=str(token_file))
    get_access_token(cfg)
    assert auth._cached_tokens["default"].refresh_token == "pinr.rotated"
    assert token_file.read_text() == "pinr.rotated"


@responses.activate
def test_expired_refresh_token_raises_auth_error():
    responses.add(
        responses.POST, TOKEN_URL,
        json={"code": 28, "message": "Refresh token is expired."},
        status=401,
    )
    with pytest.raises(PinterestAuthError):
        get_access_token(_cfg())
