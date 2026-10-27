# NEXUS: Handoff (state after M0)

## Done (all merged into main)
- Repo skeleton (backend, evals, demo_data, docs) and README
- Existing front-end moved to /frontend (app.js, index.html)
- .gitignore, .gitattributes (LF line endings), .env.example
- docker-compose.yml (db with pgvector + api), backend/Dockerfile, Makefile
- Alembic configured (reads DATABASE_URL from the environment, no migrations yet)
- docs/decisions.md and docs/project-a-brief.md
- GET /health, settings (config.py), requirements.txt

## NOT verified yet: run this first, before any M1 work
The Docker setup has never been run by anyone. Fix any failure in its own small branch first.

    git clone https://github.com/AhmedMousa-cs/DEPI.git
    cd DEPI
    Copy-Item .env.example .env
    docker compose up -d --build
    curl.exe http://localhost:8000/health
    docker compose exec api alembic current

Expected: {"status":"ok"} and Alembic runs without errors (no revisions yet is normal).

## Still open from M0
- Protect main (Settings > Branches > Require a pull request): team leader
- GitHub Project board, Milestones M0..M9, Issues for M1 and M2
- Langfuse Cloud account + project "NEXUS" (not needed before M3).
  Share keys on a private channel only, never on GitHub.
- Everyone reviews docs/decisions.md
- Front-end decision (keep vs. React) at the end of M2

## M1 order (one branch = one small PR, branch names describe the work only)
1. db-models-core
2. first-migration
3. auth-login-jwt
4. rbac-dependencies
5. seed-users
6. permission-tests
7. audit-log-writes
8. login-screen

Goal: login with two roles (PM / TEAM_LEADER); each sees only their own data;
the wrong role gets 403 on every endpoint.
M1 DoD: alembic upgrade head works on an empty DB, login returns a JWT with the
right role, permission tests pass, audit_log is written on every create/update.

## Gotchas
- first-migration: the api container has no bind mount, so autogenerate writes the
  migration INSIDE the container. Copy it out:
      docker compose exec api alembic revision --autogenerate -m "initial tables"
      docker compose cp api:/code/alembic/versions/. backend/alembic/versions/
  Then rebuild (docker compose up -d --build) so the container has the same file.
- Code changes need a rebuild: docker compose up -d --build
- If you change POSTGRES_PASSWORD in .env, change it inside DATABASE_URL too.
- Avast (and some VPNs) intercept HTTPS: Docker fails with
  "x509: certificate signed by unknown authority". Turn off HTTPS scanning in Avast,
  quit Docker Desktop, run `wsl --shutdown`, reopen Docker Desktop.
- On Windows PowerShell use curl.exe, not curl.
- Never commit .env, API keys, or secrets.
- Models and columns come from section 4 of the project blueprint PDF.