# Fantasy Portfolio — Auth / Permissions Canonical

**Purpose:**  
This document defines the canonical truth model for:

- Local authentication
- User identity
- League-scoped role mapping
- Site admin vs commissioner access
- Persistent login
- Password reset / password change semantics
- Login rate-limit behavior
- Draft submission permission rules

It exists to help a new chat deterministically answer questions like:

- What is the canonical auth model?
- How are users mapped to teams?
- What is the difference between site admin and commissioner access?
- What is the authoritative login state?
- When can a manager submit a pick?
- How do password reset and self-change differ?
- What is the current rate-limit model?
- How does persistent login actually work?

If something conflicts:

> **DB truth → container runtime → application state → documentation**

This document is **not** a history log.

---

# 0) Scope

This canonical governs:

- local auth architecture
- auth tables and role mapping
- site admin semantics
- league-scoped manager / commissioner mapping
- session and persistent login behavior
- password hashing and password-change/reset semantics
- admin reset tooling rules
- login-attempt throttling
- authoritative write permission rules for draft submission
- auth bridge / handoff model for browser cookie persistence

This canonical does **not** define:

- full DraftBoard restore behavior
- full team/franchise identity architecture beyond what auth depends on
- commissioner-tools business workflows except where auth gating matters
- UI layout/styling
- deployment topology except where runtime auth depends on env/dependencies

It may reference those only where necessary to explain auth/permission truth.

---

# 1) Canonical Auth Direction

## 1.1 Single Local Auth Model [VERIFIED]

The accepted canonical product direction is:

```text
single canonical local auth model
```

Not canonical:

- parallel Google auth stacks
- parallel Microsoft / Azure auth stacks
- hybrid zombie auth systems layered on top of local auth

Canonical rule:

```text
all user auth features extend the existing local auth system
unless auth is deliberately redesigned
```

## 1.2 Auth Scope Model [VERIFIED]

The system distinguishes:

1. **Global user identity**
2. **League-scoped role mapping**
3. **Current-season team/team-name resolution through franchise mapping**
4. **Separate commissioner surface selection**
5. **Separate site-admin authority**

These must not be conflated.

---

# 2) Canonical Auth Data Model

## 2.1 Users Table [VERIFIED]

Canonical user table:

```sql
public.auth_user
```

Verified columns include:

- `user_id`
- `email_normalized`
- `password_hash`
- `active`
- `must_change_password`
- `created_at_utc`
- `last_login_at_utc`
- `is_site_admin`

Canonical rule:

```text
auth_user is the global identity store for local auth users
```

## 2.2 League Role Mapping Table [VERIFIED]

Canonical league-role mapping table:

```sql
public.auth_user_league_role
```

Verified columns include:

- `user_id`
- `league_key`
- `franchise_id`
- `role_code`
- `active`
- `created_at_utc`

Canonical rule:

```text
user identity is global
role mapping is league-scoped
```

## 2.3 Auth Session Table [VERIFIED]

Canonical long-lived session table:

```sql
public.auth_session
```

This supports session revocation and active session tracking.

Canonical rule:

```text
auth_session is the DB surface for revocable authenticated sessions
```

## 2.4 Login Attempt Table [VERIFIED]

Canonical login-attempt throttle table:

```sql
public.auth_login_attempt
```

Verified purpose:

- attempted email
- optional IP
- success/failure
- timestamped attempt history

Canonical rule:

```text
auth_login_attempt is the source of truth
for recent login-attempt history
```

## 2.5 Auth Handoff Table [VERIFIED]

Canonical one-time handoff table:

```sql
public.auth_handoff_code
```

Verified purpose:

- one-time browser auth handoff
- bridge-side redemption into a real auth cookie
- short-lived login transition artifact
- single-use consumption tracking

Verified columns include:

- `handoff_code`
- `session_token`
- `created_at_utc`
- `expires_at_utc`
- `consumed_at_utc`

Canonical rule:

```text
auth_handoff_code is a transient one-time login handoff surface,
not the long-lived session store
```

---

# 3) Stable Identity Mapping Rules

## 3.1 Manager-to-Team Mapping Must Not Be Direct Raw team_key Identity [VERIFIED]

The canonical mapping path is:

```text
auth_user
→ auth_user_league_role.franchise_id
→ public.franchise_season_team
→ current season team_key / team_name
```

Canonical rule:

```text
manager identity is league-scoped through franchise_id,
not permanently through raw team_key
```

## 3.2 Why franchise_id Matters [VERIFIED]

`franchise_id` is the stable linkage layer because it:

- survives season rollover better than season `team_key`
- preserves continuity
- avoids treating season-scoped team keys as permanent human identity

## 3.3 Human-Friendly Proof Surface [VERIFIED]

Human-readable proof should usually resolve through:

- `team_name`
- current season mapping
- league role row

But display labels are not identity themselves.

---

# 4) Permission Layers

## 4.1 Site Admin Is a Global Identity Permission [VERIFIED]

Site admin is represented by:

```text
auth_user.is_site_admin = true
```

Canonical rule:

```text
site admin is global
```

## 4.2 Commissioner Is a League-Scoped Role [VERIFIED]

Commissioner is a league-scoped role represented by:

```text
auth_user_league_role.role_code = 'commissioner'
```

Canonical rule:

```text
commissioner is league-scoped
site admin is global
```

## 4.3 Manager Is a League-Scoped Role [VERIFIED]

Ordinary league access should be represented by a league-scoped manager role rather than by direct team-key attachment.

Canonical rule:

```text
manager identity is role-based and franchise-linked,
not direct raw team_key identity
```

## 4.4 Site Admin vs Commissioner Boundary [VERIFIED]

Practical permission split:

### Site Admin
May access:

- commissioner tools
- admin-only auth/account controls
- admin password reset tooling

### Commissioner
May access:

- commissioner league-operation tools

May not access:

- admin-only auth/account controls
- admin password reset tooling unless also `is_site_admin = true`

Canonical rule:

```text
commissioner manages league operations
site admin manages auth/admin controls
```

---

# 5) Commissioner Surface Selection vs Authorization

## 5.1 Commissioner URL Is a Surface Selector [VERIFIED]

The commissioner surface may be selected by:

```text
?commissioner=1
```

Canonical rule:

```text
commissioner URL mode selects a surface
it does not grant permission by itself
```

## 5.2 Commissioner Access Is Identity-Based [VERIFIED]

Commissioner-tool access requires:

- commissioner surface selection
- authenticated local user
- and either:

  - `auth_user.is_site_admin = true`, or
  - `auth_user_league_role.role_code = 'commissioner'`

Canonical rule:

```text
commissioner access is identity-based through authenticated role/admin truth
not through a shared commissioner password gate alone
```

## 5.3 Site Admin Identity and Commissioner Surface Must Not Be Merged [VERIFIED]

A logged-in manager does not automatically gain commissioner tools.

A site-admin identity does not automatically make every commissioner surface concern identical to site-admin authority.

Canonical rule:

```text
surface selection, commissioner authorization,
and site-admin authority are separate concerns
```

---

# 6) Runtime Auth Architecture

## 6.1 App Responsibilities [VERIFIED]

The Streamlit app owns:

- credential verification
- principal loading
- rate-limit enforcement
- runtime auth context population
- session creation
- handoff-code creation
- auth restore from browser cookie
- logout session revocation

Canonical rule:

```text
the app owns credential verification, session truth,
and auth restore logic
```

## 6.2 Auth Bridge Responsibilities [VERIFIED]

A dedicated auth bridge owns:

- one-time handoff redemption
- top-level HTTP cookie set
- top-level HTTP cookie clear
- redirecting the browser back to the main app

Canonical rule:

```text
the auth bridge owns auth-cookie write/clear behavior
the app does not directly own browser auth-cookie persistence
```

## 6.3 Canonical Persistent Login Architecture [VERIFIED]

The canonical persistent-login flow is:

```text
credentials verified in app
→ create auth_session row
→ create one-time auth_handoff_code row
→ redirect browser to auth bridge
→ auth bridge redeems handoff code once
→ auth bridge sets mlf_auth cookie via real HTTP Set-Cookie
→ app later restores auth from browser cookie
```

Canonical rule:

```text
persistent auth cookie write/clear must occur through top-level HTTP response behavior,
not through a UI/component cookie helper abstraction
```

## 6.4 Canonical Browser Cookie Restore Surface [VERIFIED]

The canonical browser-cookie restore surface is:

```python
st.context.cookies
```

Canonical rule:

```text
runtime auth restore reads the browser cookie from st.context.cookies
```

## 6.5 Non-Canonical Cookie Write Paths [VERIFIED]

The following were investigated and rejected as canonical auth-persistence paths:

- `extra-streamlit-components.CookieManager` as the live auth-cookie write path
- custom Streamlit component / browser-JS cookie writing as the live auth-cookie write path

Canonical rule:

```text
component-based or helper-based cookie write paths are not canonical
for auth persistence unless deliberately re-proven
```

---

# 7) Runtime Auth Integration

## 7.1 Current Runtime Root [VERIFIED]

Current local auth implementation root is in:

```text
app/src/draftboard/ui/app.py
```

Key helper surfaces established in runtime include:

- `_clear_local_auth_session()`
- `_get_auth_context()`
- `_user_can_submit_pick(state)`
- `_load_local_auth_principal(...)`
- `_load_local_auth_principal_by_user_id(...)`
- `_render_local_auth_block()`

Canonical rule:

```text
local auth currently integrates through ui/app.py
while browser cookie write/clear lives in auth bridge behavior
```

## 7.2 Auth Restore Timing [VERIFIED]

Persistent auth restore occurs during runtime flow before major UI interaction depends on identity.

Conceptual runtime flow:

```text
render_app()
→ ensure_initialized()
→ get_state()
→ if not authenticated:
     read auth cookie
     load principal by user_id/session
     repopulate session auth context
→ render UI
```

Canonical rule:

```text
auth restore must occur before permission-sensitive UI/actions are trusted
```

---

# 8) Password Hashing Standard

## 8.1 Canonical Hashing Library [VERIFIED]

Canonical password hashing library:

```text
bcrypt==4.2.1
```

Canonical rule:

```text
password_hash uses bcrypt
```

## 8.2 Do Not Introduce Alternate Hash Paths [REQUIREMENT]

Do not introduce alternate password-hash implementations casually, such as:

- DB-native hashing paths
- unrelated libraries
- weaker one-off utilities
- zombie parallel password storage

Canonical rule:

```text
all password mutation paths should mirror the live bcrypt model
unless auth is deliberately redesigned
```

---

# 9) Login State Model

## 9.1 Session State Alone Is Not Sufficient [VERIFIED]

Session-only login is insufficient because login must survive:

- browser refresh
- tab close / reopen

Canonical rule:

```text
authoritative practical login state requires
both server-side session truth and persistent browser-cookie restore
```

## 9.2 Cookie-Based Persistence [VERIFIED]

Persistent login now depends on:

- `public.auth_session`
- `public.auth_handoff_code`
- auth bridge cookie set/clear
- browser cookie restore through `st.context.cookies`

## 9.3 Cookie Secret Requirement [VERIFIED]

Persistent login requires runtime env var:

```text
MLF_AUTH_COOKIE_SECRET
```

Canonical rule:

```text
persistent auth cookie does not work correctly
unless MLF_AUTH_COOKIE_SECRET is present in runtime env
```

## 9.4 Current Live Runtime Env Source for Existing Public Stack [VERIFIED]

For the current live public stack, auth runtime env truth is sourced from:

```text
/Volume1/Bots/fantasy/mlf/.env
```

and must be consumed consistently by both:

- `mlf_draftboard`
- `mlf_auth_bridge`

Canonical rule:

```text
app and auth bridge must read the same live cookie secret
from the same canonical env source
```

## 9.5 Canonical Persistent Login Truth [VERIFIED]

Final accepted behavior:

- login persists across refresh
- login persists across tab close / reopen
- logout clears session and cookie state
- logout remains sticky
- browser isolation between different managers is required behavior

Canonical rule:

```text
persistent login and browser isolation
are part of current canonical auth behavior
```

---

# 10) Password Semantics

## 10.1 must_change_password Flag [VERIFIED]

Canonical password-rollout flag:

```text
auth_user.must_change_password
```

This controls first-login / reset behavior.

## 10.2 Seeded Manager Rollout Model [VERIFIED]

Canonical rollout pattern:

```text
admin seeds account with temporary password
→ manager logs in
→ manager is forced to change password on first sign-in
```

## 10.3 Self Password Change vs Admin Reset [VERIFIED]

These are intentionally different mutation paths.

### Self-service password change

- verifies current password first
- updates bcrypt hash
- sets:

```text
must_change_password = false
```

### Admin reset

- rewrites bcrypt hash
- sets:

```text
must_change_password = true
```

Canonical rule:

```text
self-change clears must_change_password
admin reset restores must_change_password
```

## 10.4 Self Password Change Is Not Rollout Reset Path [VERIFIED]

Self-service password change must not be used as the canonical rollout/admin reset mechanism because it clears `must_change_password`.

---

# 11) Admin Password Reset Tool Rules

## 11.1 Architectural Placement [VERIFIED]

Admin reset tooling is implemented inside commissioner tools, with auth context passed from `ui.app.py`.

Implementation surface includes:

```text
app/src/draftboard/ui/components/commissioner_tools.py
```

## 11.2 Site-Admin Gating [VERIFIED]

The password reset tool is not available merely because commissioner tools are visible.

Canonical rule:

```text
admin password reset tooling requires site-admin identity permission
```

## 11.3 Canonical DB Mutation Semantics [VERIFIED]

Admin reset updates the existing row in `public.auth_user` by:

- replacing `password_hash`
- setting `must_change_password = true`

Canonical rule:

```text
admin reset always forces must_change_password = true
```

## 11.4 Temporary Password Handling [VERIFIED]

Temporary passwords may be displayed to the admin as an operational handoff artifact, but are not stored as canonical plaintext app state.

## 11.5 Session Revocation Rule [VERIFIED]

Password reset operations should revoke active sessions for the reset target users.

Canonical rule:

```text
reset should revoke active sessions for explicitly selected targets
```

---

# 12) Reusable Bulk / Targeted Reset Utility Rules

## 12.1 Reusable Utility Direction [VERIFIED]

Hard-coded one-off exclusion scripts were rejected.

Canonical reset-tool direction is:

```text
reusable reset utility
+ explicit input targets
+ exact-match selector resolution
+ canonical bcrypt hashing
+ must_change_password = true
+ scoped session revocation
```

## 12.2 Preferred Selector [VERIFIED]

Preferred operational selector:

```text
email_normalized
```

Supported selectors may also include:

- `team_key`
- exact `team_name`

But canonical preference is email.

## 12.3 League-Scoped Eligibility [VERIFIED]

League-scoped reset candidates must satisfy:

- `auth_user.active = true`
- active league-role mapping in the specified league

Canonical rule:

```text
league-scoped reset utilities must not implicitly touch orphan
or unrelated auth rows
```

## 12.4 Exact-Match / Fail-Closed Rule [VERIFIED]

Reset target resolution must:

- use exact matches only
- fail on no match
- fail on ambiguous match
- not use fuzzy matching

## 12.5 Dry-Run Rule [VERIFIED]

Bulk/targeted reset operations should support and use dry-run before real mutation.

## 12.6 Output Artifact Rule [VERIFIED]

Multi-user reset operations should emit:

- rollback artifact
- password handoff artifact
- receipt artifact

These are operational outputs, not canonical plaintext DB truth.

---

# 13) Permission Model for Draft Submission

## 13.1 Protected Write Surface [VERIFIED]

Current authoritative protected manager write surface is draft submission.

This includes desktop and mobile submission paths.

Commissioner tools remain separately gated.

## 13.2 Current Permission Function [VERIFIED]

Canonical runtime permission helper:

```python
_user_can_submit_pick(state)
```

## 13.3 Draft Submit Permission Rule [VERIFIED]

Current permission logic:

1. user must be authenticated
2. site admin is always allowed
3. otherwise authenticated manager may submit only when:

```text
auth.team_key == state.picks[state.clock.current_pick_id].owner_team_key
```

Canonical rule:

```text
only the authenticated on-clock owner may submit the current pick,
unless site admin overrides
```

## 13.4 Permission Must Be Enforced at the Authoritative Action Path [VERIFIED]

Permission must not rely only on display gating.

It must be enforced where the authoritative pick-submit action occurs.

---

# 14) Non-Authoritative UI Surfaces

## 14.1 Display-Only / Session-Only UI Is Not an Authoritative Write Surface [VERIFIED]

Session-only display surfaces are not automatically protected-write surfaces merely because they touch league-facing data.

Canonical classification rule:

```text
display-only session UI is not an authoritative persistent mutation surface
```

If a currently display-only feature later becomes persistent, auth rules must be re-evaluated at that time.

---

# 15) Login Rate-Limit Model

## 15.1 Canonical Storage [VERIFIED]

Login attempt history is stored in:

```sql
public.auth_login_attempt
```

This is the canonical source of truth for recent login-attempt history.

## 15.2 Current Effective Rate-Limit Key [VERIFIED]

Current MVP behavior is effectively email-based throttling because IP capture is not yet proven/stable in this runtime.

Canonical helper behavior conceptually returns no reliable IP.

Canonical rule:

```text
current effective rate-limit key = email_attempted
```

## 15.3 Threshold Rule [VERIFIED]

Current verified throttle rule:

```text
5 failed attempts within 10 minutes
→ block further login attempts
```

Business intent:

```text
light brute-force protection, not enterprise lockout
```

## 15.4 Flow Placement [VERIFIED]

Canonical login flow order:

```text
submit login
→ normalize email
→ check rate-limit
→ if blocked: stop and show lockout message
→ else continue auth lookup / password verification
```

Canonical rule:

```text
rate-limit check occurs before authentication work
and before failure logging
```

## 15.5 Attempt Recording Semantics [VERIFIED]

Canonical recording behavior:

- failed login → insert failed attempt row
- successful login → insert successful attempt row
- blocked attempt → insert no row

Canonical rule:

```text
blocked attempts must not inflate login-attempt history
```

---

# 16) Dependency / Runtime Requirements for Auth

## 16.1 Runtime Dependencies Matter [VERIFIED]

Important runtime dependencies include:

- `bcrypt`
- `extra-streamlit-components`

Canonical rule:

```text
dependency changes require image rebuild + container recreate,
not just source edit
```

## 16.2 Bind-Mount Reality [VERIFIED]

Because source is bind-mounted, code edits can affect runtime after restart/rerun, but dependency changes do not install automatically into the image.

This is operationally important when debugging auth features.

## 16.3 Current Live Runtime Auth Shape [VERIFIED]

For the current live public stack:

- app runtime remains league-local in deployment ownership
- auth bridge runtime remains league-local in deployment ownership
- browser auth-cookie write/clear is shared in behavior but proven through bridge runtime

Canonical rule:

```text
shared auth behavior does not by itself imply shared deployment ownership
```

---

# 17) Deterministic Verification Procedure

## 17.1 Minimum Questions

When auth or permissions look wrong, answer these first:

1. What does `public.auth_user` say?
2. What does `public.auth_user_league_role` say?
3. Is the user active?
4. Is the league-role mapping active?
5. Is identity being resolved through `franchise_id` correctly?
6. Is `is_site_admin` being confused with commissioner access?
7. Is `must_change_password` supposed to be true or false for this path?
8. Is the runtime env missing `MLF_AUTH_COOKIE_SECRET`?
9. Was `auth_session` created?
10. Was `auth_handoff_code` created and consumed?
11. Is the draft-submit permission being checked at the real action path?
12. For login throttling, is the email already rate-limited in `public.auth_login_attempt`?

## 17.2 Deterministic Debug Order [VERIFIED]

When auth/permission behavior looks wrong:

1. DB truth
2. imported runtime file certainty
3. runtime helper output / auth context
4. session / cookie / handoff state
5. UI rendering / visible controls

Never reverse this order.

---

# 18) Verify Pack

## 18.1 Account / mapping truth

```sql
select
  u.email_normalized,
  u.active,
  u.must_change_password,
  u.is_site_admin,
  r.league_key,
  r.franchise_id,
  r.role_code,
  fst.team_key,
  fst.team_name
from public.auth_user u
join public.auth_user_league_role r
  on r.user_id = u.user_id
left join public.franchise_season_team fst
  on fst.franchise_id = r.franchise_id
 and fst.league_key = r.league_key
 and fst.season_year = <season_year>
where r.league_key = '<league_key>'
order by r.franchise_id;
```

## 18.2 Runtime dependency truth

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import bcrypt
import extra_streamlit_components as stx
print("bcrypt OK", bcrypt.__version__)
print("extra_streamlit_components OK")
PY'
```

## 18.3 Cookie secret truth

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import os
v = os.environ.get("MLF_AUTH_COOKIE_SECRET", "")
print("SECRET_PRESENT:", bool(v))
print("SECRET_LEN:", len(v))
PY'

docker exec -i mlf_auth_bridge sh -lc 'python - << "PY"
import os
v = os.environ.get("MLF_AUTH_COOKIE_SECRET", "")
print("SECRET_PRESENT:", bool(v))
print("SECRET_LEN:", len(v))
PY'
```

## 18.4 Compile truth

```bash
docker exec -i mlf_draftboard bash -lc "python -m py_compile /app/app/src/draftboard/ui/app.py /app/app/src/draftboard/ui/components/commissioner_tools.py /app/scripts/reset_manager_passwords.py"
```

## 18.5 Rate-limit helper proof

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import draftboard.ui.app as m
email = "<test email>"
print("count_recent_failed =", m._count_recent_failed_login_attempts(email_attempted=email, ip_address=None, window_minutes=10))
print("is_rate_limited =", m._is_login_rate_limited(email_attempted=email, ip_address=None, max_failures=5, window_minutes=10))
PY'
```

## 18.6 Handoff proof

```sql
select
  handoff_code,
  session_token,
  created_at_utc,
  expires_at_utc,
  consumed_at_utc
from public.auth_handoff_code
order by created_at_utc desc
limit 10;
```

## 18.7 Session proof

```sql
select
  s.user_id,
  u.email_normalized,
  s.session_token,
  s.created_at_utc,
  s.expires_at_utc,
  s.revoked_at_utc
from public.auth_session s
join public.auth_user u
  on u.user_id = s.user_id
order by s.created_at_utc desc
limit 10;
```

## 18.8 Product behavior proof

Manually verify:

1. normal manager login succeeds
2. browser refresh keeps manager logged in
3. tab close / reopen keeps manager logged in
4. normal logout keeps manager logged out
5. two browsers can remain isolated as different managers
6. wrong-team manager cannot submit current pick
7. correct-team manager can submit current pick
8. site admin can always submit current pick

---

# 19) Critical Invariants (Do Not Break)

- the app uses one canonical local auth model unless deliberately redesigned
- `public.auth_user` is the global local-user identity store
- `public.auth_user_league_role` is league-scoped role mapping truth
- stable manager-to-team linkage must route through `franchise_id`
- site admin and commissioner are different permission layers
- commissioner surface selection and commissioner authorization are different concerns
- password hashes use bcrypt
- persistent login requires `MLF_AUTH_COOKIE_SECRET`
- long-lived session truth remains in `public.auth_session`
- one-time handoff truth lives in `public.auth_handoff_code`
- persistent auth restore reads from `st.context.cookies`
- auth-cookie write/clear must occur through top-level HTTP response behavior
- self password change verifies current password and clears `must_change_password`
- admin reset rewrites password and sets `must_change_password = true`
- reset tooling should use explicit scoped targets and fail-closed matching
- current protected manager write surface is draft submission
- only the authenticated on-clock owner may submit a pick, unless site admin overrides
- display-only/session-only UI is not automatically an authoritative persistent write surface
- login throttle truth is stored in `public.auth_login_attempt`
- current effective throttle is email-based
- blocked login attempts must not insert additional attempt rows
- dependency changes require image rebuild, not just source edit
- browser isolation between different managers is required behavior

---

# 20) Document Intent

This document exists to help a new chat:

- reason correctly about local auth truth
- separate site admin from commissioner access
- understand stable league/team mapping for permissions
- preserve password/reset semantics
- verify persistent login and rate-limit behavior deterministically
- enforce pick submission permissions at the real authoritative path
- distinguish shared auth behavior from current league-local deployment ownership

It intentionally does **not** try to document every commissioner workflow or every UI detail.

Those details should live in companion canonicals or league overlays such as:

- Team / Franchise Identity
- Draft State / Initialization / Restore
- Pick Ownership / Pick Trades / Draft Order
- UI Architecture
- Deployment / Infrastructure
- league-specific overlays for MLF or MiLF differences