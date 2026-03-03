# Date/Time Type Safety Checks

## Raw SQL to Domain Types
- For fields from `text(...)`/raw SQL rows, is there explicit normalization helper before any comparison?
- Are raw values converted to expected datetime type (timezone-safe) before SLA/TTL logic?
- Is `str` vs `datetime` comparison impossible by construction?

## Service Guards
- Are explicit type-guards present before date comparisons (`<`, `>`, `<=`, `>=`)?
- Is naive datetime handled deterministically (normalize or reject by policy)?
- Is `None` handled explicitly before comparisons?

## Comparator Safety
- Is comparison done against consistent `now` representation (aware/normalized)?
- Are helper and guard paths covered by negative tests (`str`, naive datetime, `None`)?
