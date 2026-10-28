"""Tests for multi-login (PINTEREST_PROFILES) token routing."""

import pytest
import responses

from pinloop import auth
from pinloop.api import client as client_mod
from pinloop.api.accounts import list_ad_accounts
from pinloop.api.client import api_request
from pinloop.config import AdsConfig, PinloopConfig, SafetyConfig, _parse_profiles

BASE = "https://api.pinterest.com/v5"
TOKEN_URL = f"{BASE}/oauth/token"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(auth, "_cached_tokens", {})
    monkeypatch.setattr(client_mod, "_account_profiles", {})


@pytest.fixture
def cfg():
    return PinloopConfig(
        ads=AdsConfig(
            app_id="1", app_secret="s",
            profiles={"superbolt": "rt_sb", "sgs": "rt_sgs"},
        ),
        safety=SafetyConfig(),
    )


def _mint_tokens():
    """Token endpoint returns an access token derived from the refresh token sent."""
    def cb(request):
        rt = request.body.split("refresh_token=")[1]
        return (200, {}, '{"access_token": "at_%s", "expires_in": 3600}' % rt)
    responses.add_callback(responses.POST, TOKEN_URL, callback=cb)


def _accounts(token, items):
    def cb(request):
        import json
        if request.headers["Authorization"] != f"Bearer {token}":
            return (403, {}, "{}")
        return (200, {}, json.dumps({"items": items, "bookmark": None}))
    responses.add_callback(responses.GET, f"{BASE}/ad_accounts", callback=cb)


def test_parse_profiles():
    assert _parse_profiles('{"a": " x ", "b": "y"}') == {"a": "x", "b": "y"}
    assert _parse_profiles("") == {}
    with pytest.raises(ValueError):
        _parse_profiles("[1]")
    with pytest.raises(ValueError):
        _parse_profiles("nope")


def test_legacy_single_token_is_default_profile():
    ads = AdsConfig(refresh_token_seed="rt")
    assert list(ads.profile_tokens()) == ["default"]


@responses.activate
def test_list_ad_accounts_merges_tags_and_first_profile_wins(cfg):
    _mint_tokens()
    responses.add(  # superbolt sees A and B
        responses.GET, f"{BASE}/ad_accounts", match=[
            responses.matchers.header_matcher({"Authorization": "Bearer at_rt_sb"})],
        json={"items": [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]},
    )
    responses.add(  # sgs sees B (duplicate) and C
        responses.GET, f"{BASE}/ad_accounts", match=[
            responses.matchers.header_matcher({"Authorization": "Bearer at_rt_sgs"})],
        json={"items": [{"id": "2", "name": "B"}, {"id": "3", "name": "C"}]},
    )
    out = list_ad_accounts(cfg)
    assert {(a["id"], a["profile"]) for a in out["ad_accounts"]} == {
        ("1", "superbolt"), ("2", "superbolt"), ("3", "sgs")}
    assert out["total_ad_accounts"] == 3


@responses.activate
def test_dead_profile_reported_not_fatal(cfg):
    responses.add(responses.POST, TOKEN_URL, json={"code": 28, "message": "Refresh token is expired."}, status=401,
                  match=[responses.matchers.urlencoded_params_matcher({"grant_type": "refresh_token", "refresh_token": "rt_sb"})])
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "at_sgs", "expires_in": 3600},
                  match=[responses.matchers.urlencoded_params_matcher({"grant_type": "refresh_token", "refresh_token": "rt_sgs"})])
    responses.add(responses.GET, f"{BASE}/ad_accounts", json={"items": [{"id": "3"}]})
    out = list_ad_accounts(cfg)
    assert "superbolt" in out["profile_errors"]
    assert out["ad_accounts"][0]["profile"] == "sgs"


@responses.activate
def test_account_paths_route_to_owning_profile(cfg):
    _mint_tokens()
    _accounts("at_rt_sb", [{"id": "1"}])
    _accounts("at_rt_sgs", [{"id": "3"}])
    responses.add(responses.GET, f"{BASE}/ad_accounts/3/campaigns", json={"items": []})
    list_ad_accounts(cfg)
    api_request(cfg, "GET", "/ad_accounts/3/campaigns")
    assert responses.calls[-1].request.headers["Authorization"] == "Bearer at_rt_sgs"


@responses.activate
def test_unknown_account_triggers_discovery_then_routes(cfg):
    _mint_tokens()
    _accounts("at_rt_sb", [{"id": "1"}])
    _accounts("at_rt_sgs", [{"id": "3"}])
    responses.add(responses.GET, f"{BASE}/ad_accounts/3/campaigns", json={"items": []})
    api_request(cfg, "GET", "/ad_accounts/3/campaigns")  # no prior list_ad_accounts
    assert responses.calls[-1].request.headers["Authorization"] == "Bearer at_rt_sgs"


@responses.activate
def test_403_rebuilds_stale_map_and_retries(cfg, monkeypatch):
    _mint_tokens()
    # Stale map says account 3 belongs to superbolt; really it's sgs now.
    monkeypatch.setattr(client_mod, "_account_profiles", {"3": "superbolt"})
    _accounts("at_rt_sb", [{"id": "1"}])
    _accounts("at_rt_sgs", [{"id": "3"}])

    def campaigns(request):
        ok = request.headers["Authorization"] == "Bearer at_rt_sgs"
        return (200, {}, '{"items": []}') if ok else (403, {}, '{"message": "no access"}')
    responses.add_callback(responses.GET, f"{BASE}/ad_accounts/3/campaigns", callback=campaigns)

    assert api_request(cfg, "GET", "/ad_accounts/3/campaigns") == {"items": []}
    assert client_mod._account_profiles["3"] == "sgs"


@responses.activate
def test_organic_paths_use_default_profile(cfg):
    _mint_tokens()
    responses.add(responses.GET, f"{BASE}/boards", json={"items": []})
    api_request(cfg, "GET", "/boards")
    assert responses.calls[-1].request.headers["Authorization"] == "Bearer at_rt_sb"


@responses.activate
def test_rotated_token_file_is_per_profile(cfg, tmp_path):
    cfg.ads.token_path = str(tmp_path / "rt")
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "a", "refresh_token": "rotated", "expires_in": 3600})
    auth.get_access_token(cfg, profile="sgs")
    assert (tmp_path / "rt.sgs").read_text() == "rotated"
    assert not (tmp_path / "rt.superbolt").exists()
