# Talk-to-Pinterest-SB-remote

A FastMCP server that connects **Pinterest** to Claude — organic **pins & boards**,
ads **campaign management**, and **conversions** (tags + the Conversions API). It's
the Pinterest sibling of `Talk-to-Google-SB-remote` / `Talk-to-Bing-SB-remote`,
deployed the same way on Railway (authless + shared-secret token gate).

Package: `pinloop`. Pinterest API: **v5** (`https://api.pinterest.com/v5`), plain
REST/JSON over `requests`.

## Tools

**Reads (`readOnlyHint`)**
- `health_check`, `get_user_account`, `list_ad_accounts`, `split_date_range`
- Pins & boards: `list_boards`, `get_board`, `list_board_sections`,
  `list_board_pins`, `list_pins`, `get_pin`, `get_pin_analytics`
- Campaign mgmt: `list_campaigns`, `get_campaign`, `list_ad_groups`, `list_ads`,
  `get_campaign_analytics`, `get_ad_group_analytics`, `get_ad_analytics`
- Conversions: `list_conversion_tags`, `get_conversion_tag`

**Writes (`destructiveHint` where they delete/archive/send)** — every write returns
a preview; nothing changes until `confirm_and_apply(plan_id, dry_run=false)`:
- Pins & boards: `draft_create_board`, `draft_delete_board`, `draft_create_pin`,
  `draft_delete_pin`
- Campaign mgmt: `draft_create_campaign`, `draft_create_ad_group`,
  `draft_update_status`
- Conversions: `draft_create_conversion_tag`, `draft_send_conversion_event`
- `confirm_and_apply`

Budgets/bids are passed in **account currency** (e.g. dollars) and converted to
Pinterest micro-currency internally. Analytics endpoints require the entity ids
(`campaign_ids`/`ad_group_ids`/`ad_ids`) and take inclusive `YYYY-MM-DD` dates;
use `split_date_range` for week-over-week pulls so shared boundary days don't
double-count.

## Auth model

`pinloop` never runs OAuth against Claude — it's **authless** (Parker-style). If
`MCP_AUTH_TOKEN` is set, `TokenGateMiddleware` requires it on every request via the
connector URL (`?access_token=...`) or a Bearer header. Set `SERVER_URL` (or rely
on Railway's `RAILWAY_PUBLIC_DOMAIN`) so DNS-rebinding allowed-hosts are derived
correctly, else `/mcp` returns 421.

### Pinterest token lifecycle

The server holds a long-lived **refresh token** (`PINTEREST_REFRESH_TOKEN`) and
mints short-lived access tokens on demand, caching them in-process. Pinterest uses
**continuous refresh** — the refresh token does **not** rotate by default — so a
static env var is enough and **no volume is required**.

If your app has refresh-token rotation enabled, set `PINTEREST_TOKEN_PATH` to a
file on a mounted Railway volume; the server persists each rotated token there.

### Multiple Pinterest logins

If your ad accounts are split across several Pinterest logins (e.g. your own plus
a client's), set `PINTEREST_PROFILES='{"superbolt":"<refresh>","sgs":"<refresh>"}'`
(same app id/secret). `list_ad_accounts` merges every login's accounts and tags each
with its `profile`; any call under `/ad_accounts/{id}/...` (campaigns, analytics,
conversion tags, writes) is routed to the login that owns that account, and a 403
rebuilds the routing map once and retries. If two logins see the same account the
first profile wins. Boards/pins and other non-account calls use the first profile.
`health_check` and `get_user_account` report per profile.

Mint one refresh token per login: run the script below once for each, signed in as
that Pinterest user (use a private window), with the same scopes.

## One-time setup: mint the seed token

You need your own Pinterest app (app id + secret) with a registered redirect URI.

```bash
export PINTEREST_APP_ID=... PINTEREST_APP_SECRET=... PINTEREST_REDIRECT_URI=...
python3 scripts/mint_seed_token.py
```

Open the printed URL, approve, paste the `code` from the redirect. Copy the
`PINTEREST_REFRESH_TOKEN` it prints into Railway.

## Environment variables

See [`.env.example`](.env.example). Required: `PINTEREST_APP_ID`,
`PINTEREST_APP_SECRET`, `PINTEREST_REFRESH_TOKEN`. Common: `PINTEREST_AD_ACCOUNT_ID`,
`SERVER_URL`, `MCP_AUTH_TOKEN`, `PINLOOP_MAX_DAILY_BUDGET`, `PINLOOP_REQUIRE_DRY_RUN`.

## Local dev

```bash
pip install -e ".[dev]"
pytest -q                                   # unit tests (no network)
MCP_TRANSPORT=stdio python -m pinloop        # stdio for local Claude
# or HTTP:
PINTEREST_APP_ID=... PINTEREST_APP_SECRET=... PINTEREST_REFRESH_TOKEN=... \
  MCP_PORT=8000 python -m pinloop
curl localhost:8000/health
```

## Deploy (Railway)

Dockerfile + `railway.toml` are ready (health check on `/health`). Push the repo,
set the env vars above, deploy. Then add the connector in Claude as
`https://<railway-host>/mcp?access_token=<MCP_AUTH_TOKEN>` (remove and re-add a
fresh connector rather than reconnecting an old OAuth one).
