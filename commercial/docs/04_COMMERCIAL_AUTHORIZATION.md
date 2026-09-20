# Commercial League Authorization

Status: DESIGN CONTRACT
Scope: Commissioner Tools commercial application
Last updated: 2026-09-16

## 1. Purpose

Commissioner Tools requires an authorization boundary separate from legacy
team/franchise authorization.

Authentication answers:

    Who is the user?

Commercial league authorization answers:

    Which Commissioner Tools leagues may this user administer?

Provider authorization must rely on both.

## 2. Existing Identity

`public.auth_user.user_id` remains the canonical user identity.

The commercial application must not create a second user identity system.

Existing `public.auth_session` remains the canonical session source.

A valid commercial API request derives `user_id` from an authenticated session.
The browser must never choose or submit the owning `user_id` for authorization.

## 3. Legacy League Roles

`public.auth_user_league_role` remains legacy/shared infrastructure.

It currently requires:
- `user_id`;
- `league_key`;
- `franchise_id`;
- `role_code`.

Because `franchise_id` is mandatory, it is not the canonical authorization model
for a newly created Commissioner Tools league.

Commissioner authorization must be able to exist before:
- franchises are initialized;
- provider teams are imported;
- managers are created;
- a draft is run.

The legacy table must remain unchanged by the initial commercial authorization
implementation.

## 4. Commercial League User Role

Commissioner Tools will use:

    public.commercial_league_user_role

A row grants one authenticated user a role for one commercial league season.

Required fields:

- user_id;
- league_key;
- season_year;
- role_code;
- active;
- created_at_utc;
- updated_at_utc.

The commercial league identity is:

    (league_key, season_year)

and must reference `public.league_profile`.

The user identity must reference `public.auth_user`.

## 5. Initial Role Model

The first supported commercial role is:

    commissioner

A commissioner may administer the corresponding Commissioner Tools league.

Initial implementation does not require:
- manager access;
- read-only users;
- billing roles;
- organization roles;
- fine-grained permissions.

Those may be added later if product requirements justify them.

The initial schema should remain bounded rather than implement a general
permission engine.

## 6. Creator Authorization

Authenticated league creation must assign the creating user the active
`commissioner` role in the same logical operation as league creation.

A successfully created commercial league must not be left without an
authorized commissioner.

If role assignment fails, league creation must fail rather than silently create
an administratively orphaned league.

## 7. League Administration Rule

For protected Commissioner Tools operations, authorization requires:

1. a valid, non-revoked, non-expired authenticated session;
2. an active `auth_user`;
3. an active `commercial_league_user_role`;
4. matching `league_key` and `season_year`;
5. an allowed role for the requested operation.

For the initial implementation, protected league-administration operations
require:

    role_code = 'commissioner'

## 8. Provider Authorization

Provider connection ownership and league authorization are independent checks.

A provider operation may proceed only when:
- the authenticated user owns the referenced `provider_connection`; and
- the authenticated user is an active commissioner for the target commercial
  league.

The client must not supply a different `user_id` to override either check.

This prevents a valid provider connection from being attached to another
user's commercial league.

## 9. Commercial API Principal

The commercial FastAPI application will resolve the authenticated principal
from the existing session model.

Initial session flow:

    mlf_auth cookie
        ->
    auth_session.session_token
        ->
    valid, unexpired, non-revoked auth_session
        ->
    active auth_user
        ->
    authenticated commercial principal

The API should expose the resolved `user_id` internally to repository/service
operations.

Raw session tokens must not be returned in normal API responses or logs.

## 10. Next.js Proxy Boundary

Commercial Next.js API proxy routes must forward the authentication cookie
needed by FastAPI.

The proxy must not:
- manufacture a user identity;
- transform a browser-supplied user ID into authorization;
- log session-token contents.

FastAPI remains authoritative for session validation and league authorization.

## 11. Existing Commercial TEST League

The existing commercial league:

    commercial.6bfaff22ede94b19abb11e9c2d6913a2
    season 2027

predates the commercial authorization table.

It must not be assigned to an arbitrary active user.

A controlled one-time commissioner assignment may be performed after the
correct authenticated user identity is explicitly established.

The league does not need to be recreated.

## 12. Authorization Repository Boundary

Commercial authorization persistence should provide narrowly scoped operations
such as:

- grant commissioner role;
- load a user's role for a league season;
- determine whether a user may administer a league;
- deactivate a role if later required.

Repository calls should accept a server-resolved `user_id`.

Browser/API request bodies must not be treated as authoritative ownership
identity.

## 13. Transaction Boundary

League creation and creator-role assignment should eventually be atomic.

The implementation may accomplish this through:
- one repository transaction; or
- a service-layer transaction that coordinates profile creation and role
  creation.

The invariant is more important than the exact function boundary:

    no successful new commercial league without its creator authorization.

## 14. Database Migration Direction

Migration `004_commercial_league_user_role.sql` should create only the minimum
commercial authorization schema.

Expected constraints:

- foreign key `user_id -> auth_user.user_id`;
- foreign key `(league_key, season_year) -> league_profile`;
- unique role identity for a user / league season / role;
- bounded initial `role_code`;
- active-state support;
- indexes for user-to-league and league-to-user authorization lookup.

The migration must:
- be additive;
- preserve `auth_user_league_role`;
- preserve all existing commercial profiles;
- create no role rows automatically;
- be idempotent;
- pass a forced-rollback dry run before live application.

## 15. Initial Implementation Scope

The first commercial authorization vertical slice is:

1. create commercial role schema;
2. add commercial authorization repository;
3. resolve authenticated FastAPI principal from `mlf_auth`;
4. require commissioner authorization for protected commercial league actions;
5. make new league creation assign the creator as commissioner;
6. forward the auth cookie through Next.js;
7. explicitly assign the existing TEST League only after identifying the
   correct authenticated user;
8. expose provider connection/binding API operations only after these guards
   exist.

## 16. Out of Scope

Not required in this slice:
- OAuth credential persistence;
- manager self-service access;
- organization accounts;
- role invitations;
- billing permissions;
- granular permission matrices;
- provider-specific authorization rules beyond connection ownership;
- public multi-user league collaboration.

## 17. Design Decision

Commercial league authorization is independent of:
- franchise ownership;
- provider account ownership;
- league rules;
- provider credentials.

The canonical commercial administration relationship is:

    auth_user
        |
        +-- commercial_league_user_role
                |
                +-- league_profile

Provider operations then require both:

    authenticated commissioner authorization
    +
    provider_connection ownership
