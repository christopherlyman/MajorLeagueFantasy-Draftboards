# MLF FastAPI Application Boundary

Status: Locked initial API/application-boundary contract.

Frozen migration baseline entering this phase: 6901472.

## Purpose

Replace the Streamlit presentation/runtime boundary with Next.js plus FastAPI
while preserving PostgreSQL as canonical relational state and preserving the
existing authoritative Python/PostgreSQL mutation mechanics.

FastAPI is a thin service boundary. It must not become a second implementation
of MLF draft rules.

## Target architecture

Browser
  -> https://mlf.majorleaguefantasy.app
  -> Caddy
     -> /api/* to FastAPI
     -> /auth/* to existing auth bridge while required
     -> all other application traffic to Next.js

FastAPI
  -> existing Python domain/runtime wrappers
  -> canonical PostgreSQL relational state

This remains one MLF production application, not a second permanent site.

## Initial API surface

The initial scaffold exposes only:

- GET /health
- GET /auth/me

The first mutation added later is:

- POST /drafts/{draft_key}/picks

No destructive commissioner endpoint belongs in the initial scaffold.

## Health contract

GET /health proves:

- FastAPI is running;
- PostgreSQL connectivity works;
- no credentials, SQL text, stack traces, or secret material are disclosed.

Successful response fields are:

- status = ok
- database = ok

Required dependency failure returns HTTP 503 with a generic application error.

## Authentication: MLF Team Gateway

The rebuilt MLF DraftBoard uses the low-friction Team Gateway model already
proven by the NFFL and NFHL DraftBoards. The legacy username/password plus
public.auth_session flow is not the authentication contract for the rebuilt
MLF application.

Manager flow:

1. The commissioner distributes a private manager link containing an opaque
   team gateway token.
2. GET /gateway/claim validates that token for the active MLF league and season
   and resolves the canonical franchise/team from PostgreSQL.
3. A successful claim records claim metadata and an audit event.
4. FastAPI sets a signed browser cookie named mlf_team_gateway.
5. The cookie is Secure, HttpOnly, SameSite=Strict, Path=/, and valid for
   180 days.
6. The private team-link token is not stored in the identity cookie or audit
   text.
7. Future visits restore manager identity without a username/password prompt.

The signed cookie contains version, role=manager, league_key, season_year,
franchise_id, team_key, and issued_at_utc.

The cookie uses HMAC-SHA256 with MLF_GATEWAY_COOKIE_SECRET. There is no
development fallback secret. Missing signing configuration fails closed.

A manager cookie is accepted only when its signature, version, role, league,
and season are valid and PostgreSQL confirms that franchise_id, league_key,
season_year, and team_key still identify the same canonical team.

GET /auth/me is public and read-only. It returns either a canonical manager
principal or a public principal.

Commissioner authority is separate. The commissioner=1 query parameter is
presentation state only and must never authorize a FastAPI mutation.

Manager write authorization remains defense in depth:

1. FastAPI validates the signed gateway cookie and canonical team identity.
2. FastAPI requires that identity to match the expected owner/team.
3. Existing PostgreSQL/Python atomic mutation logic remains authoritative for
   draft ownership, active-pick state, concurrency, QO/POACH behavior,
   contracts, and all other draft invariants.

Initial gateway endpoints are GET /health, GET /auth/me, GET /gateway/claim,
and GET /gateway/clear. The gateway endpoints establish browser identity only;
they do not perform draft mutations.
## GET /auth/me

Missing, expired, revoked, unknown, or inactive sessions return HTTP 401.

The public principal response may contain only:

- user_id
- email
- is_site_admin
- must_change_password
- league_role
- franchise_id
- team_key
- team_name

It must never expose:

- password_hash
- session token
- database credentials
- raw SQL
- internal exception details

## Manager pick authorization

Future pick submission requires authentication.

The initial manager pick endpoint permits:

- an authenticated active manager whose resolved team_key equals the
  request expected_owner_team_key.

The browser cannot assert a different acting franchise. The resolved Team
Gateway principal is the authoritative API identity. Site-administrator and
commissioner mutation authority will be added only with the separate signed
commissioner mechanism before commissioner endpoints are exposed.

This API authorization check is defense-in-depth.

PostgreSQL remains authoritative for:

- current active pick;
- canonical pick ownership;
- contract/PT protection;
- QO/POACH classification;
- duplicate-player protection;
- concurrent submissions;
- QO ladder mutation;
- next-pick advancement;
- draft runtime state.

Those rules are not duplicated in FastAPI or TypeScript.

## Commissioner authorization

Commissioner authority is separate from manager Team Gateway identity.

The old Streamlit `?commissioner=1` parameter is presentation state only and
must never participate in FastAPI authorization.

The first rebuilt commissioner capability is the read-only manager-link
inventory. A private commissioner claim token from
`MLF_COMMISSIONER_GATEWAY_TOKEN` establishes a distinct signed
`mlf_commissioner_gateway` browser cookie. That cookie is Secure, HttpOnly,
SameSite=Strict, scoped to Path=/, tied to the active MLF league and season,
and expires after 30 days.

This initial commissioner credential authorizes read-only commissioner
endpoints only. It does not authorize draft, contract, trade, lottery, or other
commissioner mutations.

Before any commissioner mutation endpoint is exposed, mutation authority still
requires a separately proven user-bound authorization contract for an
authenticated site administrator or authenticated active league commissioner.

## Submit-pick mapping

Implemented endpoint:

POST /drafts/{draft_key}/picks

It delegates to the existing Python wrapper:

submit_draft_pick_atomic(...)

Inputs supplied by the API are:

- draft_key
- pick_id
- expected_owner_team_key
- yahoo_player_key
- optional expected_pick_kind
- deterministic selected_by actor/source

The selected_by value identifies the authenticated API actor. The initial
Team Gateway manager form is:

api:manager:<franchise_id>

The browser does not supply selected_by.

FastAPI does not recreate the PostgreSQL transaction or draft rules.

## Next.js browser transport

The browser does not connect directly to PostgreSQL and does not reimplement
draft rules in TypeScript.

The Next.js application exposes same-origin transport-only route handlers:

- GET /api/mlf/auth/me
- POST /api/mlf/drafts/{draft_key}/picks

Those handlers proxy to the internal FastAPI service at MLF_API_INTERNAL_URL,
which defaults inside the MLF Docker network to:

http://mlf_api:8000

The proxy may forward only transport/security context needed by FastAPI,
including the browser Cookie, Content-Type, Origin, and Referer headers.
Origin and Referer are forwarded unchanged so FastAPI remains authoritative
for the same-origin/CSRF decision.

The Next.js transport layer does not:

- decide manager authorization;
- classify FA/QO/POACH;
- decide whether a player is available;
- decide current-pick ownership;
- timestamp a pick;
- write PostgreSQL;
- duplicate the atomic draft transaction.

A successful browser pick still follows:

browser -> Next.js transport -> FastAPI -> existing Python wrapper ->
mlf.submit_draft_pick_atomic(...) -> PostgreSQL.

The private Team Gateway claim route remains a FastAPI route. Production
routing for /gateway/* is handled during the explicit Caddy cutover step.

## Timestamp semantics

The authoritative pick timestamp is:

mlf.draft_selection.selected_at_utc

It comes from the successful server/database transaction.

Browser time is never authoritative.

## Same-origin and CSRF policy

Production remains same-origin at:

https://mlf.majorleaguefantasy.app

Final production routing uses Caddy. FastAPI has no directly exposed public
port and there is no wildcard CORS policy.

The existing cookie remains:

- Secure
- HttpOnly
- SameSite=Strict

For unsafe browser methods such as POST, PUT, PATCH, and DELETE:

- endpoints accepting bodies require the appropriate JSON content type;
- Origin, when present, must match the MLF production origin;
- when Origin is absent, Referer must resolve to that same origin;
- absence of both is rejected for browser-facing unsafe requests unless a
  separately documented trusted non-browser mechanism is later required.

SameSite is an additional defense, not the complete CSRF policy.

## HTTP error contract

Expected meanings:

- 400 malformed application request
- 401 unauthenticated or invalid session
- 403 authenticated but unauthorized
- 404 unknown resource
- 405 unsupported method
- 409 canonical mutation conflict
- 422 request-schema validation failure
- 503 required dependency unavailable

Errors returned to clients never expose SQL, credentials, stack traces, or
secret material.

## Initial test strategy

Before exposing any mutation, prove:

1. source compiles;
2. FastAPI imports successfully;
3. route surface contains only /health and /auth/me;
4. /health proves PostgreSQL connectivity;
5. /auth/me without a cookie returns 401;
6. /auth/me with an invalid cookie returns 401;
7. /auth/me with a valid active session returns only approved public fields;
8. unsupported write methods on read-only endpoints return 405;
9. service runs internally on mlf_net;
10. FastAPI has no public host port;
11. canonical database signature remains 400|256|144|24;
12. existing production container identities/start times remain unchanged;
13. production URL remains healthy.

## Mutation-test isolation

The completed canonical 2026 draft must not be mutated for API testing.

Write tests require a provably isolated mechanism such as:

- transaction rollback;
- disposable test draft;
- controlled fixture data;
- another explicitly proven isolated strategy.

Production canonical rows are never experimental test data.

## Deferred mutation endpoints

Deferred until each exact existing Python contract is inspected and proven:

- draft clock
- set current pick
- delete/rewind selection
- reset draft
- pick ownership transfer
- draft-order rebase
- keeper-assignment rebuild
- prospect/PT controls
- contract controls
- trades

Before exposing trades, inspect apply_trade_assets_atomic directly.

## Cutover and rollback

Production cutover to Next.js completed on September 28, 2026 after
Next.js/FastAPI parity, authorization, failure handling, authenticated safe
write transport, zero-mutation, and rollback proofs passed.

Current production routing sends the MLF root to `mlf_next`, `/gateway/*` to
`mlf_api`, and `/auth/*` to `mlf_auth_bridge`.

The legacy Streamlit application remains running only as temporary rollback
insurance during post-cutover stabilization.

The live Caddyfile is intentionally ignored by Git. Current production routing,
health checks, and the exact Caddy-only rollback procedure are documented in
`PRODUCTION_OPERATIONS.md`.

## No zombie code

When replacing an execution path:

1. identify the exact current caller;
2. move callers deliberately;
3. prove the replacement;
4. prove the old path is unreferenced;
5. remove or archive the obsolete path when rollback requirements allow.

Parallel implementations are not retained just in case.
## Gateway Request Logging

The FastAPI runtime disables Uvicorn access logging because the private manager gateway token is carried on the /gateway/claim query string. The gateway token and signed browser cookie must never be emitted to request or application logs. Operational logging may record non-secret outcomes and canonical identity metadata only.

## Persistent Internal API Service

The FastAPI boundary runs as the Compose service `api` with container name `mlf_api`.

- It uses the dedicated FastAPI image and the existing ignored MLF `.env`.
- It joins only the internal `mlf_net` Docker network.
- It publishes no host port.
- Caddy routes `/gateway/*` directly to this service in production.
- Same-origin Next.js `/api/mlf/*` route handlers also use this internal service as the application boundary.
- The production root now routes to `mlf_next`; Streamlit is retained only as temporary rollback insurance.
- The temporary proof container `mlf_api_internal` is not part of the persistent architecture.
