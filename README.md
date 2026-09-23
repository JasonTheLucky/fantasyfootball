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
```

## Output files

Everything lands in `data/`. Read `metadata.json` or `SNAPSHOT.md` first to
confirm the snapshot is usable, then go to the detail files.

| File | Contents |
| --- | --- |
| `SNAPSHOT.md` | One-page digest: freshness, gates, matchups, rosters, injuries. Start here. |
| `metadata.json` | Fetch timestamp, `scoringPeriodId`, per-step success flags, every ESPN call made. |
| `player_index.json` | Flat name-to-owner lookup for all 1,041 classified players. Use this to answer "who owns X?". |
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

## Maintenance

`ESPN_SEASON` is pinned to `2026` in the workflow. ESPN does not publish a new
season until roughly midsummer, so auto-detecting it would break during the
offseason. Bump that value when the 2027 season opens.
