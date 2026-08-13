"""Dev check: assert every function body is <= 15 lines. Not shipped."""
import ast
import pathlib
import sys

LIMIT = 15
roots = [pathlib.Path("backend"), pathlib.Path("infra")]
violations = []

for path in [p for r in roots for p in r.rglob("*.py")]:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_lines = node.body[-1].end_lineno - node.body[0].lineno + 1
            if body_lines > LIMIT:
                violations.append(f"{path}:{node.lineno} {node.name} = {body_lines}")

print("\n".join(violations) if violations else "OK: all functions <= 15 lines")
sys.exit(1 if violations else 0)
