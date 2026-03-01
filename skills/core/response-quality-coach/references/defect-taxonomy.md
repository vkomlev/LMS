# Defect Taxonomy For AI Responses

## Factual and Reasoning
- `FACT`: factual error or unsupported claim
- `LOGIC`: contradiction, broken reasoning chain
- `ASSUMPTION`: hidden assumption treated as fact

## Task Alignment
- `MISSED_INTENT`: answer does not solve user request
- `SCOPE_DRIFT`: unnecessary expansion beyond requested scope
- `FORMAT_MISMATCH`: ignores required output format or structure

## Communication Quality
- `CLARITY`: ambiguous or hard-to-follow wording
- `NOISE`: too verbose relative to requested depth
- `TONE`: tone mismatch with user preference/context
- `ACTIONABILITY`: lacks concrete next actions

## Process and Safety
- `UNVERIFIED`: presents unstable facts without verification
- `POLICY`: violates instruction hierarchy or project rules
- `RISK_OMISSION`: misses key risk for high-impact action

## Severity Guide
- `S1`: could cause incorrect high-impact decisions or unsafe changes
- `S2`: blocks efficient execution or likely causes rework
- `S3`: cosmetic/readability issue with low risk

