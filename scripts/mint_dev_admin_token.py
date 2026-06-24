#!/usr/bin/env python3
"""Dev-only: mint a short-lived Lovv access token signed with the DEV JWT secret.

Used to smoke-test the deployed dev admin API. The token's roles come from the
MINT_ROLE env var (default R-ADMIN); the signing secret is pulled from Secrets
Manager exactly as the deployed Lambda authorizer reads it, so the issued token
verifies against the live API.

    # admin token (review/approve/reject/history/list)
    python scripts/mint_dev_admin_token.py

    # data-provider token (create/list own)
    set MINT_ROLE=R-DATA-PROVIDER && python scripts/mint_dev_admin_token.py   # cmd
    $env:MINT_ROLE="R-DATA-PROVIDER"; python scripts/mint_dev_admin_token.py  # PowerShell
"""
import os
import sys

sys.path.insert(0, "src")

import boto3  # noqa: E402

SECRET_ID = os.environ.get("AUTH_TOKEN_SIGNING_SECRET_ARN", "lovv/dev/jwt-signing-secret")
# The Lambda uses response["SecretString"] verbatim as the HMAC key, so do the same.
secret_value = boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ID)["SecretString"]

os.environ["AUTH_TOKEN_SIGNING_SECRET"] = secret_value
os.environ.setdefault("AUTH_ISSUER", "lovv-auth")
os.environ.setdefault("AUTH_AUDIENCE", "lovv-api")
os.environ.setdefault("AUTH_TOKEN_TTL_SECONDS", "3600")

from shared.auth import create_access_token  # noqa: E402

role = os.environ.get("MINT_ROLE", "R-ADMIN")
org_ids = [v for v in os.environ.get("MINT_ORG_IDS", "").split(",") if v]
region_ids = [v for v in os.environ.get("MINT_REGION_IDS", "").split(",") if v]

token = create_access_token(
    user_id=os.environ.get("MINT_USER_ID", "smoke-" + role.lower()),
    session_id="smoke-session",
    roles=[role],
    organization_ids=org_ids,
    region_ids=region_ids,
).token
print(token)
