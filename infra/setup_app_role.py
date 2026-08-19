"""Create the non-owner runtime DB role `epistemy_app` and its credential.

  EPISTEMY_ACCOUNT=<id> AWS_REGION=us-west-2 PYTHONPATH=. python -m infra.setup_app_role

Locally mints the app secret (password never printed), then runs an in-VPC admin
task that CREATE/ALTER ROLEs epistemy_app (NOSUPERUSER, NOBYPASSRLS) and grants it
least-privilege DML — so the app stops connecting as the table owner and RLS bites.
Idempotent. Re-run after adding new schemas to extend grants.
"""
from __future__ import annotations
import json
import secrets as pysecrets

import boto3

from backend.config import Settings
from infra import network, schema_apply

FAMILY = "epistemy-m3-approle"

# Runs in-container as epistemy_admin (owner). Reads the app secret for the
# password, then creates/repairs the least-privilege runtime role.
SETUP = r"""
import os, json, boto3, psycopg2
from psycopg2 import sql
region = os.environ["AWS_REGION"]; sm = boto3.client("secretsmanager", region_name=region)
admin = json.loads(sm.get_secret_value(SecretId=os.environ["EPISTEMY_DB_SECRET_ARN"])["SecretString"])
app = json.loads(sm.get_secret_value(SecretId=os.environ["EPISTEMY_APP_SECRET_ARN"])["SecretString"])
conn = psycopg2.connect(host=admin["host"], port=admin.get("port", 5432),
                        dbname=admin.get("dbname", "epistemy"), user=admin["username"],
                        password=admin["password"], connect_timeout=10)
conn.autocommit = True; cur = conn.cursor(); role = app["username"]; pw = app["password"]
cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
verb = "ALTER" if cur.fetchone() else "CREATE"
cur.execute(sql.SQL(verb + " ROLE {} WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD %s")
            .format(sql.Identifier(role)), (pw,))
for stmt in [
    "GRANT USAGE ON SCHEMA public TO {r}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {r}",
]:
    cur.execute(sql.SQL(stmt).format(r=sql.Identifier(role)))
print("APPROLE_DONE role=" + role)
"""


def main() -> None:
    s = Settings()
    sess = boto3.Session(region_name=s.region)
    sm = sess.client("secretsmanager")
    admin_arn = sm.describe_secret(SecretId=s.db_secret_name)["ARN"]
    app_arn = _ensure_app_secret(sm, s, admin_arn)
    ecs, ec2 = sess.client("ecs"), sess.client("ec2")
    subnets, groups = network.default_network(ec2)
    task_def = _register(ecs, s, admin_arn, app_arn)
    print(f"running {FAMILY} ...")
    print("task ended:", schema_apply.run_migrate_task(
        ecs, s.cluster_name, task_def, subnets, groups[0]))


def _ensure_app_secret(sm, s: Settings, admin_arn: str) -> str:
    """Mint/refresh the epistemy_app secret, copying host/port/db from admin."""
    admin = json.loads(sm.get_secret_value(SecretId=admin_arn)["SecretString"])
    payload = json.dumps({
        "username": "epistemy_app", "password": pysecrets.token_urlsafe(32),
        "host": admin["host"], "port": admin.get("port", 5432),
        "dbname": admin.get("dbname", s.db_name)})
    try:
        return sm.create_secret(Name=s.db_app_secret_name, SecretString=payload)["ARN"]
    except sm.exceptions.ResourceExistsException:
        return sm.put_secret_value(SecretId=s.db_app_secret_name,
                                   SecretString=payload)["ARN"]


def _register(ecs, s: Settings, admin_arn: str, app_arn: str) -> str:
    """Register the one-off role-setup task (SETUP via entryPoint override)."""
    env = [{"name": "AWS_REGION", "value": s.region},
           {"name": "EPISTEMY_DB_SECRET_ARN", "value": admin_arn},
           {"name": "EPISTEMY_APP_SECRET_ARN", "value": app_arn}]
    return ecs.register_task_definition(
        family=FAMILY, networkMode="awsvpc", requiresCompatibilities=["FARGATE"],
        cpu="256", memory="512", executionRoleArn=s.role_arn(s.exec_role),
        taskRoleArn=s.role_arn(s.task_role),
        containerDefinitions=[{
            "name": "approle", "image": s.ecr_image(), "essential": True,
            "entryPoint": ["python", "-c", SETUP], "environment": env,
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": s.log_group, "awslogs-region": s.region,
                "awslogs-stream-prefix": "approle"}}}]
    )["taskDefinition"]["taskDefinitionArn"]


if __name__ == "__main__":
    main()
