"""Compile the React frontend into the path the container serves.

Raises on ANY build failure (non-zero npm exit, or no index.html produced) so a
broken build can never silently ship stale assets inside the image. Shared by
redeploy_app and deploy_full.

Installs dependencies first when the lockfile has moved ahead of node_modules.
Without that, a deploy from a branch that added a dependency fails inside `tsc`
with errors that point at the source file rather than at the real cause: the
package was in package.json and the lockfile but had never been installed, so
its types resolved to nothing and every typed callback became an implicit any.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess


def _deps_are_current(src: pathlib.Path) -> bool:
    """True when node_modules already matches the lockfile.

    npm writes node_modules/.package-lock.json on every install, so comparing its
    mtime against package-lock.json is the cheap, reliable staleness check — and
    it keeps the common case (nothing changed) from paying for a full install on
    every deploy.
    """
    lock = src / "package-lock.json"
    installed = src / "node_modules" / ".package-lock.json"
    if not (src / "node_modules").is_dir():
        return False
    if not lock.exists():
        return True       # nothing to compare against; assume whatever is there
    if not installed.exists():
        return False      # node_modules predates npm writing the marker
    return installed.stat().st_mtime >= lock.stat().st_mtime


def _ensure_deps(npm: str, src: pathlib.Path) -> None:
    """Install node_modules when the lockfile has moved ahead of it.

    `npm ci` rather than `npm install`: it installs exactly the lockfile and will
    not quietly rewrite it, which is what a deploy wants. Falls back to
    `npm install` only when there is no lockfile to honour.
    """
    if _deps_are_current(src):
        return
    cmd = [npm, "ci"] if (src / "package-lock.json").exists() else [npm, "install"]
    print(f"[frontend] dependencies stale — running {' '.join(cmd[1:])} ...")
    result = subprocess.run(cmd, cwd=src, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"frontend dependency install FAILED ({' '.join(cmd[1:])}) — "
            "refusing to build against a stale node_modules:\n"
            f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}")


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

    _ensure_deps(npm, src)

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
