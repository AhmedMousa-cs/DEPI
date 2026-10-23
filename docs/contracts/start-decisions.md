
# MAPMS — Start Decisions

Status: Planning record. Unresolved items are not approved decisions.
Source of truth: MAPMS_Final_System_Design_and_Implementation_Blueprint.md

## 1. Development Mode

- [G] Core development uses Replay Mode with deterministic fixtures.
- [G] Missing replay fixtures must fail loudly; no silent fallback.
- [G] Live LLM access must not block Core development.

## 2. Decision 2 — Database Isolation and Cross-Project Atomicity

Status: Unresolved.

Questions to decide:
- Is one shared PostgreSQL database with logical project isolation acceptable?
- Does "change together" mean all-or-none across affected projects?

Required evidence:
- Complete Spike X and record the results.
- Prove coordinated transaction behavior before claiming atomicity.

If atomic apply is not approved or cannot be proven:
- Use the documented ordered, idempotent fallback.
- Expose the result as `partially_applied` when only some project changes succeed.
- Never claim all-or-nothing behavior without proof.

Gate: Decision 2 and the required Spike X experiments must be resolved before Core implementation proceeds.

## 3. Decision 3 — Authoritative Guide and v12

Status: Unresolved.

Questions to decide:
- Is the reviewed 8-page PDF the authoritative Guide?
- Does `v12` mean a candidate snapshot?

Current evidence:
- The available Step-by-Step Guide describes the architecture and three scenarios.
- A separate `v12` document or version has not been located.
- The meaning of `v12` must not be inferred from its mention in an example.

Gate: Resolve Decision 3 before Freeze-1. If unresolved, do not sign Freeze-1 or implement code that depends on frozen shared contracts.

## 4. Shared Contracts

- [G] Freeze-1 requires stable shared contracts and Decision 3.
- [G] Breaking contract changes require a written change note and the required reviews.
- [G] Do not implement components against unstable shared contracts.

Status: Pending Freeze-1.

## 5. Accepted-State Safety

- [G] A proposal is not an accepted fact.
- [G] Core Mode A does not auto-promote proposals.
- [G] Only AG-07 writes accepted project state.
- [G] Accepted changes require authorization and transactional validation.
- [G] Stale or failed applies must not change accepted state.

## 6. Implementation Gates

- [G] Complete M1 environment and architecture-linter acceptance checks.
- [G] Resolve Decision 3 before Freeze-1.
- [G] Complete Spike X and resolve Decision 2 before Core schema and State Manager implementation.
- [G] Record gate evidence and decisions before proceeding.

## 7. Open Items

- Decision 2: Resolve database isolation acceptability and cross-project all-or-none semantics.
- Decision 3: Confirm the authoritative Guide and the meaning of `v12`.
- Spike X: Record experiment results and the approved atomic/fallback path.
- M1: Verify all required environment and test-harness deliverables.

No unresolved item is considered approved by omission.
