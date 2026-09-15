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

## Authentication

Authentication continues to use the existing HttpOnly mlf_auth cookie.

The cookie contains an opaque session token. FastAPI never trusts cookie
contents as identity claims.

Principal resolution is:

1. read the mlf_auth cookie;
2. match public.auth_session.session_token;
3. require revoked_at_utc IS NULL;
4. require expires_at_utc > now();
5. join public.auth_user;
6. require the user to be active;
7. load active league role information;
8. resolve franchise/team mapping when applicable.

FastAPI does not use Streamlit session state.

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

The API authorization layer permits:

- an authenticated site administrator; or
- an authenticated active manager whose resolved team_key equals the
  request expected_owner_team_key.

Commissioner role alone does not authorize an ordinary manager pick.

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

Future commissioner endpoints require:

- authenticated site administrator; or
- authenticated active league commissioner.

The old Streamlit ?commissioner=1 parameter is only a presentation gate and
must never participate in API authorization.

## Submit-pick mapping

Future endpoint:

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

The selected_by value identifies the authenticated API actor, for example:

api:user:<user_id>

FastAPI does not recreate the PostgreSQL transaction or draft rules.

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

No production Caddy routing changes occur during API scaffolding.

Before production cutover:

- Next.js/FastAPI parity passes;
- authorization behavior passes;
- concurrency and duplicate behavior pass;
- failure behavior passes;
- rollback procedure is proven.

The current Streamlit application remains the rollback target until the new
production architecture is fully proven.

## No zombie code

When replacing an execution path:

1. identify the exact current caller;
2. move callers deliberately;
3. prove the replacement;
4. prove the old path is unreferenced;
5. remove or archive the obsolete path when rollback requirements allow.

Parallel implementations are not retained just in case.