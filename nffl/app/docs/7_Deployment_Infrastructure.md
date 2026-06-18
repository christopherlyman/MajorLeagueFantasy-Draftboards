# Fantasy Portfolio — Shared Deployment / Infrastructure Canonical

**Purpose:**  
This document defines the canonical truth model for the **shared deployment and infrastructure architecture** of the fantasy portfolio.

It exists to help a new chat deterministically answer questions like:

- What is the canonical deployment entrypoint?
- What containers exist and what are their roles?
- How does app-to-DB connectivity work?
- What is the public access path?
- What is host-published vs internal-only?
- When do code changes require rebuild vs just rerun/restart?
- What deployment assumptions are no longer canonical?
- Which infrastructure truths are shared patterns versus current league-local ownership facts?

If something conflicts:

> **DB truth → container runtime → application state → documentation**

This document is **not** a history log.

---

# 0) Scope

This canonical governs:

- runtime container topology
- canonical deployment file/location
- Docker network model
- app-to-DB connectivity path
- reverse proxy / HTTPS path
- public hostname routing
- host port exposure rules
- image/build truth
- dependency-install reality
- environment-variable runtime expectations
- auth-bridge routing/runtime infrastructure
- deterministic operational verification procedures

This canonical does **not** define:

- full DraftBoard business logic
- draft-state restore logic
- team/franchise identity design
- auth/permissions business rules except where runtime env and routing matter
- UI structure except where public routing matters
- league-specific business rules such as contracts / QO / PT

It may reference those areas only where necessary to explain deployment truth.

---

# 1) Deployment Model Overview

## 1.1 Unified Docker Deployment Is Canonical [VERIFIED]

The system uses a **unified Docker deployment** as the canonical deployment model.

Canonical rule:

```text
deployment truth comes from the unified stack definition,
not from older split launcher habits
```

## 1.2 Older Split Control Paths Are Non-Canonical [VERIFIED]

Earlier deployment control was split across:

- a shell script for the app container
- a separate compose-managed Postgres stack

That split model is no longer the canonical deployment architecture.

Canonical rule:

```text
do not treat older split launch paths as authoritative deployment truth unless re-verified
```

---

# 2) Canonical Deployment SSOT

## 2.1 Current Live Deployment SSOT [VERIFIED]

The **current live deployment SSOT** for the public stack is:

```text
/Volume1/Bots/fantasy/mlf/runtime/docker-compose.yml
```

Canonical rule:

```text
runtime/docker-compose.yml is the current live deployment source of truth
for the existing public stack
```

## 2.2 Current Live Runtime Env SSOT [VERIFIED]

The **current live runtime env SSOT** is:

```text
/Volume1/Bots/fantasy/mlf/.env
```

This is part of the live deployment contract for the currently deployed services.

Canonical rule:

```text
current live runtime env truth comes from root .env
not from legacy split env helpers
```

## 2.3 Shared Ownership vs Current Deployment Ownership [VERIFIED]

This document is **shared-owned**, but current deployment ownership is still **league-local**.

Current live deployment ownership remains under:

- `mlf/runtime/docker-compose.yml`
- `mlf/.env`

Canonical rule:

```text
shared canonical ownership does not by itself imply shared deployment ownership
```

## 2.4 Archived Env/Deploy Helpers Are Historical Only [VERIFIED]

Archived env/deploy helpers may document prior operational history, but they are not current deployment truth.

Examples include historical artifacts such as:

- legacy deploy shell scripts
- retired split env files
- retired draft-specific compose helpers

Canonical rule:

```text
archived env/deploy helpers are historical only,
not current deployment truth
```

---

# 3) Runtime Container Topology

## 3.1 Core Runtime Services [VERIFIED]

The canonical live stack currently contains four core services:

- `draftboard`
- `postgres`
- `caddy`
- `auth_bridge`

Observed runtime container names include:

- `mlf_draftboard`
- `mlf_postgres`
- `mlf_caddy`
- `mlf_auth_bridge`

## 3.2 Service Roles [VERIFIED]

### DraftBoard app service

Responsible for:

- Streamlit runtime
- app code execution
- UI rendering
- application-level DB access

### Postgres service

Responsible for:

- canonical DB storage
- persisted state
- contracts/auth/session/login-attempt data
- DraftBoard persistence surfaces

### Caddy service

Responsible for:

- reverse proxying
- TLS termination
- public hostname routing
- automatic HTTPS certificate management

### Auth bridge service

Responsible for:

- redeeming one-time auth handoff codes
- emitting real HTTP `Set-Cookie` headers for `mlf_auth`
- emitting real cookie-clear headers on logout
- redirecting the browser back to the main app

Canonical rule:

```text
public access flows through Caddy,
and auth-cookie write/clear flows through auth_bridge
rather than direct Streamlit header control
```

---

# 4) Docker Network Model

## 4.1 Shared Internal Docker Network Is Canonical [VERIFIED]

The unified deployment uses a shared internal Docker bridge network.

Canonical rule:

```text
draftboard, postgres, caddy, and auth_bridge communicate
over internal Docker networking
```

## 4.2 Internal Service Name Resolution Matters [VERIFIED]

The app and DB are expected to resolve each other through service/container naming on the shared network, not through NAS-host loopback assumptions.

Canonical rule:

```text
internal service-name connectivity is the canonical app-to-db path
```

## 4.3 App-to-DB Connectivity Must Not Revert to Host-IP by Default [VERIFIED/REQUIREMENT]

The prior model used the NAS host IP for DB connectivity. That is no longer the intended architecture when unified internal Docker networking is functioning.

Canonical rule:

```text
do not revert app db connectivity back to NAS host IP
if internal Docker networking is healthy
```

---

# 5) App-to-DB Connectivity Contract

## 5.1 Canonical DSN Host [VERIFIED]

Inside the unified stack, the canonical Postgres host is:

```text
postgres
```

Example DSN shape:

```text
postgresql://mlf:<password>@postgres:5432/mlf
```

Canonical rule:

```text
inside the unified stack, db host = postgres
```

## 5.2 Internal DB Path Is Canonical [VERIFIED]

The intended app-to-DB path is:

```text
draftboard container → postgres service name → internal Docker network
```

Not:

```text
draftboard container → NAS host IP → host-published Postgres port
```

## 5.3 DB Connectivity Questions Must Be Proven from Inside the App Container [REQUIREMENT]

If DB access is in question, proof should come from container runtime inspection, not host-level assumption.

Canonical rule:

```text
prove live db host/path from inside the running app container
```

---

# 6) Port Exposure Model

## 6.1 Postgres Is Internal-Only [VERIFIED]

Postgres is not supposed to be host-published for normal canonical operation.

Observed canonical runtime shape:

```text
5432/tcp
```

and not:

```text
0.0.0.0:5432->5432/tcp
```

Canonical rule:

```text
postgres should remain internal-only unless there is a deliberate architecture change
```

## 6.2 DraftBoard App Host Port [VERIFIED]

The DraftBoard app is still host-published on port `8501` at runtime.

This is an operational/runtime detail, but it is not the intended league-facing public URL.

Canonical rule:

```text
raw :8501 is not the intended public product entrypoint
once reverse proxy + https exists
```

## 6.3 Caddy Host Port Publishing [VERIFIED]

Because the NAS uses host ports 80 and 443 directly, Caddy is host-published using alternative host ports:

- `8080 -> 80`
- `8443 -> 443`

Canonical rule:

```text
caddy receives traffic on mapped host ports
because the NAS occupies native 80/443
```

## 6.4 Auth Bridge Is Internal-Only [VERIFIED]

`auth_bridge` is an internal service behind Caddy.

Canonical rule:

```text
auth_bridge is not a separate public-facing host
```

---

# 7) NAS Host Port Constraint

## 7.1 TerraMaster Owns Host 80/443 [VERIFIED]

The NAS itself listens on host ports:

- `80`
- `443`

Therefore Caddy cannot directly bind host `80/443` in the current design.

## 7.2 Architectural Consequence [VERIFIED]

Because of this host-port conflict, router forwarding must map:

```text
external 80  → NAS 8080
external 443 → NAS 8443
```

Canonical rule:

```text
public http/https success depends on router forwarding
into caddy’s mapped host ports
```

## 7.3 Do Not Change This Casually [REQUIREMENT]

Do not bind Caddy directly to host `80/443` unless the NAS web-port strategy is intentionally redesigned and re-proven.

---

# 8) Reverse Proxy / HTTPS Architecture

## 8.1 Caddy Is the Canonical Reverse Proxy [VERIFIED]

Canonical reverse proxy service:

```text
mlf_caddy
```

Canonical config file currently used by the live stack:

```text
/Volume1/Bots/fantasy/mlf/runtime/Caddyfile
```

Canonical rule:

```text
Caddy owns public reverse proxying and TLS termination
```

## 8.2 Public Routing Path [VERIFIED]

Canonical league-facing routing path is:

```text
public hostname
→ router forwarding
→ NAS mapped host ports
→ Caddy
→ draftboard upstream
→ internal Docker network / app
```

Canonical rule:

```text
DNS → router → Caddy → Streamlit app is the public request path
```

## 8.3 Auth Routing Branch [VERIFIED]

Caddy also owns a distinct auth routing branch:

```text
public hostname
→ router forwarding
→ NAS mapped host ports
→ Caddy
   → /auth/*         → mlf_auth_bridge:8601
   → everything else → app upstream
```

Canonical rule:

```text
/auth/* is a distinct routed surface owned by auth_bridge
```

## 8.4 HTTPS Certificate Management [VERIFIED]

Caddy is also the canonical TLS certificate manager for this stack.

Canonical rule:

```text
registrar/dns and TLS termination are separate concerns
registrar handles domain/dns
caddy handles certificates and https
```

## 8.5 Registrar-Provided SSL Is Not Canonical for This Stack [VERIFIED]

Even if the registrar offers SSL workflows, they are not the canonical cert-management layer here.

Caddy automatic HTTPS is the canonical solution.

---

# 9) Public Hostname Strategy

## 9.1 Canonical Domain Pattern [VERIFIED]

The chosen domain is:

```text
majorleaguefantasy.app
```

Canonical league hostname pattern includes league-specific subdomains, such as:

```text
mlf.majorleaguefantasy.app
milf.majorleaguefantasy.app
```

Canonical rule:

```text
one hostname per league is the preferred scaling model
```

## 9.2 Current Live Public URLs [VERIFIED]

Current live public URL examples include:

```text
https://mlf.majorleaguefantasy.app
https://milf.majorleaguefantasy.app
```

Canonical rule:

```text
league-facing access should use the https hostname,
not raw host:8501
```

## 9.3 `.app` Domain Implication [VERIFIED]

Because `.app` domains require HTTPS behavior in browsers, the architecture must preserve working TLS termination through Caddy.

---

# 10) Image / Build Truth

## 10.1 Canonical Build Inputs for Current Live Stack [VERIFIED]

Current live canonical Docker build file:

```text
/Volume1/Bots/fantasy/mlf/Dockerfile
```

Current live canonical dependency file:

```text
/Volume1/Bots/fantasy/mlf/requirements.txt
```

Canonical runtime image tag observed:

```text
mlf_tools:latest
```

## 10.2 Compose Uses an Image, Not Live Dependency Installation [VERIFIED]

The DraftBoard service uses a built image and bind-mounts project source into `/app`.

Canonical implication:

- source code edits can affect runtime via bind mount
- dependency edits do **not** install automatically into the running image

Canonical rule:

```text
source changes can ride the bind mount
dependency changes require image rebuild + container recreate
```

## 10.3 Runtime Image and Source Must Stay Aligned [VERIFIED/REQUIREMENT]

If code changes rely on new Python packages or changed system dependencies, the image must be rebuilt and the relevant containers recreated.

Do not assume a mounted `requirements.txt` file updates the live container environment by itself.

---

# 11) Bind-Mount Runtime Model

## 11.1 Canonical App Filesystem Context [VERIFIED]

The current live project root is bind-mounted into the app container at:

```text
/app
```

Canonical rule:

```text
the current app container executes against bind-mounted project source under /app
```

## 11.2 Operational Implication [VERIFIED]

This means:

- many code edits become active on rerun/restart because source is mounted live
- image-level dependency changes still require rebuild/recreate

This is a critical debugging distinction.

## 11.3 Shared Auth Bridge Execution Path [VERIFIED]

The auth bridge no longer depends on a fragile file-overlay mount onto `/app/runtime/auth_bridge.py`.

The current live bridge executes directly from a shared path:

```text
python /shared/runtime/auth_bridge.py
```

with live mounts:

```text
/Volume1/Bots/fantasy/mlf    -> /app
/Volume1/Bots/fantasy/shared -> /shared
```

Canonical rule:

```text
prefer direct shared-path execution over nested file-overlay mounts
for extracted shared runtime helpers
```

---

# 12) Runtime Dependency / Environment Rules

## 12.1 Environment Variables Matter at Runtime [VERIFIED]

Runtime behavior depends on container env, not just source code assumptions.

Examples include:

- DB DSN settings
- cookie/auth secrets
- commissioner password
- any other env-driven runtime contract

Canonical rule:

```text
when behavior depends on env, prove the env inside the running container
```

## 12.2 Current Compose Env Contract [VERIFIED]

The live compose stack points services at the canonical root env file.

In particular, the currently deployed `draftboard` and `auth_bridge` services consume:

```text
/Volume1/Bots/fantasy/mlf/.env
```

This is part of the live deployment contract.

## 12.3 Dependency Drift Must Be Proven from Runtime [REQUIREMENT]

If an import fails or a package seems missing, verify the running container’s installed environment rather than assuming the source tree reflects runtime dependency state.

## 12.4 Auth Cookie Secret Is a Required Shared Runtime Secret [VERIFIED]

Persistent auth depends on:

```text
MLF_AUTH_COOKIE_SECRET
```

This secret must be present in the canonical runtime env used by both:

- `mlf_draftboard`
- `mlf_auth_bridge`

Canonical rule:

```text
persistent auth requires the same cookie secret to be injected
into both app and auth bridge from the canonical env source
```

---

# 13) Python DB Driver Standard

## 13.1 Canonical Python DB Driver [VERIFIED]

The current canonical Python DB driver standard is:

```text
psycopg v3
```

using:

```text
psycopg[binary]
```

Canonical rule:

```text
current db-driver standard = psycopg, not psycopg2
```

## 13.2 Do Not Reintroduce Legacy Driver Usage Casually [REQUIREMENT]

Do not reintroduce `psycopg2` in live code while the image/runtime standard is `psycopg` unless there is a deliberate and re-proven architecture change.

---

# 14) Operational Command Reality on This NAS

## 14.1 Working Compose Command [VERIFIED]

The working command on this NAS is:

```text
docker-compose
```

not necessarily:

```text
docker compose
```

Canonical rule:

```text
prefer the proven compose command on this host
unless runtime tooling changes are re-verified
```

---

# 15) Auth Bridge / Cookie Infrastructure

## 15.1 Auth Bridge Is Part of the Canonical Unified Stack [VERIFIED]

The canonical live stack includes:

- `draftboard`
- `postgres`
- `caddy`
- `auth_bridge`

Canonical rule:

```text
auth_bridge is part of the canonical unified stack
```

## 15.2 Browser Auth-Cookie Write/Clear Is Infrastructure Behavior [VERIFIED]

Real browser auth-cookie write/clear depends on real top-level HTTP response headers coming through the reverse-proxied auth bridge path.

Canonical rule:

```text
browser auth-cookie write/clear must be proven through
route truth + container truth + response-header truth
```

## 15.3 Auth Bridge Runtime Source / Ownership Split [VERIFIED]

Current live truth is:

- source code = `shared/runtime/auth_bridge.py`
- deployment SSOT = `mlf/runtime/docker-compose.yml`
- runtime env SSOT = `mlf/.env`

Canonical rule:

```text
the auth bridge is shared in code location,
but still league-local in deployment ownership
until a later deployment extraction phase
```

## 15.4 Auth Persistence Debugging Rule [VERIFIED/REQUIREMENT]

When auth persistence or auth routing looks wrong, answer these first:

1. Does the compose file define `auth_bridge`?
2. Is `mlf_auth_bridge` actually running?
3. Does Caddy route `/auth/*` to `mlf_auth_bridge:8601`?
4. Does the bridge container have the required env contract?
5. Does the bridge return the expected `Set-Cookie` header?
6. Was the bridge actually recreated after the relevant source/config change?
7. Is the browser reaching the bridge path through the public hostname?

Canonical rule:

```text
auth persistence issues must be debugged through
route truth + container truth + header truth,
not app UI alone
```

---

# 16) Restart-Window Behavior

## 16.1 Restart-Window Proxy Errors Are Not Alone Deployment Failure [VERIFIED/OBSERVED]

During coordinated restarts, Caddy may briefly log:

- connection refused to app upstream
- temporary upstream DNS lookup failure
- transient 502 responses

while app services are stopping or rejoining the Docker network.

Canonical rule:

```text
transient reverse-proxy errors during coordinated restart windows
are not by themselves proof of broken steady-state deployment
```

Steady-state truth must be re-proven from:

- `docker ps`
- post-restart app availability
- current container logs
- repeated public-route checks after warm-up

---

# 17) Deterministic Verification Procedure

## 17.1 Minimum Questions

When deployment or infrastructure behavior looks wrong, answer these first:

1. What does `runtime/docker-compose.yml` define?
2. What containers are actually running?
3. What ports are actually published?
4. Is Postgres internal-only or accidentally re-exposed?
5. What DSN host is the app actually using?
6. Are app and DB on the expected internal network path?
7. Is the runtime image rebuilt for the dependency/source assumptions being made?
8. Is public access going through Caddy and the expected hostname?
9. Are router forwarding assumptions aligned with the NAS 80/443 constraint?
10. Is the env inside the running container what the app expects?
11. If auth is involved, is `/auth/*` routed to a healthy `auth_bridge`?

## 17.2 Deterministic Debug Order [VERIFIED]

When deployment behavior looks wrong:

1. deployment file / compose truth
2. live container runtime truth
3. live ports/network truth
4. env / dependency truth inside container
5. app behavior through the public routing path

Never reverse this order.

---

# 18) Verify Pack

## 18.1 Live container / port state

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Expected core truths include:

- app container published on `8501`
- postgres internal-only `5432/tcp`
- caddy published on `8080->80` and `8443->443`
- auth bridge present on the internal network

## 18.2 Live app image identity

```bash
docker inspect mlf_draftboard --format '{{.Image}} {{.State.Status}} {{.State.Running}}'
```

Use this to confirm the running app container and image identity.

## 18.3 DNS proof

```bash
dig mlf.majorleaguefantasy.app +short
```

Use this to prove hostname resolution.

## 18.4 Caddy logs

```bash
docker logs --tail 100 mlf_caddy
```

Use this to confirm:

- certificate issuance
- serving config
- proxy behavior
- TLS behavior

## 18.5 Runtime DB host/env proof

Example pattern from inside the app container:

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import os
print(os.environ.get("MLF_POSTGRES_DSN", ""))
PY'
```

Use this to verify the live DSN contract.

## 18.6 Runtime dependency/import proof

Example pattern:

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import psycopg
print("psycopg OK")
PY'
```

Use equivalent checks for any dependency or runtime import claim.

## 18.7 Bridge route response proof

```bash
curl -k -s -D - -o /dev/null 'https://mlf.majorleaguefantasy.app/auth/clear?next=/'
curl -k -s -D - -o /dev/null 'https://milf.majorleaguefantasy.app/auth/clear?next=/'
```

Use this to verify live `Set-Cookie` and redirect behavior through Caddy + auth bridge.

## 18.8 Live bridge command/mount proof

```bash
docker inspect mlf_auth_bridge --format '{{json .Config.Cmd}}'
docker inspect mlf_auth_bridge --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
```

Use this to verify the bridge is executing from the intended shared path.

---

# 19) Portfolio Direction and Current Boundaries

## 19.1 Current Live League Root vs Future Portfolio Root [REQUIREMENT]

Current live league-local deployment root remains:

```text
/Volume1/Bots/fantasy/mlf
```

But forward-looking architecture should distinguish:

- `fantasy/` as a portfolio root
- `mlf/`, `milf/`, and `us/` as league-specific roots

This is a target-architecture direction, not a claim that migration is already complete.

Canonical rule:

```text
do not assume the current live MLF deployment root
is the final multi-league portfolio deployment root
```

## 19.2 Multi-League Expansion Must Preserve One Deployment SSOT Per Deployed League [REQUIREMENT]

As future leagues are added, each deployed league should have:

- one clear deployment entrypoint
- one clear env source
- one clear public hostname pattern

Canonical rule:

```text
multi-league expansion must not reintroduce competing deploy files,
competing env files, or ambiguous live operational entrypoints
```

## 19.3 Shared Extraction Must Follow Proven Responsibility Boundaries [VERIFIED/REQUIREMENT]

Shared-framework extraction should follow proven responsibility boundaries, not folder aesthetics.

The auth bridge extraction provides one proven example:

- shared code location
- league-local deployment ownership
- stable public-route proof after recreate/warm-up

Canonical rule:

```text
shared extraction should be responsibility-driven,
then proven by runtime truth
```

---

# 20) Document Intent

This document exists to help a new chat:

- reason correctly about live deployment truth
- distinguish runtime reality from old deployment habits
- keep app, DB, proxy, and auth-bridge roles clear
- verify public-routing and internal-network assumptions deterministically
- avoid dependency/build drift
- debug infrastructure issues without jumping straight to app-level guesses
- separate shared deployment patterns from current league-local deployment ownership

It intentionally does **not** try to document every business/system feature that runs on the infrastructure.

Those details should live in companion canonicals such as:

- Draft State / Initialization / Restore
- Team / Franchise Identity
- Player Control (Contracts / PT / QO)
- Pick Ownership / Pick Trades / Draft Order
- Auth / Permissions
- UI Architecture