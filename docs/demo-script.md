# NEXUS demo script (M2 part: Project A)

Setup: `make up`, then `docker compose exec api python -m app.seed`, then serve `frontend/` on port 5500.

1. Sign in as `pm@nexus.test` / `pm12345`.
2. Create project "Clinic Appointment Booking": paste the brief from `docs/project-a-brief.md`, upload `demo_data/project-a-transcript.txt`.
3. Team tab: Import CSV `demo_data/project-a-team.csv`; edit one member's capacity.
4. Set Team Leader: Omar Hassan -> `leader@nexus.test`.
5. Refresh the page: everything is still there (PostgreSQL is the source of truth).
6. Try an unsupported file and an oversized file: clear error messages.
7. Sign in as `leader@nexus.test` / `leader12345`: sees only this project, read-only.
