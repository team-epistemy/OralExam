"""Provisioning endpoints: professors (admin-created), invitations (self-serve join).

All tenant writes bind app.org_id to the caller's verified org_id (the x-org-name
header, overwritten by the auth middleware). Redemption is the one exception: the
redeemer has no app_user yet, so it validates the raw token and calls the
SECURITY DEFINER auth.redeem_invitation to provision atomically.
"""
from __future__ import annotations
import hashlib
import json
import secrets
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from backend.app import factory
from backend.auth.provision import cognito_admin_create, insert_app_user
from backend.models import Role


class ProfessorRequest(BaseModel):
    email: str
    password: str | None = None


class StudentRequest(BaseModel):
    email: str
    course_id: str | None = None
    password: str | None = None


class InvitationRequest(BaseModel):
    course_id: str | None = None
    capacity: int = 1
    expires_at: datetime | None = None


class RedeemRequest(BaseModel):
    code: str


def _require(role: str, *allowed: str) -> None:
    if role not in allowed:
        raise HTTPException(status_code=403, detail="insufficient role")


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _temp_password() -> str:
    """A password satisfying the 12+ upper/lower/digit/symbol pool policy."""
    return "Ep1!" + secrets.token_urlsafe(9)


def _audit(cur, action: str, target: str, org_id: str, actor: str) -> None:
    """Append an audit row (INSERT-only table); tenant already bound."""
    cur.execute(
        "INSERT INTO auth.audit_log (action, target, org_id, meta) "
        "VALUES (%s, %s, %s, %s)",
        (action, target, org_id, json.dumps({"actor": actor})))


def register_auth_routes(app: FastAPI, deps) -> None:
    """Attach professor-provisioning and invitation endpoints."""

    @app.post("/api/auth/professors")
    def create_professor(req: ProfessorRequest, x_org_name: str = Header(...),
                         x_user_id: str = Header(...), x_role: str = Header(...)):
        """platform_admin creates a professor in their own org."""
        _require(x_role, Role.PLATFORM_ADMIN.value)
        d = deps()
        cognito = factory.build_cognito_config(d["settings"])
        sub = cognito_admin_create(cognito["user_pool_id"], req.email,
                                   cognito["region"], req.password)
        pool = d["pool"]
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.org_id', %s, false)", (x_org_name,))
                uid = insert_app_user(cur, sub, req.email, x_org_name, "professor")
                _audit(cur, "create_professor", req.email, x_org_name, x_user_id)
            conn.commit()
        finally:
            factory.return_connection_to_pool(pool, conn)
        return {"id": uid, "email": req.email, "role": "professor"}

    @app.post("/api/auth/students")
    def create_student(req: StudentRequest, x_org_name: str = Header(...),
                       x_user_id: str = Header(...), x_role: str = Header(...)):
        """professor/admin provisions a student (Cognito user + enrollment).

        Returns the temp password once so the professor can share it — the most
        reliable pilot path, no self-signup or email verification required.
        """
        _require(x_role, Role.PROFESSOR.value, Role.PLATFORM_ADMIN.value)
        password = req.password or _temp_password()
        d = deps()
        cognito = factory.build_cognito_config(d["settings"])
        sub = cognito_admin_create(cognito["user_pool_id"], req.email,
                                   cognito["region"], password)
        pool = d["pool"]
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.org_id', %s, false)", (x_org_name,))
                uid = insert_app_user(cur, sub, req.email, x_org_name, "student")
                if req.course_id:
                    cur.execute(
                        """INSERT INTO auth.enrollment (app_user_id, course_id, org_id)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (app_user_id, course_id) DO NOTHING""",
                        (uid, req.course_id, x_org_name))
                _audit(cur, "create_student", req.email, x_org_name, x_user_id)
            conn.commit()
        finally:
            factory.return_connection_to_pool(pool, conn)
        return {"id": uid, "email": req.email, "role": "student", "password": password}

    @app.post("/api/auth/invitations")
    def create_invitation(req: InvitationRequest, x_org_name: str = Header(...),
                          x_user_id: str = Header(...), x_role: str = Header(...)):
        """professor/admin mints a join code (plaintext returned once)."""
        _require(x_role, Role.PROFESSOR.value, Role.PLATFORM_ADMIN.value)
        code = secrets.token_urlsafe(9)
        d = deps()
        pool = d["pool"]
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.org_id', %s, false)", (x_org_name,))
                cur.execute(
                    """INSERT INTO auth.invitation
                         (org_id, course_id, code_hash, capacity, expires_at)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                    (x_org_name, req.course_id, _hash_code(code),
                     req.capacity, req.expires_at))
                inv_id = str(cur.fetchone()[0])
                _audit(cur, "create_invitation", inv_id, x_org_name, x_user_id)
            conn.commit()
        finally:
            factory.return_connection_to_pool(pool, conn)
        return {"id": inv_id, "code": code, "capacity": req.capacity}

    @app.post("/api/auth/invitations/redeem")
    def redeem(req: RedeemRequest, authorization: str = Header(None)):
        """Public at the middleware: validate the token, then provision atomically.

        The caller must present a valid Cognito access token but need not yet be a
        provisioned app_user — redemption is how they become one.
        """
        token = authorization[7:] if authorization and \
            authorization.startswith("Bearer ") else None
        if not token:
            raise HTTPException(status_code=401, detail="access token required")
        d = deps()
        try:
            claims = d["resolver"].validate_only(token)
        except Exception:
            raise HTTPException(status_code=401, detail="invalid token")
        sub, email = claims["sub"], claims.get("email", "")
        pool = d["pool"]
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT org_id, role, course_id "
                    "FROM auth.redeem_invitation(%s, %s, %s)",
                    (_hash_code(req.code), sub, email))
                row = cur.fetchone()
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc).strip())
        finally:
            factory.return_connection_to_pool(pool, conn)
        org_id, role, course_id = row
        return {"orgId": str(org_id), "role": role,
                "courseId": str(course_id) if course_id else None}
