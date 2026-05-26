# API Contract Guards

Use these guards for FastAPI/API work when public contracts, auth, or external systems are touched.

## Hardcoded URL Guard

Production/public URLs must come from settings, not inline constants. Search service/core/client layers for hardcoded `http://` or `https://` values and either remove them or justify local-only test fixtures.

## IDOR Sweep

For endpoints with user-owned resources:

- require authenticated user dependency;
- verify ownership before returning or mutating data;
- include a negative test for another user's object.

## Contract Backsync

If URL, HTTP method, request schema, response schema, or status code changes:

- update spec/OpenAPI/ADR or project docs in the same change;
- grep neighboring projects for old paths or schemas;
- include the result in review artifacts.

## External Write Paths

Mock-only success is insufficient for external write paths. Require either:

- a gated live smoke test using env-controlled credentials; or
- an explicit note explaining why live smoke is impossible and what operator verification replaces it.

## Spec Test List

If a tech spec names test files or edge cases, every named test must be present and passing, or the implementation remains `NOT_READY`.

