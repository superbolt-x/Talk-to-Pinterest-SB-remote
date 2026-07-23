#!/usr/bin/env python3
"""
Mint the one-time seed refresh token for Talk-to-Pinterest-SB-remote.

Runs the OAuth authorization-code flow with manual code-paste — no local
callback server needed, so it works even when your registered redirect URI is a
real https URL. The resulting refresh token is the long-lived seed you set as
PINTEREST_REFRESH_TOKEN in Railway (Pinterest does not rotate it by default).

Flow:
  1. Prints an authorization URL. Open it, approve the scopes.
  2. Pinterest redirects to your registered redirect_uri with ?code=... in the
     address bar (the page may 404 — only the URL matters). Copy the code.
  3. Paste it here; the script exchanges it for tokens and verifies them.

Config from env vars or a .env file next to this script:
  PINTEREST_APP_ID        (required)
  PINTEREST_APP_SECRET    (required)
  PINTEREST_REDIRECT_URI  (required; must exactly match a URI registered on the app)
  PINTEREST_SCOPES        (optional; default covers pins, boards, ads, conversions)
  PINTEREST_API_BASE      (optional; default https://api.pinterest.com/v5)
"""
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("PINTEREST_API_BASE", "https://api.pinterest.com/v5")
AUTH_URL = "https://www.pinterest.com/oauth/"
DEFAULT_SCOPES = (
    "user_accounts:read,"
    "boards:read,boards:write,"
    "pins:read,pins:write,"
    "ads:read,ads:write"
)

try:
    import certifi

    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()


def load_dotenv():
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def mask(secret, keep=6):
    if not secret:
        return "<empty>"
    if len(secret) <= keep:
        return "*" * len(secret)
    return f"{secret[:keep]}…{secret[-4:]} (len {len(secret)})"


def _send(req):
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            pass
        return e.code, detail
    except urllib.error.URLError as e:
        return None, {"error": "connection_failed", "reason": str(e.reason)}


def exchange_code(app_id, app_secret, code, redirect_uri):
    body = urllib.parse.urlencode(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
    ).encode()
    basic = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()
    req = urllib.request.Request(
        f"{API_BASE}/oauth/token",
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    return _send(req)


def verify(access_token):
    req = urllib.request.Request(
        f"{API_BASE}/user_account",
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    return _send(req)


def main():
    load_dotenv()
    app_id = os.environ.get("PINTEREST_APP_ID")
    app_secret = os.environ.get("PINTEREST_APP_SECRET")
    redirect_uri = os.environ.get("PINTEREST_REDIRECT_URI")
    scopes = os.environ.get("PINTEREST_SCOPES", DEFAULT_SCOPES)

    missing = [
        n for n, v in (
            ("PINTEREST_APP_ID", app_id),
            ("PINTEREST_APP_SECRET", app_secret),
            ("PINTEREST_REDIRECT_URI", redirect_uri),
        ) if not v
    ]
    if missing:
        print(f"ERROR: missing config: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    params = urllib.parse.urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "state": "mint",
        }
    )
    print("app_id:       ", mask(app_id))
    print("redirect_uri: ", redirect_uri)
    print("scopes:       ", scopes)
    print("\nOpen this URL, approve, then copy the `code` param from the redirect:\n")
    print(f"{AUTH_URL}?{params}\n")

    code = input("Paste the `code` value here: ").strip()
    if code.startswith("http") and "code=" in code:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query).get("code", [""])[0]
    if not code:
        print("ERROR: no code provided.", file=sys.stderr)
        sys.exit(2)

    print("\nExchanging code for tokens...")
    status, body = exchange_code(app_id, app_secret, code, redirect_uri)
    if status != 200:
        print(f"EXCHANGE FAILED (status {status}): {json.dumps(body, indent=2)}")
        sys.exit(1)

    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    print("OK. scope:", body.get("scope"), "| expires_in:", body.get("expires_in"))

    status, acct = verify(access_token)
    print("Verify:", "WORKS" if status == 200 else f"FAILED ({status})",
          "| username:", acct.get("username") if status == 200 else acct)

    print("\n--- Set these in Railway (refresh token is the long-lived seed) ---")
    print(f"PINTEREST_ACCESS_TOKEN={access_token}")
    print(f"PINTEREST_REFRESH_TOKEN={refresh_token}")


if __name__ == "__main__":
    main()
