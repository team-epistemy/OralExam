"""Provisioning: create Cognito users and their auth.app_user rows.

Shared by bootstrap (platform admin) and the professor/student provisioning
endpoints. Cognito owns the identity (sub); app_user owns the tenant + role.
"""
from __future__ import annotations

import boto3


def _sub_of(attributes: list) -> str:
    """Pull the immutable `sub` out of a Cognito attribute list."""
    for a in attributes:
        if a["Name"] == "sub":
            return a["Value"]
    raise KeyError("cognito user has no sub attribute")


def cognito_admin_create(pool_id: str, email: str, region: str,
                         password: str | None = None) -> str:
    """Create (or fetch) a Cognito user by email; return its immutable sub.

    Idempotent: an already-existing user's sub is returned unchanged. The invite
    email is always suppressed; when a password is given it is set permanent
    (user active immediately), otherwise the user stays in FORCE_CHANGE_PASSWORD
    until a password is set out of band (admin-set-user-password).
    """
    idp = boto3.client("cognito-idp", region_name=region)
    kwargs = dict(UserPoolId=pool_id, Username=email, MessageAction="SUPPRESS",
                  UserAttributes=[{"Name": "email", "Value": email},
                                  {"Name": "email_verified", "Value": "true"}])
    try:
        sub = _sub_of(idp.admin_create_user(**kwargs)["User"]["Attributes"])
    except idp.exceptions.UsernameExistsException:
        sub = _sub_of(idp.admin_get_user(
            UserPoolId=pool_id, Username=email)["UserAttributes"])
    if password:
        idp.admin_set_user_password(UserPoolId=pool_id, Username=email,
                                    Password=password, Permanent=True)
    return sub


def insert_app_user(cur, cognito_sub: str, email: str, org_id, role: str,
                    status: str = "active") -> str:
    """Upsert the app_user mapping; returns its id. Caller commits."""
    cur.execute(
        """INSERT INTO auth.app_user (cognito_sub, email, org_id, role, status)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (cognito_sub) DO UPDATE
             SET email = EXCLUDED.email, org_id = EXCLUDED.org_id,
                 role = EXCLUDED.role, status = EXCLUDED.status
           RETURNING id""",
        (cognito_sub, email, str(org_id), role, status))
    return str(cur.fetchone()[0])
