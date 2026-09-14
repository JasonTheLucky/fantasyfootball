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
| `ownership.json` | The ownership gate. Player id to owning team, plus ESPN's availability list. |
| `rosters.json` | All 12 teams: starters, bench, IR, open slots, records, acquisition counts. |
| `available_players.json` | ESPN `FREEAGENT` + `WAIVERS` pool with ownership percentages. |
| `transactions.json` | Ownership changes separated from lineup-only shuffles and draft picks. |
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
