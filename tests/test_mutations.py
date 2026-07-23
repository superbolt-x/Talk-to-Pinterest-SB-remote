"""Tests for write-tool validation, preview/confirm gating, and apply dispatch."""

import pinloop.mutations as m


def test_create_campaign_rejects_bad_objective(config):
    res = m.draft_create_campaign(
        config, account_id="123", name="X", objective_type="NONSENSE", daily_budget=10
    )
    assert "error" in res
    assert any("objective_type" in d for d in res["details"])


def test_create_campaign_requires_a_budget(config):
    res = m.draft_create_campaign(
        config, account_id="123", name="X", objective_type="WEB_CONVERSION"
    )
    assert any("budget" in d for d in res["details"])


def test_budget_cap_enforced(config):
    res = m.draft_create_campaign(
        config, account_id="123", name="X", objective_type="WEB_CONVERSION", daily_budget=999
    )
    assert "error" in res and "exceeds maximum" in res["error"]


def test_draft_returns_preview_and_stores_plan(config):
    res = m.draft_create_campaign(
        config, account_id="123", name="X", objective_type="WEB_CONVERSION", daily_budget=10
    )
    assert res["status"] == "PENDING_CONFIRMATION"
    assert "plan_id" in res


def test_confirm_dry_run_makes_no_call(config, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(
        m.campaigns_api, "create_campaign", lambda *a, **k: called.__setitem__("n", 1)
    )
    draft = m.draft_create_campaign(
        config, account_id="123", name="X", objective_type="WEB_CONVERSION", daily_budget=10
    )
    res = m.confirm_and_apply(config, plan_id=draft["plan_id"], dry_run=True)
    assert res["status"] == "DRY_RUN_SUCCESS"
    assert called["n"] == 0


def test_confirm_apply_dispatches_to_executor(config, monkeypatch):
    monkeypatch.setattr(
        m.campaigns_api, "create_campaign",
        lambda config, **kw: {"created": {"id": "c1", **kw}},
    )
    draft = m.draft_create_campaign(
        config, account_id="123", name="Launch", objective_type="WEB_CONVERSION", daily_budget=10
    )
    res = m.confirm_and_apply(config, plan_id=draft["plan_id"], dry_run=False)
    assert res["status"] == "APPLIED"
    assert res["result"]["created"]["name"] == "Launch"
    assert res["result"]["created"]["daily_budget"] == 10


def test_update_status_validation(config):
    res = m.draft_update_status(
        config, account_id="123", entity_type="widget", entity_id="1", status="ACTIVE"
    )
    assert any("entity_type" in d for d in res["details"])


def test_delete_pin_requires_double_confirm(config):
    res = m.draft_delete_pin(config, pin_id="p1")
    assert res["requires_double_confirm"] is True


def test_conversion_event_preview_hides_raw_pii(config):
    res = m.draft_send_conversion_event(
        config, account_id="123", event_name="checkout", action_source="web",
        event_time=1700000000, user_data={"em": "user@example.com"},
    )
    assert "_user_data" not in res["changes"]
    assert res["changes"]["user_data_keys"] == ["em"]
    assert "user@example.com" not in str(res)
