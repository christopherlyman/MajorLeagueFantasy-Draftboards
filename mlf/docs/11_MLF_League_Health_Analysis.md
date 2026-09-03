# MLF League Health Analysis

## Purpose

This document freezes the analytical methodology for the historical MLF
League Health cross-validation used by the commercialization discovery
project.

The purpose is not to create a perfect historical baseball projection or
Yahoo scoring reconstruction.

The purpose is to determine whether the competitive-balance mechanism found
in NFFL is corroborated, partially corroborated, or contradicted by the
longer-running MLF contract league.

The portfolio-level stopping rule is defined in:

`COMMERCIALIZATION_ROADMAP.md`

## Historical Analysis Window

Primary seasons:

**2017-2025**

League size:

**16 franchises**

Primary competitive outcome:

**Final finish rank, 1 through 16**

Secondary grouped outcome:

**Top 6 versus non-Top 6**

The Top 6 boundary reflects the MLF playoff structure and should not be
replaced with the NFFL Top 4 convention.

## Controlled Asset Dataset

The historical contract source contains:

**1,654 unique controlled player-seasons**

after deterministic end-owner resolution.

Controlled rows are defined as:

- years remaining from 1 through 5; or
- FT contract status.

Literal year-marker cells are excluded.

For same-season multi-owner cases:

- `(from X)` identifies the receiving/end-season controller;
- `(to X)` identifies the sender and is excluded as end controller;
- waiver-claim rows identify the end-season controller;
- corresponding `/ FA` rows are excluded when a claimant exists.

The existing parsed timeline view is not treated as a perfect historical
ownership log without this cleanup.

## Historical Identity

Historical player identity is sufficiently resolved for League Health
analysis.

Primary identity method:

1. historical contract player name;
2. normalized exact MLB name match;
3. MLB activity in the target season;
4. explicit historical alias handling where necessary;
5. stable Yahoo player ID / role corroboration for true same-name cases.

Yahoo historical `editorial_team_abbr` is not treated as authoritative for
historical MLB organization.

MLBAM is the canonical realized-performance identifier.

Notable explicit resolutions include:

- José Ramírez hitter -> MLBAM 608070;
- Will Smith catcher -> MLBAM 669257;
- Jose Miranda -> MLBAM 669304;
- Shohei Ohtani batting and pitching fantasy rows remain separate analytical
  roles even though they share one MLBAM person.

## MLB Activity Status

Identity and realized production are separate concepts.

A valid controlled player can have no MLB activity in a season because of:

- prospect/minor-league status;
- injury;
- opt-out;
- suspension or absence;
- other non-participation.

Such rows receive:

`mlb_activity = false`

and:

`quality_pct = NULL`

They are not classified as failed identities.

## Realized Player Quality

Quality is season-relative and role-relative.

The analysis uses three role groups:

- B - hitter;
- SP - starting-pitcher profile;
- RP - relief-pitcher profile.

Pitcher role is derived from season usage.

Primary rule:

- SP when games started are at least 3 and games started / games played is
  at least 0.40;
- otherwise RP.

This rule may be retained as analysis metadata rather than production logic.

## Hitter Quality Components

The primary hitter metric uses the categories that are conceptually stable
across the 2017-2025 MLF period:

- Runs;
- Home Runs;
- RBI;
- Stolen Bases;
- Walks;
- Strikeouts;
- Batting Average contribution.

Positive direction:

- R;
- HR;
- RBI;
- SB;
- BB.

Negative direction:

- K.

Raw AVG is not ranked directly.

AVG contribution is workload weighted:

`AVG_IMPACT = H - (ROLE_SEASON_BASELINE_AVG * AB)`

This prevents a low-volume batting average from being treated as equivalent
to the same average over a full season.

## Pitcher Quality Components

The primary pitcher metric deliberately uses one cross-era component set for
2017-2025:

- Wins;
- Strikeouts;
- Innings Pitched;
- Saves plus Holds;
- ERA contribution;
- WHIP contribution;
- Total Bases Allowed contribution.

This avoids pretending that the exact 2017-2023 and 2024-2025 Yahoo category
definitions were identical.

`SVH = saves + holds`

Rate and prevention categories are workload weighted.

ERA contribution:

`ERA_IMPACT = (ROLE_SEASON_BASELINE_ERA - ERA) * IP / 9`

WHIP contribution:

`WHIP_IMPACT = (ROLE_SEASON_BASELINE_WHIP - WHIP) * IP`

Total Bases Allowed contribution:

`TB_IMPACT = (ROLE_SEASON_BASELINE_TB_PER_IP * IP) - TB_ALLOWED`

Higher values are better for all three impact measures.

MLB Stats API season payloads provide the required primary inputs, including
pitching total bases.

## Quality Starts

Quality Starts are not required for the primary cross-era metric.

MLF pitching definitions changed for 2024-2025 to include QS and combined
SV+H.

The MLB season-stat endpoint tested during discovery does not expose QS
directly.

QS should be reconstructed from game logs only if the primary League Health
conclusion materially depends on 2024-2025 pitcher results.

It is therefore a sensitivity-analysis option, not a prerequisite.

## Component Normalization

For each season and role:

1. calculate each component;
2. rank each component as a percentile within that season-role controlled
   player population;
3. reverse components where lower values are better;
4. average the available component percentiles;
5. percentile-rank the composite again within season-role.

Final realized quality:

`quality_pct = 0.00 through 1.00`

Elite realized asset:

`quality_pct >= 0.75`

This preserves the same general role-relative percentile concept used in the
NFFL League Health analysis while adapting it to baseball.

## Franchise-Season Measures

For every franchise-season, calculate at minimum:

- controlled asset count;
- MLB-active controlled asset count;
- non-producing controlled asset count;
- average active quality percentile;
- realized quality sum;
- elite controlled asset count;
- elite share;
- persistent elite count.

Primary realized quality sum treats no-MLB-activity assets as zero realized
production while preserving their separate activity status.

Average active quality excludes no-activity rows.

Both measures are retained because they answer different questions.

## Persistence

Persistent elite control is measured at the franchise/player level.

At minimum evaluate:

- elite asset retained by the same franchise into the next season;
- two-season persistent elite control;
- three-season persistent elite control where available.

Persistence measures should distinguish:

- total elite count;
- repeated control of the same elite asset;
- elite retention rate conditional on having an elite asset.

This avoids mechanically treating a larger elite portfolio as proof of
greater retention efficiency.

## Quantity and Quality Tests

Primary tests:

1. controlled asset count versus final finish;
2. active controlled asset count versus final finish;
3. average quality versus final finish;
4. realized quality sum versus final finish;
5. elite count versus final finish;
6. persistent elite count versus final finish.

For each appropriate measure report:

- pooled Pearson relationship with finish rank;
- pooled Spearman relationship with finish rank;
- Top 6 versus non-Top 6 comparison;
- season-by-season direction where useful.

Association is not interpreted as proof of causation.

## Expiration and Recirculation

For contract endpoints, classify the next-season outcome as:

- same-franchise new contract;
- same-franchise FT;
- other-franchise controlled;
- no controlled relationship.

Primary endpoint measures:

- incumbent continuation rate;
- incumbent break rate;
- elite versus non-elite continuation;
- elite versus non-elite incumbent break;
- FT bridging behavior where identifiable.

Use:

**broke incumbent controlled relationship**

rather than:

**returned to circulation**

when a player moved directly to another controlled franchise.

## No-Activity / Future Asset Lens

No-MLB-activity controlled assets are not automatically labeled prospects.

The bucket may include:

- prospects;
- injured MLB players;
- opt-outs;
- suspended/absent players;
- players whose MLB contribution had effectively ended.

For the primary competitive-performance analysis they contribute no realized
MLB production.

Future-value or prospect-quality modeling is secondary and should be added
only if the commercial conclusion materially depends on it.

## Cross-League Comparison

After the MLF battery is complete, compare the results with NFFL around the
common mechanism:

**Competitive Balance**
-> **Asset Quality**
-> **Contract Duration**
-> **Expiration**
-> **FT / QO**
-> **Recirculation**

The purpose is not to force identical numerical behavior across baseball and
football.

The purpose is to determine whether the underlying contract-control mechanism
is repeatable enough to support League Health as a commercial differentiator.

## Required Conclusion

The MLF workstream ends with one classification:

- **CORROBORATED**
- **PARTIALLY CORROBORATED**
- **CONTRADICTED**

The conclusion should be supported by a concise set of material findings and
caveats.

After that conclusion, historical research stops and the project advances to
the Commercial Product Decision Record.
