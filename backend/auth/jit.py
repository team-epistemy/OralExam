"""Just-in-time Cognito -> GCIP migration behind POST /api/auth/migrate.

On a user's first GCIP sign-in they have no GCIP account yet. We verify their
password against Cognito (unauthenticated InitiateAuth — no AWS creds needed, so
it works from Cloud Run), then create the GCIP user with uid == their existing
IdP subject. Because the GCIP token's `sub` then equals the stored
auth.app_user.cognito_sub, the resolver maps it to the same user with no row
rewrite. Idempotent: a user already in GCIP is reported "ready". Validation of
this path needs a real Cognito test account (password), so it is unexercised on
the synthetic replica.
"""
from __future__ import annotations


class MigrationError(Exception):
    """Carries an HTTP status + detail for the endpoint to surface."""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


def verify_cognito_password(cognito_cfg: dict, email: str, password: str) -> None:
    """Raise MigrationError unless email+password authenticate to Cognito.

    Uses an UNSIGNED client: InitiateAuth/USER_PASSWORD_AUTH is a public flow, so
    no AWS credentials are required (the app client is public — no secret hash).
    """
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    idp = boto3.client("cognito-idp", region_name=cognito_cfg["region"],
                       config=Config(signature_version=UNSIGNED))
    try:
        idp.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            ClientId=cognito_cfg["client_id"],
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
    except (idp.exceptions.NotAuthorizedException,
            idp.exceptions.UserNotFoundException):
        raise MigrationError(401, "invalid email or password")
    except idp.exceptions.PasswordResetRequiredException:
        raise MigrationError(409, "password reset required on the source account")
    except Exception as exc:  # flow not enabled, throttling, etc.
        raise MigrationError(502, f"could not verify against the source identity provider: {exc}")


_fb_ready = False


def _firebase_auth(project: str):
    """Lazily init the Firebase Admin app (ADC on Cloud Run) and return auth."""
    global _fb_ready
    import firebase_admin
    from firebase_admin import auth as fb_auth
    if not _fb_ready:
        try:
            firebase_admin.initialize_app(options={"projectId": project})
        except ValueError:
            pass  # already initialized
        _fb_ready = True
    return fb_auth


def ensure_gcip_user(project: str, uid: str, email: str, password: str,
                     role: str, org_id: str) -> str:
    """Create the GCIP user with uid==sub (or confirm it exists) and set the
    role/org_id custom claims. Returns "migrated" (created now) or "ready".
    """
    fb_auth = _firebase_auth(project)
    try:
        fb_auth.get_user(uid)
        status = "ready"
    except fb_auth.UserNotFoundError:
        fb_auth.create_user(uid=uid, email=email, password=password,
                            email_verified=True)
        status = "migrated"
    fb_auth.set_custom_user_claims(uid, {"role": role, "org_id": str(org_id)})
    return status
