# Repo Re-root & Workspace Tidying — Design

> Status: approved design, not yet executed. Written 2026-08-12.
> This is Stage 1 of a two-stage plan. Stage 2 (the `apps/` + `packages/`
> architectural refactor) is a separate spec, deferred.

## Goal

Enforce one rule: **the pushed git repo is the code, nothing else.**

Today the git repo is rooted at `/Users/uthira/Desktop/epistemy/`, a folder
that mixes product code with prototypes, personal notes, a 64 MB demo video,
credentials, and Seaglass scaffolding. The single `first commit` (already pushed
to `github.com/uthiramohan/epistemy`) tracks the code **and** three top-level
docs.

After this change:
- The GitHub repo, when cloned, drops you directly into the project
  (`backend/`, `frontend/`, `infra/`, `docs/`, `tests/` at the root).
- The top-level `epistemy/` folder is a **local-only workspace** — never a git
  repo, never pushed.

## Decisions (locked)

1. **Re-root** the repository at `m3-content-ingestion/`. The top-level
   `epistemy/` stops being a git repo.
2. **Architecture docs + the EDS fixture travel with the code**, in
   `docs/` inside the repo.
3. **Everything else stays local**, in the top-level workspace, unpushed.

## Target layout

### Pushed repo (was `m3-content-ingestion/`)
```
<repo root>/
├── backend/          # application code (unchanged)
├── frontend/             # React source (unchanged)
├── infra/                # deploy scripts (unchanged)
├── tests/                # incl. smoke_flows.py (unchanged)
├── docs/                 # architecture docs — NEW, populated below
│   └── superpowers/specs/  # this spec + future specs
├── Dockerfile, requirements*.txt, README.md, etc. (unchanged)
```

### Local-only workspace (top-level `epistemy/`, not a repo)
```
epistemy/
├── m3-content-ingestion/     # now contains its own .git (the repo)
├── prototype/                # EpistemyOralExamDemo.jsx
├── uthira_personal/
│   ├── superseded/           # concept-graph-uthiraingest/ + .zip, Copy_Dockerfile, istructurecon/
│   ├── notes/                # customer-discovery-outreach, transition, newlesson_2, s2 insimple word
│   ├── assets/               # Demo-SKyDeck-Final.mov, textbooks/
│   └── credentials/          # admin_accessKeys_epistemy.csv
├── README.md, package.json, main.skill.md, skills/, .seaglass/   # Seaglass scaffold — left in place
└── .venv/, .vscode/, .claude/                                     # tooling — left in place
```

## Docs moving into the repo (`m3-content-ingestion/docs/`)

| Doc | Current location | Current git state |
|---|---|---|
| `HLD-Agentic-EdTech-Platform.md` | top-level | tracked |
| `CLOUD-SERVICES.md` | top-level | tracked |
| `ARCHITECTURE-NETWORKING-internal.md` | top-level | tracked ⚠️ see open item |
| `SCHEMA-REGISTRY.md` | top-level | gitignored |
| `M3-tasks.md` | top-level | gitignored |
| `eds_representative_responses.md` | top-level | untracked (EDS golden fixture for Stage 2) |
| `SYSTEM-VIEW.md` | `m3-content-ingestion/` | tracked — relocated into `docs/` |

## Execution plan (documented here, run after the implementation plan)

Order matters: move files in, build the new repo, **verify the push**, and only
then retire the old repo.

1. **Move docs in.** `mkdir m3-content-ingestion/docs`; move the seven docs
   above into it (plain `mv` — the old repo is about to be replaced).
2. **Write the new `.gitignore`** at `m3-content-ingestion/` (see below).
3. **Re-init.** There is exactly one commit (`first commit`) and one
   contributor, so a clean re-init loses nothing of value and avoids
   `git filter-repo` sharp edges:
   ```
   cd m3-content-ingestion
   git init -b main
   git remote add origin https://github.com/uthiramohan/epistemy.git
   git add -A && git commit -m "Re-root repository at code folder; move architecture docs into docs/"
   ```
4. **Force-push** (replaces the old root commit on the solo remote — confirmed
   acceptable):
   ```
   git push -f origin main
   ```
5. **Verify** the remote now holds a code-rooted tree (`git ls-remote origin`,
   and inspect the pushed tree) **before** touching the old repo.
6. **Retire the old repo safely.** Rather than `rm -rf`, rename it first so it's
   recoverable:
   ```
   mv /Users/uthira/Desktop/epistemy/.git /Users/uthira/Desktop/epistemy/.git.bak-reroot
   ```
   Delete `.git.bak-reroot` only after confirming the new repo works. (The old
   commit `578cde7` also remains recoverable via GitHub for a period.)
7. **Tidy the local workspace** (cosmetic — none of it is pushed now): create
   `prototype/` and `uthira_personal/{superseded,notes,assets,credentials}`,
   move the remaining files in, and delete the empty `untitled folder`.

### New `.gitignore` for the code repo
```
# OS / editor
.DS_Store
.vscode/
.venv/

# Python
__pycache__/
*.pyc
.pytest_cache/

# Node / frontend build artifacts
frontend/node_modules/
frontend/dist/
backend/app/static/frontend/

# Secrets / local env
.env
*.env
```

## Rollback

Before step 6, the old repo is intact. If the new push looks wrong, the new
`m3-content-ingestion/.git` can be deleted and the top-level repo restored by
renaming `.git.bak-reroot` back to `.git`. The pre-rewrite remote commit
`578cde7` is recoverable from GitHub until it is garbage-collected.

## Out of scope (Stage 2, separate spec)

The `apps/` + `packages/` restructure, extracting pure `scoring/`, the prompt
registry, import-linter, Alembic, and the OpenAPI-generated TS client. See
`docs/REPO-STRUCTURE-PROPOSAL.md` for the thinking; none of it is done here.

## Open items

- **Repo visibility.** `gh` is not installed locally, so visibility could not be
  verified. `ARCHITECTURE-NETWORKING-internal.md` was already in the pushed
  `first commit`, so re-rooting adds **no new exposure** — but confirm the repo
  is private before treating the networking doc as safe. If it is public,
  consider keeping that one doc local instead.
