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


def cognito_reset_password(pool_id: str, email: str, region: str, password: str) -> None:
    """Set a new permanent password for an EXISTING Cognito user (professor-driven
    'reset & reveal' recovery — a lost temp password can't be retrieved, only
    replaced). Raises UserNotFoundException if the user isn't in the pool.
    """
    idp = boto3.client("cognito-idp", region_name=region)
    idp.admin_set_user_password(UserPoolId=pool_id, Username=email,
                                Password=password, Permanent=True)


def cognito_provision_student(pool_id: str, email: str, region: str,
                              password: str) -> tuple[str, bool]:
    """Create a student Cognito user; return (sub, created).

    Unlike cognito_admin_create, an already-existing user is left untouched — its
    password is NOT reset — so re-running a roster upload never churns passwords
    already handed out. `created` is False for such users.
    """
    idp = boto3.client("cognito-idp", region_name=region)
    try:
        attrs = idp.admin_create_user(
            UserPoolId=pool_id, Username=email, MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email},
                            {"Name": "email_verified", "Value": "true"}],
        )["User"]["Attributes"]
        idp.admin_set_user_password(UserPoolId=pool_id, Username=email,
                                    Password=password, Permanent=True)
        return _sub_of(attrs), True
    except idp.exceptions.UsernameExistsException:
        sub = _sub_of(idp.admin_get_user(
            UserPoolId=pool_id, Username=email)["UserAttributes"])
        return sub, False


def insert_app_user(cur, cognito_sub: str, email: str, org_id, role: str,
                    status: str = "active") -> str:
    """Upsert the app_user mapping; returns its id. Caller commits.

    On conflict a student provision NEVER downgrades an existing professor/
    platform_admin — otherwise adding someone's email to a course roster would
    silently strip their privileges (they'd be re-routed as a student).
    """
    cur.execute(
        """INSERT INTO auth.app_user (cognito_sub, email, org_id, role, status)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (cognito_sub) DO UPDATE
             SET email = EXCLUDED.email, org_id = EXCLUDED.org_id,
                 status = EXCLUDED.status,
                 role = CASE
                     WHEN auth.app_user.role IN ('professor', 'platform_admin')
                          AND EXCLUDED.role = 'student'
                     THEN auth.app_user.role ELSE EXCLUDED.role END
           RETURNING id""",
        (cognito_sub, email, str(org_id), role, status))
    return str(cur.fetchone()[0])
