# ESPN Fantasy Football Data Pipeline

Fetches a fresh, complete snapshot of ESPN fantasy league **1787259003**
("Home Heroes", 12 teams, 2026 season) straight from the ESPN API on a GitHub
runner, then publishes it as JSON that an assistant can read.

## Why this exists

The problem was never the fantasy report. It was getting reliably fresh,
complete data out of ESPN.

```
ESPN API  ->  web retrieval / cache layer  ->  possibly stale ESPN data   (the problem)
ESPN API  ->  GitHub Action  ->  fresh JSON  ->  assistant  ->  report    (this repo)
```

A GitHub runner is an ordinary execution environment, which buys three things a
web-retrieval path cannot offer:

1. **Request headers.** ESPN's player-pool endpoint is driven by the
   `X-Fantasy-Filter` header. Without it there is no way to ask ESPN "who is a
   free agent or on waivers right now". A script sends it trivially.
2. **Cache-busting.** Every request carries a unique `cb` timestamp plus
   `Cache-Control: no-cache`, so no intermediary can hand back an old body.
3. **Week discovery.** The current `scoringPeriodId` is read from ESPN's own
   status rather than hard-coded, so the snapshot never reports the wrong week.

The result: **ownership is resolved positively.** Availability comes from ESPN's
own free-agent/waiver pool, never from the weak inference "this player wasn't on
any of the 12 rosters, so he's probably available."

## Layout

```
.github/workflows/espn-fantasy.yml   the workflow
scripts/fetch_espn.py                fetches and normalizes ESPN data
scripts/summarize.py                 renders the digest
data/                                committed output (refreshed by the workflow)
docs/daily-report-prompt.md          the daily report prompt that consumes it
```

The prompt that turns this snapshot into the daily report lives in
[docs/daily-report-prompt.md](docs/daily-report-prompt.md), including its FAAB
bidding rules and validation gates.

## Output files

Everything lands in `data/`. Read `metadata.json` or `SNAPSHOT.md` first to
confirm the snapshot is usable, then go to the detail files.

| File | Contents |
| --- | --- |
| `SNAPSHOT.md` | One-page digest: freshness, gates, matchups, rosters, injuries. Start here. |
| `metadata.json` | Fetch timestamp, `scoringPeriodId`, per-step success flags, every ESPN call made. |
| `player_index.json` | Flat name-to-owner lookup for all classified players. Use this to answer "who owns X?". |
| `recent_form_rankings.json` | Recent form: top 24 QB, 60 RB, 60 WR, 24 TE, 24 K, 20 D/ST by average points over each player's own last three completed games, with the owning fantasy team and a Jason X flag. |
| `ownership.json` | The ownership gate. Player id to owning team, plus ESPN's availability list. |
| `rosters.json` | All 12 teams: starters, bench, IR, open slots, records, FAAB remaining. |
| `available_players.json` | ESPN `FREEAGENT` + `WAIVERS` pool with ownership percentages. |
| `transactions.json` | Full history across all scoring periods: ownership changes, `waiver_bids` with FAAB amounts, failed claims, lineup-only shuffles, draft picks. |
| `matchups.json` | Current-period matchups with live scores and win probability. |
| `league.json` | Settings, scoring rules, roster slots, teams, waiver rules. |
| `raw/` | Unmodified ESPN responses. In the workflow artifact only, gitignored (several MB per run). |

### Verifying freshness before using the data

`metadata.json` is designed to be checked, not trusted blindly:

```json
{
  "fetched_at": "2026-09-14T19:18:33.996174+00:00",
  "scoringPeriodId": 1,
  "team_count": 12,
  "expected_team_count": 12,
  "team_count_complete": true,
  "roster_fetch_success": true,
  "transactions_fetch_success": true,
  "player_pool_fetch_success": true,
  "ownership_reconciled": true,
  "critical_success": true,
  "errors": []
}
```

Reject the snapshot if `critical_success` is false, if `team_count_complete` is
false, if `errors` is non-empty, or if `fetched_at` is older than you need.

`ownership_reconciled` is only true when all rosters and the player pool were
both retrieved **and** no player appeared in both lists.

### Resolving a player name to an owner

`player_index.json` exists so that answering "who owns MarShawn Lloyd?" is one
exact lookup rather than a search across two multi-hundred-kilobyte files. It
holds every classified player once (1,041 here: 194 rostered + 847 available) and
is about a quarter the size of the files it replaces.

```json
{
  "by_normalized_name": { "marshawn lloyd": [4429023] },
  "players_by_id": {
    "4429023": {
      "player_id": 4429023,
      "name": "MarShawn Lloyd",
      "normalized_name": "marshawn lloyd",
      "position": "RB",
      "pro_team": "GB",
      "fantasy_status": "ROSTERED",
      "fantasy_team": "From Puka with Love",
      "lineup_slot": "RB"
    }
  }
}
```

To resolve a name, lowercase it, strip accents, then build candidate keys two
ways and try each:

1. Replace each run of punctuation with a space: `A.J. Brown` -> `a j brown`
2. Delete punctuation outright: `A.J. Brown` -> `aj brown`
3. Repeat both with a trailing `Jr`/`Sr`/`II`/`III`/`IV`/`V` removed

Both forms are indexed, which is what makes `AJ Brown`, `A.J. Brown`,
`Ja'Marr Chase`, `JaMarr Chase`, `Michael Pittman`, `Jaguars DST` and
`Jaguars D/ST` all resolve. Keys with null values are omitted from records, so a
missing `fantasy_team` means the player is not rostered.

Declare ownership unresolved only when **every** candidate key is absent from
`by_normalized_name`, or when several players survive disambiguation by
`position` and `pro_team`. A failed code search or a truncated file read is not
evidence of absence — GitHub code search does not reliably index large generated
JSON, so searching for a name and finding nothing says nothing about the data.

Names mapping to more than one player are listed in `collisions`. In this
snapshot there is exactly one: two free agents named Josh Johnson, a QB and an
RB, separated by `position`.

### Recent form rankings (last 3 completed games)

`data/recent_form_rankings.json` ranks players by the average actual fantasy
points of their last three completed games. The limits are **24 QB**, **60 RB**,
**60 WR**, **24 TE**, **24 K** and **20 D/ST** — total **212**. Those numbers
have one source, `RECENT_FORM_LIMITS` in `scripts/fetch_espn.py`; the artifact,
the digest, the daily-report prompt, this README and the tests all trace back to
it.

**The window is per player, not a fixed week range.** Each player's three most
recent completed games are used, so two players in the same table can be on
different weeks after a bye. This is not a stylistic choice: a fixed weeks 6–8
window yields only 21 kickers against a limit of 24, while the per-player window
yields 35. It is also why the PDF heads the game columns `GAME 1 (OLDEST) /
GAME 2 / GAME 3 (LATEST)` with the week inside each cell (`W8 · 24.60`) rather
than with week numbers.

**What counts as a completed game — participation is verified, not assumed.**
A game qualifies only when the player actually appeared in it. ESPN must have
reported an actual entry for that scoring period **and** that entry must carry
the games-played marker, stat id `210`.

The presence of a scoring row is **not** sufficient, and relying on it was a real
bug. ESPN emits a weekly actual with `appliedTotal 0.0` for every rostered player
who did not play, so a player who went 18.4 / did-not-play / 16.5 averaged 11.63
instead of 17.45. Measured across weeks 1–9 of this league the marker is strictly
binary — present as `1.0` on 3,721 rows, absent on 1,541 — and the 1,541
absences are exactly the rows carrying an empty stats dict.

A game the player *did* appear in counts **even when it scored 0.00**: a receiver
targeted five times with no catch, a quarterback with one incomplete pass. There
were 1,073 such real appearances, and discarding them would be just as wrong as
counting the missed games. `diagnostics.zero_point_games_kept` reports how many
were kept; `diagnostics.not_played_excluded` reports how many did-not-play rows
were discarded.

A bye or a missed game simply does not count. A player with fewer than three
qualifying games is excluded entirely — nothing is padded and no placeholder row
exists.

Lists are sorted by `avg_3_game_exact` descending, ties broken by ascending ESPN
player id so the order is total and stable between runs. `avg_3_game_display` is
the two-decimal string to show.

```json
{
  "rank": 2,
  "player_id": 4430878,
  "name": "Jaxon Smith-Njigba",
  "position": "WR",
  "pro_team": "SEA",
  "games": [
    { "scoring_period_id": 6, "points": 25.3 },
    { "scoring_period_id": 7, "points": 35.4 },
    { "scoring_period_id": 8, "points": 25.8 }
  ],
  "avg_3_game_display": "28.83",
  "owner_label": "Jason X",
  "owner_team_id": 1,
  "fantasy_status": "ROSTERED",
  "is_jason_x": true
}
```

**`is_jason_x` drives the pale-yellow row highlight in the daily PDF.** It is
resolved by ESPN player id against the same snapshot's `player_index.json`, and
is true only when `fantasy_status == "ROSTERED"` and `fantasy_team_id == 1`
(`JASON_X_TEAM_ID`). It is true for bench and IR players and for Jason X's D/ST
unit, because it never looks at a lineup slot.

The **team id is the authority, not the team name**. Two teams in this league
were renamed mid-season (`Super Lamario Brothers` → `Cole-Train`,
`Norberto's Gnarly Team` → `Burrow's Bunch`), and a rename of Jason X under a
name rule would have switched every highlight off silently while the tests still
passed. The name is kept as a cross-check: `ownership.jason_x_name_id_agree`
goes false and the run records a warning when the name and the id stop selecting
the same players, and `ownership.jason_x_team_name_observed` reports the name the
id currently carries.

`owner_label` is the owning team's **current** name, or `FREE AGENT`, `WAIVERS`,
or `UNKNOWN` when the id is absent from the index — never a generic `Owned`, so
an unusually strong three-game average immediately reads as yours, a waiver
target, or a trade target.

Weekly points come from ESPN's `appliedTotal` on a league-scoped request, which
already applies this league's scoring. `scoring_crosscheck` independently
recomputes every ranked value from `league.json` → `scoring_settings` and reports
the largest disagreement; it is drift detection only and never changes a ranking.

One consequence worth knowing: because a game counts once the player has appeared
in it, a snapshot taken while games are in progress can count a partially played
week for players whose game has already kicked off. `weeks.with_data` shows
which periods contributed, and the snapshot refreshes every six hours.

**The rendered PDF cannot be verified here.** The PDF is produced externally by
ChatGPT from `docs/daily-report-prompt.md`, so no test in this repository can
confirm the yellow highlighting survived rendering. What the repository does
verify is the data behind it, in both directions: every flagged player is
Jason X's in the index, and every Jason X player in the tables is flagged. The
rendering itself is enforced by prompt gates `T` and `U`.

### FAAB budgets

This league uses FAAB with a **$200** per-team budget and a $0 minimum bid
(`isUsingAcquisitionBudget` is set, so the budget is meaningful). Remaining
budget appears in three places:

- `league.json` -> `faab` for the league-wide settings
- `rosters.json` -> each team's `faab` block: `budget`, `spent`, `remaining`, `percent_remaining`
- `ownership.json` -> `team_needs`, alongside open roster slots, for waiver planning
- `SNAPSHOT.md` has a ranked table

Quote `remaining` from those files. It derives from ESPN's own per-team ledger
(`transactionCounter.acquisitionBudgetSpent`), which is what the ESPN UI shows.

**Do not sum `bid_amount` from the transaction log to compute remaining budget.**
`transactions.json` -> `waiver_bids` lists every bid with a `succeeded` flag, and
failed claims (`status` beginning `FAILED_`) were never charged. ESPN's ledger can
also legitimately disagree with a sum of winning bids: in this league one team
has a $1 executed week-1 claim that its ledger does not count. Use `waiver_bids`
for narrative ("who bid what, and who lost a claim"), not for arithmetic on
budgets.

Waiver rank is still present and breaks ties between equal bids.

## Running it

**Manually:** Actions tab -> "ESPN Fantasy Snapshot" -> Run workflow. Optional
inputs let you override the week or skip committing.

**On a schedule:** every six hours via cron.

**Locally:**

```bash
python scripts/fetch_espn.py          # writes ./data
python scripts/summarize.py           # writes data/SNAPSHOT.md

python scripts/fetch_espn.py --help   # league/season/week/output overrides
```

No dependencies. Standard library only, Python 3.9+.

### Running the tests

```bash
python -m unittest discover -s tests -v
```

Standard-library `unittest`, no pytest and no dependencies. The suite runs fully
offline against the committed `data/*.json`, so it depends on
`data/recent_form_rankings.json` being committed. A few cases skip rather than
fail when the committed snapshot is degraded (a scoring period failed to fetch)
or when `metadata.json` came from a different run than the rankings artifact —
the scheduled Action commits whatever it produced, so "nothing went wrong this
time" is not treated as an invariant.

## How an assistant consumes this

Two paths, both already available through a GitHub connector:

1. **Committed files (simplest).** Read `data/SNAPSHOT.md` and
   `data/metadata.json` from the default branch. Every workflow run commits
   refreshed data with a message like
   `ESPN snapshot: week 1 @ 2026-09-14T19:18:33+00:00`.
2. **Workflow artifact.** List runs of `espn-fantasy.yml`, take the newest
   successful run, download the `espn-fantasy-data` artifact. This also contains
   `raw/`, useful for auditing a specific claim against ESPN's exact response.

The job summary on each run page repeats the same digest, so a run can be
sanity-checked without downloading anything.

## Notes on the ESPN API

Findings from building this, since they are easy to get wrong:

- Base URL: `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leagues/1787259003`
- `mTransactions2` returns **HTTP 400 if you send an `X-Fantasy-Filter` header.**
  Send no filter for that view.
- `mTransactions2` is **scoped to a scoring period**, and calling it with no
  `scoringPeriodId` returns only the *current* period. This is a quiet trap: in
  week 1 the bare call happened to include the draft and every early move, so it
  looked like full history; the identical call in week 2 returned four lineup
  tweaks. The script now walks periods `0..current` and merges by transaction id,
  which restores the full log (298 rows here versus 4). Periods overlap, so
  deduping is required.
- Waiver claims that lose or are invalid stay in the log with a
  `FAILED_*` status (for example `FAILED_INVALIDPLAYERSOURCE`). They carry a real
  `bidAmount` but were never charged, so never count them as acquisitions.
- The `communication/` activity endpoint returns **401** for a public league, so
  it is not used. `mTransactions2` covers transactions.
- `scoringPeriodId` is at the payload top level; `status` carries
  `latestScoringPeriod` and `currentMatchupPeriod`.
- `totalPoints` stays `0.0` until a matchup is finalized. The in-progress score
  is in `totalPointsLive`.
- Healthy players report `injuryStatus: "ACTIVE"` on the player object and
  `"NORMAL"` on the roster entry. Real designations are `QUESTIONABLE`,
  `DOUBTFUL`, `OUT`, `INJURY_RESERVE`, `DAY_TO_DAY`. D/ST has none.
- The player pool pages via `limit`/`offset` inside the filter. This league's
  FA + waiver pool is roughly 850 players.
- Public leagues need no authentication. If the league is ever made private, add
  `ESPN_SWID` and `ESPN_S2` repository secrets; the script picks them up
  automatically.
- **Per-week actual scores** come from `view=kona_player_info` with
  `scoringPeriodId=N`. The weekly actual is the `player.stats` entry with
  `statSourceId == 0`, `scoringPeriodId == N` **and** `seasonId` equal to the
  season. All three conditions are required: season aggregates are
  `statSourceId 0` with `scoringPeriodId 0`, projections are `statSourceId 1`,
  and prior-season rows share the same array. The inner per-week `stats` dict is
  keyed by stat id **as a string**, so any recompute must coerce the key.
- **`appliedTotal` on a league-scoped request already applies this league's
  scoring.** Verified by recomputing
  `sum(stats[statId] * scoringItems[statId].points)` from `league.json` for every
  value used in the rankings: max absolute delta **0.0** across 636 values. The
  recompute must honour `pointsOverrides`, which this league keys by
  `defaultPositionId` as a string (statId 123 scores `-1.0` for position 16 and
  `0.0` otherwise), or it reports a large false delta on every D/ST.
- `filterStatsForTopScoringPeriodIds` returns **HTTP 400** on this endpoint.
  Never send it.
- Omitting `filterStatus` returns ONTEAM + WAIVERS + FREEAGENT together,
  including D/ST units (`defaultPositionId 16`, negative ids such as `-16026`).
  An `X-Fantasy-Filter` is still required for paging: with no filter header at
  all the endpoint returns only 50 players and no D/ST, `limit: 1500` returns the
  whole pool in one request, and `limit: 2000` is an HTTP 400. The code pages at
  1000, which is what the free-agent pool fetch already relies on.

## Maintenance

`ESPN_SEASON` is pinned to `2026` in the workflow. ESPN does not publish a new
season until roughly midsummer, so auto-detecting it would break during the
offseason. Bump that value when the 2027 season opens.
