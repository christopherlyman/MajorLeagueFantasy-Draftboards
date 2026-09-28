# MLF Production Operations

Last verified: September 28, 2026

## Purpose

This document records the production runtime and rollback procedure for the
MLF DraftBoard after the Next.js and FastAPI production cutover.

The architectural boundary is documented in `FASTAPI_APPLICATION_BOUNDARY.md`.
This file is the operational runbook.

## Production architecture

Production URL:

    https://mlf.majorleaguefantasy.app

Primary request path:

    Browser
      -> Caddy
      -> Next.js
      -> FastAPI where required
      -> PostgreSQL

PostgreSQL remains the canonical source of draft state and the authoritative
executor for draft mutations.

The browser and Next.js presentation layer do not own FA, QO, or POACH
classification.

## Production services

The production Compose/runtime services are:

- `mlf_caddy`
  - image: `caddy:2`
  - public reverse proxy
  - restart policy: `unless-stopped`
- `mlf_next`
  - image: `mlf-next:55462f6`
  - primary production presentation/runtime
  - no host port
  - restart policy: `unless-stopped`
- `mlf_api`
  - image: `mlf-fastapi:1e4ffe7`
  - authenticated application/mutation boundary
  - no host port
  - restart policy: `unless-stopped`
- `mlf_auth_bridge`
  - image: `mlf_tools:latest`
  - legacy `/auth/*` bridge
  - restart policy: `unless-stopped`
- `mlf_draftboard`
  - image: `mlf_tools:latest`
  - legacy Streamlit application
  - retained temporarily as rollback insurance
  - not the normal production route
- `mlf_postgres`
  - image: `postgres:16`
  - canonical database
  - restart policy: `unless-stopped`

All internal application services use the `mlf_net` Docker network.

## Current Caddy routing

The active MLF production site block is:

    mlf.majorleaguefantasy.app {
        handle /auth/* {
            reverse_proxy mlf_auth_bridge:8601
        }

        handle /gateway/* {
            reverse_proxy mlf_api:8000
        }

        handle {
            reverse_proxy mlf_next:3000
        }
    }

Routing responsibilities:

- `/auth/*` -> `mlf_auth_bridge:8601`
- `/gateway/*` -> `mlf_api:8000`
- all other production traffic -> `mlf_next:3000`
- Next.js `/api/mlf/*` route handlers transport appropriate requests to the
  internal FastAPI service.

FastAPI is not exposed through a host port.

## Caddyfile tracking model

`mlf/runtime/Caddyfile` is intentionally ignored by Git.

It is an operational runtime artifact, not a Git-managed deployment file.

Therefore:

- ordinary `git status` does not report Caddyfile changes;
- a Caddy change must be verified from its actual content;
- use a before/after SHA-256 when performing a controlled routing change;
- validate the resulting Caddyfile before reload;
- retain the exact previous file bytes until the new routing is proven.

At the successful Next.js production cutover on September 28, 2026, the
full-file SHA-256 was:

    0558dd8ec1b96ae580c3c68ab875ed3af73bab4812f6c8cb4ee381c7c2fb931c

That hash records the proven cutover state at that point in time. It is not a
permanent invariant because unrelated future Caddy configuration may
legitimately change the full-file hash.

## Normal production health checks

Next.js health:

    https://mlf.majorleaguefantasy.app/api/health

Expected:

- HTTP 200
- JSON response from the Next.js application

Production root:

    https://mlf.majorleaguefantasy.app/

Expected:

- HTTP 200
- MLF Draft Board rendered by Next.js

Public authentication boundary:

    https://mlf.majorleaguefantasy.app/api/mlf/auth/me

Expected without a manager cookie:

- HTTP 200
- `is_authenticated` is false
- role is `public`

While Next.js is the production root:

    https://mlf.majorleaguefantasy.app/_stcore/health

must not return the Streamlit `200 / ok` health response through the production
catch-all route.

## Successful cutover evidence

The September 28, 2026 production cutover proved:

- Next.js health: HTTP 200
- public production root: HTTP 200
- Available Players: 1,938 rendered player rows
- public auth boundary: HTTP 200
- authenticated manager identity: HTTP 200
- authenticated safe write transport: HTTP 409 `draft_conflict` because the
  2026 preseason draft was already complete
- database signature remained `400|256|144|24`
- gateway state remained `16|1|15|1`
- no production service was recreated by the Caddy-only cutover

These values are historical cutover evidence, not permanent future-season
health invariants.

## Streamlit rollback target

During the post-cutover stabilization period, `mlf_draftboard` remains running
as immediate rollback insurance.

The rollback MLF site block is:

    mlf.majorleaguefantasy.app {
        handle /auth/* {
            reverse_proxy mlf_auth_bridge:8601
        }

        handle {
            reverse_proxy mlf_draftboard:8501
        }
    }

A routing rollback is a Caddy-only operation.

Do not recreate PostgreSQL, FastAPI, Next.js, the auth bridge, or Streamlit
merely to roll production traffic back to Streamlit.

## Rollback procedure

1. Verify `mlf_draftboard` is already running.
2. Save the exact current `mlf/runtime/Caddyfile` bytes before editing it.
3. Replace only the `mlf.majorleaguefantasy.app` site block with the Streamlit
   rollback block above.
4. Verify no unrelated Caddy site block changed.
5. Validate the configuration:

       docker exec mlf_caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

6. Reload Caddy without recreating the container:

       docker exec mlf_caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile

7. Verify:

       https://mlf.majorleaguefantasy.app/_stcore/health

   Expected result:

       HTTP 200
       ok

8. Verify the public root renders the Streamlit MLF application.
9. Verify canonical PostgreSQL state was not mutated by the routing change.

## Return from rollback to Next.js

To restore the current primary production architecture:

1. verify `mlf_next` and `mlf_api` are already healthy;
2. replace only the MLF Caddy site block with the current Next.js block in this
   document;
3. validate Caddy;
4. reload Caddy;
5. prove `/api/health`, `/`, `/api/mlf/auth/me`, and an authenticated safe
   write path;
6. verify PostgreSQL and gateway state were not unexpectedly mutated.

Do not use a broad Compose restart as a substitute for a Caddy routing change.

## Service retirement policy

Do not remove `mlf_draftboard` during immediate post-cutover stabilization.

Once the Next.js production runtime has an acceptable stabilization period and
rollback requirements no longer justify keeping Streamlit alive:

1. verify no production Caddy route references `mlf_draftboard`;
2. verify no operational procedure still depends on it;
3. update this runbook;
4. retire the legacy Streamlit service deliberately.

The manually exposed historical Next.js preview container is also not part of
the permanent production architecture and may be retired separately after
production stabilization.

## Operational invariants

- PostgreSQL remains canonical.
- FastAPI remains the authenticated mutation boundary.
- Next.js does not reimplement draft legality or FA/QO/POACH classification.
- No wildcard public CORS is required for the same-origin production design.
- `mlf_next` and `mlf_api` expose no host ports.
- A Caddy-only route change must not recreate application or database
  containers.
- Secrets must not be copied into this document or into operational command
  output.
