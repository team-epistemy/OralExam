"""Compile the React frontend into the path the container serves.

Raises on ANY build failure (non-zero npm exit, or no index.html produced) so a
broken build can never silently ship stale assets inside the image. Shared by
redeploy_app and deploy_full.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess


def build_frontend(root: pathlib.Path) -> list[str]:
    """Build the SPA and replace the served bundle. Returns the built js bundles.

    Wiping the served directory before copying is what prevents a stale bundle
    from lingering next to a fresh one.
    """
    src = root / "frontend"
    dist = src / "dist"
    served = root / "backend" / "app" / "static" / "frontend"

    if not (src / "package.json").exists():
        raise RuntimeError(f"no package.json under {src}")

    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm not found on PATH — required to build the frontend")

    result = subprocess.run([npm, "run", "build"], cwd=src,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "frontend build FAILED — refusing to deploy stale assets:\n"
            f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}")

    if not (dist / "index.html").exists():
        raise RuntimeError(f"frontend build produced no index.html in {dist}")

    if served.exists():
        shutil.rmtree(served)
    shutil.copytree(dist, served)

    return sorted(p.name for p in (served / "assets").glob("index-*.js"))
