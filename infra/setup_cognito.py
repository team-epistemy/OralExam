"""Provision the dev Cognito substrate: user pool + public PKCE app client + Hosted UI.

  EPISTEMY_ACCOUNT=<id> AWS_REGION=us-west-2 PYTHONPATH=. python -m infra.setup_cognito

Idempotent — reuses resources matched by name, never deletes. Writes a non-secret
config blob to Secrets Manager (epistemy/cognito-<env>) for the API to consume.
"""
from __future__ import annotations
import json

import boto3

from backend.config import Settings

# Localhost pair covers Vite dev; the prod pair is the HTTPS CloudFront front
# (/app base) over the http-only ALB. SPA PKCE returns to /callback; /login is
# the logout landing.
PROD_ORIGIN = "https://d3fxwe1wjfkcz0.cloudfront.net/app"
LOCAL_CALLBACKS = ["http://localhost:5173/callback", "http://localhost:5173/"]
LOCAL_LOGOUTS = ["http://localhost:5173/login"]
PROD_CALLBACKS = [f"{PROD_ORIGIN}/callback", f"{PROD_ORIGIN}/"]
PROD_LOGOUTS = [f"{PROD_ORIGIN}/login"]


def main() -> None:
    s = Settings()
    sess = boto3.Session(region_name=s.region)
    idp = sess.client("cognito-idp")

    pool_id, pool_created = _ensure_pool(idp, s)
    domain = _ensure_domain(idp, s, pool_id)
    callbacks, logouts, warn = _callback_urls()
    client_id, client_created = _ensure_client(idp, pool_id, s, callbacks, logouts)

    issuer = f"https://cognito-idp.{s.region}.amazonaws.com/{pool_id}"
    # Prefix Hosted UI lives under amazoncognito.com, NOT amazonaws.com.
    hosted_ui_url = f"https://{domain}.auth.{s.region}.amazoncognito.com"
    _write_config(sess, s, {
        "user_pool_id": pool_id, "client_id": client_id, "domain": domain,
        "region": s.region, "issuer": issuer, "hosted_ui_url": hosted_ui_url})

    print(f"pool: {'created' if pool_created else 'reused'}  "
          f"client: {'created' if client_created else 'reused'}")
    print(f"callbacks: {callbacks}")
    if warn:
        print(f"WARNING: {warn}")
    print(f"COGNITO_DONE user_pool_id={pool_id} client_id={client_id} "
          f"issuer={issuer} hosted_ui_url={hosted_ui_url}")


def _ensure_pool(idp, s: Settings) -> tuple[str, bool]:
    """Find the user pool by name or create it with FERPA-aligned policy."""
    name = s.user_pool_name
    token = None
    while True:
        kw = {"MaxResults": 60, **({"NextToken": token} if token else {})}
        resp = idp.list_user_pools(**kw)
        for p in resp.get("UserPools", []):
            if p["Name"] == name:
                _assert_email_signin(idp, p["Id"])
                return p["Id"], False
        token = resp.get("NextToken")
        if not token:
            break
    pool = idp.create_user_pool(
        PoolName=name,
        UsernameAttributes=["email"],
        AutoVerifiedAttributes=["email"],
        AdminCreateUserConfig={"AllowAdminCreateUserOnly": False},
        AccountRecoverySetting={
            "RecoveryMechanisms": [{"Priority": 1, "Name": "verified_email"}]},
        Policies={"PasswordPolicy": {
            "MinimumLength": 12, "RequireUppercase": True, "RequireLowercase": True,
            "RequireNumbers": True, "RequireSymbols": True}},
        Schema=[{"Name": "email", "AttributeDataType": "String",
                 "Required": True, "Mutable": True}],
    )
    return pool["UserPool"]["Id"], True


def _assert_email_signin(idp, pool_id: str) -> None:
    """Loudly flag a reused pool whose (immutable) sign-in mode isn't email.

    UsernameAttributes can't be changed after creation, so a username-based pool
    breaks student self-signup email verification and must be recreated.
    """
    attrs = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"].get(
        "UsernameAttributes")
    if attrs != ["email"]:
        print(f"WARNING: pool {pool_id} has UsernameAttributes={attrs}, "
              f"expected ['email']. Sign-in mode is immutable — self-signup email "
              f"verification will fail. Recreate the pool to fix.")


def _ensure_domain(idp, s: Settings, pool_id: str) -> str:
    """Reuse the pool's Hosted UI domain, else create a globally-unique prefix."""
    existing = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"].get("Domain")
    if existing:
        return existing
    prefix = f"epistemy-{s.env}-{s.account_id}".lower()
    try:
        idp.create_user_pool_domain(Domain=prefix, UserPoolId=pool_id)
    except idp.exceptions.InvalidParameterException:
        # Prefix taken globally; fall back to a shorter account-suffixed prefix.
        prefix = f"epistemy-{s.env}-{s.account_id[-6:]}".lower()
        idp.create_user_pool_domain(Domain=prefix, UserPoolId=pool_id)
    return prefix


def _client_kwargs(pool_id: str, s: Settings, callbacks, logouts) -> dict:
    """Shared create/update args for the public authorization-code + PKCE client."""
    return dict(
        UserPoolId=pool_id,
        ClientName=f"epistemy-web-{s.env}",
        GenerateSecret=False,
        AllowedOAuthFlows=["code"],
        AllowedOAuthScopes=["openid", "email", "profile"],
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
        CallbackURLs=callbacks, LogoutURLs=logouts,
        PreventUserExistenceErrors="ENABLED",
        ExplicitAuthFlows=["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        AccessTokenValidity=60, IdTokenValidity=60, RefreshTokenValidity=30,
        TokenValidityUnits={"AccessToken": "minutes", "IdToken": "minutes",
                            "RefreshToken": "days"},
    )


def _ensure_client(idp, pool_id: str, s: Settings, callbacks, logouts) -> tuple[str, bool]:
    """Find the web client by name (update it) or create it."""
    name = f"epistemy-web-{s.env}"
    token = None
    while True:
        kw = {"UserPoolId": pool_id, "MaxResults": 60,
              **({"NextToken": token} if token else {})}
        resp = idp.list_user_pool_clients(**kw)
        for c in resp.get("UserPoolClients", []):
            if c["ClientName"] == name:
                idp.update_user_pool_client(ClientId=c["ClientId"],
                                            **{k: v for k, v in _client_kwargs(
                                                pool_id, s, callbacks, logouts).items()
                                               if k != "GenerateSecret"})
                return c["ClientId"], False
        token = resp.get("NextToken")
        if not token:
            break
    client = idp.create_user_pool_client(**_client_kwargs(pool_id, s, callbacks, logouts))
    return client["UserPoolClient"]["ClientId"], True


def _callback_urls() -> tuple[list, list, str]:
    """Localhost dev URLs plus the HTTPS CloudFront prod origin fronting /app."""
    return LOCAL_CALLBACKS + PROD_CALLBACKS, LOCAL_LOGOUTS + PROD_LOGOUTS, ""


def _write_config(sess, s: Settings, blob: dict) -> None:
    """Persist the non-secret Cognito config for the API (create or overwrite)."""
    sm = sess.client("secretsmanager")
    name = f"epistemy/cognito-{s.env}"
    payload = json.dumps(blob)
    try:
        sm.create_secret(Name=name, SecretString=payload)
    except sm.exceptions.ResourceExistsException:
        sm.put_secret_value(SecretId=name, SecretString=payload)


if __name__ == "__main__":
    main()
