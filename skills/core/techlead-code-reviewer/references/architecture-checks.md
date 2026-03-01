# Architecture Checks

## Layering and Boundaries
- Are module boundaries explicit and respected?
- Is business logic kept out of transport/controller layer?
- Are infrastructure concerns leaking into domain/service layer?

## Coupling and Cohesion
- Are dependencies minimized and intentional?
- Any cyclic dependency or hidden cross-module coupling?
- Are interfaces/contracts stable and narrow enough?

## Change Isolation
- Is the change localized or does it create broad blast radius?
- Are extension points used instead of invasive rewrites?
- Is rollback feasible without system-wide changes?
