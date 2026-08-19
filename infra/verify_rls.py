"""Read-only RLS posture check: run diagnostic queries as the app's DB role.

  EPISTEMY_ACCOUNT=<id> AWS_REGION=us-west-2 PYTHONPATH=. python -m infra.verify_rls

Reports the connecting role, which roles bypass RLS (super/bypassrls), and each
tenant table's owner + rls_enabled + rls_forced. Uses the current image via an
entryPoint override (no rebuild). Re-run after the fix to confirm.
"""
from __future__ import annotations
import time

import boto3

from backend.config import Settings
from infra import network, schema_apply

FAMILY = "epistemy-m3-rls-diag"

DIAG = r"""
import os, sys, importlib
sys.path.insert(0, "/app")
pkg = "backend" if os.path.isdir("/app/backend") else "epistemy_m3"
factory = importlib.import_module(pkg + ".app.factory")
load_settings = importlib.import_module(pkg + ".config").load_settings
conn = factory.db_connection(load_settings()); cur = conn.cursor()
def show(title, sql):
    cur.execute(sql)
    print("\n### " + title)
    for r in cur.fetchall():
        print(" | ".join("" if x is None else str(x) for x in r))
show("connecting role (current_user | session_user)", "SELECT current_user, session_user")
show("roles: rolname | super | bypassrls | canlogin",
     "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles ORDER BY rolname")
show("tables: schema | table | owner | rls_enabled | rls_forced",
     "SELECT n.nspname, c.relname, pg_get_userbyid(c.relowner), c.relrowsecurity,"
     " c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace"
     " WHERE c.relkind='r' AND n.nspname IN ('public','auth') ORDER BY 1,2")
print("\nDIAG_DONE")
"""


def main() -> None:
    s = Settings()
    sess = boto3.Session(region_name=s.region)
    ecs, ec2 = sess.client("ecs"), sess.client("ec2")
    secret = sess.client("secretsmanager").describe_secret(SecretId=s.db_secret_name)["ARN"]
    subnets, groups = network.default_network(ec2)
    task_def = _register(ecs, s, secret)
    print(f"running {FAMILY} ...")
    status = schema_apply.run_migrate_task(ecs, s.cluster_name, task_def, subnets, groups[0])
    print(f"task ended: {status}")
    _print_logs(sess.client("logs"), s.log_group)


def _register(ecs, s: Settings, secret_arn: str) -> str:
    """Register a one-off task that runs DIAG via an entryPoint override."""
    env = [{"name": "AWS_REGION", "value": s.region},
           {"name": "EPISTEMY_DB_SECRET_ARN", "value": secret_arn},
           {"name": "EPISTEMY_ACCOUNT", "value": s.account_id},
           {"name": "PYTHONPATH", "value": "/app"}]
    return ecs.register_task_definition(
        family=FAMILY, networkMode="awsvpc", requiresCompatibilities=["FARGATE"],
        cpu="256", memory="512", executionRoleArn=s.role_arn(s.exec_role),
        taskRoleArn=s.role_arn(s.task_role),
        containerDefinitions=[{
            "name": "diag", "image": s.ecr_image(), "essential": True,
            "entryPoint": ["python", "-c", DIAG], "environment": env,
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": s.log_group, "awslogs-region": s.region,
                "awslogs-stream-prefix": "diag"}}}]
    )["taskDefinition"]["taskDefinitionArn"]


def _print_logs(logs, group: str) -> None:
    """Fetch and print the most recent diag stream's events."""
    time.sleep(3)
    streams = logs.describe_log_streams(
        logGroupName=group, logStreamNamePrefix="diag").get("logStreams", [])
    streams.sort(key=lambda s: s.get("creationTime", 0), reverse=True)
    if not streams:
        print("no diag log stream yet; tail manually:",
              f"aws logs tail {group} --log-stream-name-prefix diag")
        return
    events = logs.get_log_events(logGroupName=group,
                                 logStreamName=streams[0]["logStreamName"],
                                 startFromHead=True)["events"]
    print("\n".join(e["message"] for e in events))


if __name__ == "__main__":
    main()
