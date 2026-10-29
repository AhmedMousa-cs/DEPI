
## Architecture Decision Records

### ADR-002: Deferring Full Project Access Control & Audit Triggers to M2
- **Status:** Approved
- **Context:** Core DB models and initial migrations are verified in M1.
- **Decision:** Defer complex RBAC access rules and detailed audit log triggers to Milestone 2 to focus on Project & File Ingestion APIs first.
- **Consequence (added in M2):** Every M2 create/update endpoint writes an `audit_log` row in the same transaction, and project-scoped endpoints use `require_project_access`; both are covered by tests.

### ADR-003: Single Alembic head and isolated test database
- **Status:** Approved
- **Context:** A duplicate `initial_schema` revision produced two root heads and broke `alembic upgrade head`. Tests also ran against the development database.
- **Decision:** One linear migration chain, enforced by `test_alembic_has_single_head`. Tests run only against `nexus_test` (the suite refuses any database whose name does not end in `_test`), built from the real migrations and cleaned after every test.

## Architecture Decision Records

### ADR-002: Deferring Full Project Access Control & Audit Triggers to M2
- **Status:** Approved
- **Context:** Core DB models and initial migrations are verified in M1.
- **Decision:** Defer complex RBAC access rules and detailed audit log triggers to Milestone 2 to focus on Project & File Ingestion APIs first.
- **Consequence (added in M2):** Every M2 create/update endpoint writes an `audit_log` row in the same transaction, and project-scoped endpoints use `require_project_access`; both are covered by tests.

### ADR-003: Single Alembic head and isolated test database
- **Status:** Approved
- **Context:** A duplicate `initial_schema` revision produced two root heads and broke `alembic upgrade head`. Tests also ran against the development database.
- **Decision:** One linear migration chain, enforced by `test_alembic_has_single_head`. Tests run only against `nexus_test` (the suite refuses any database whose name does not end in `_test`), built from the real migrations and cleaned after every test.
