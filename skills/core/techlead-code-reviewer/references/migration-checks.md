# Migration Checks

## Safety
- Is migration reversible with explicit downgrade?
- Are destructive operations guarded and justified?
- Are locking and large table impacts assessed?

## Compatibility
- Is backward compatibility considered for rolling deploys?
- Are defaults/nullability/indexes consistent with runtime behavior?
- Is data backfill strategy defined if needed?

## Validation
- Were migration apply/rollback steps tested in non-prod?
- Are post-migration checks defined (row counts, constraints, query plan)?
