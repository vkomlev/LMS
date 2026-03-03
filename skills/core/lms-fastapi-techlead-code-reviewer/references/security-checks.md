# Security Checks

## Input and Access Control
- Are inputs validated and normalized at boundaries?
- Is authorization enforced for sensitive operations?
- Are trust boundaries explicit across layers?

## Data Protection
- Are secrets never hardcoded or logged?
- Is sensitive data exposure prevented in API responses/errors?

## Abuse and Misuse
- Any injection, deserialization, SSRF, or traversal risk introduced?
- Are rate/abuse controls considered for public endpoints?

## Supply and Configuration
- Any unsafe defaults or debug settings left enabled?
- Are dependency updates introducing known risk patterns?
