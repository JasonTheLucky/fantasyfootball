# ESPN Fantasy Snapshot

**USABLE** - Home Heroes (2026)

| Field | Value |
| --- | --- |
| Fetched at (UTC) | `2026-09-15T18:20:16.209365+00:00` |
| Scoring period (week) | **2** |
| League id | `1787259003` |
| Teams retrieved | 12 / 12 |
| Rostered players | 194 |
| Available players (FA + waivers) | 848 |
| Players in lookup index | 1042 |
| Fetch duration | 1.07s |
| ESPN calls | 4 |
| Workflow run | `35006824300` |

## Freshness and completeness gates

| Gate | Passed |
| --- | --- |
| League settings retrieved | yes |
| All 12 rosters retrieved | yes |
| Transactions retrieved | yes |
| Player pool retrieved | yes |
| Matchups retrieved | yes |
| Ownership reconciled, no conflicts | yes |
| Player lookup index complete | yes |

## Week 2 matchups

| Away | Pts | Home | Pts | Final |
| --- | --- | --- | --- | --- |
| Jason X | 0.0 | Kelli's Top-Notch Team | 0.0 | NO |
| The Bye Week Boys | 0.0 | Norberto's Gnarly Team | 0.0 | NO |
| 🔥Certified Dumpster Fire 🔥 | 0.0 | Giant Packer Fan | 0.0 | NO |
| Connor’s Team | 0.0 | Super Lamario Brothers | 0.0 | NO |
| Commanders of Chaos | 0.0 | Nicole's Gnarly Team | 0.0 | NO |
| From Puka with Love | 0.0 | Auto Draft Champion | 0.0 | NO |

## Rosters

| # | Team | Owner | Roster | Start | Bench | IR | Bench open |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Jason X | Jason Prinsen | 17 | 9 | 7 | 1 | 0 |
| 2 | The Bye Week Boys | Ravinder Battu | 17 | 9 | 7 | 1 | 0 |
| 3 | Auto Draft Champion | Amanda Spence | 16 | 9 | 6 | 1 | 1 |
| 4 | Super Lamario Brothers | Jeffrey Clark | 16 | 9 | 7 | 0 | 0 |
| 5 | Norberto's Gnarly Team | Norberto Vargas | 16 | 9 | 7 | 0 | 0 |
| 6 | From Puka with Love | Brandon Daab | 16 | 9 | 6 | 1 | 1 |
| 7 | Connor’s Team | Connor Hartland | 16 | 8 | 7 | 1 | 0 |
| 8 | Giant Packer Fan | Brian Seehafer | 16 | 9 | 7 | 0 | 0 |
| 9 | Nicole's Gnarly Team | Nicole Brandau | 16 | 9 | 7 | 0 | 0 |
| 10 | Kelli's Top-Notch Team | Kelli Medvesky | 16 | 9 | 7 | 0 | 0 |
| 11 | 🔥Certified Dumpster Fire 🔥 | Mike LeMay | 16 | 9 | 7 | 0 | 0 |
| 12 | Commanders of Chaos | Morgan Nadke | 16 | 9 | 7 | 0 | 0 |

## Injury designations (19)

| Team | Player | Pos | Status | Slot | Starting |
| --- | --- | --- | --- | --- | --- |
| Jason X | Brock Bowers | TE | QUESTIONABLE | IR | NO |
| The Bye Week Boys | Jalen Coker | WR | QUESTIONABLE | BENCH | NO |
| The Bye Week Boys | Ja'Kobi Lane | WR | DOUBTFUL | BENCH | NO |
| The Bye Week Boys | Jordyn Tyson | WR | INJURY_RESERVE | IR | NO |
| Auto Draft Champion | Kyler Murray | QB | QUESTIONABLE | BENCH | NO |
| Auto Draft Champion | Dylan Sampson | RB | OUT | BENCH | NO |
| Auto Draft Champion | Isiah Pacheco | RB | INJURY_RESERVE | IR | NO |
| Super Lamario Brothers | Josh Jacobs | RB | DAY_TO_DAY | BENCH | NO |
| Norberto's Gnarly Team | De'Zhaun Stribling | WR | DOUBTFUL | BENCH | NO |
| Norberto's Gnarly Team | Jordan Mason | RB | QUESTIONABLE | BENCH | NO |
| From Puka with Love | Ladd McConkey | WR | QUESTIONABLE | WR | yes |
| From Puka with Love | Tank Dell | WR | INJURY_RESERVE | IR | NO |
| Connor’s Team | TreVeyon Henderson | RB | QUESTIONABLE | BENCH | NO |
| Connor’s Team | Alec Pierce | WR | QUESTIONABLE | BENCH | NO |
| Connor’s Team | A.J. Brown | WR | INJURY_RESERVE | IR | NO |
| Kelli's Top-Notch Team | Brian Thomas Jr. | WR | QUESTIONABLE | BENCH | NO |
| 🔥Certified Dumpster Fire 🔥 | Jalen McMillan | WR | QUESTIONABLE | BENCH | NO |
| 🔥Certified Dumpster Fire 🔥 | Sam Darnold | QB | DOUBTFUL | BENCH | NO |
| Commanders of Chaos | Zay Flowers | WR | QUESTIONABLE | FLEX | yes |

## Available player pool by position

| D/ST | K | QB | RB | TE | WR | Total |
| --- | --- | --- | --- | --- | --- | --- |
| 20 | 46 | 109 | 184 | 182 | 307 | 848 |

This pool comes from ESPN's own `filterStatus` of `FREEAGENT` and `WAIVERS`. Availability is never inferred from a player's absence from the rosters above.

## Transactions

4 total | 0 executed ownership changes | 4 lineup-only moves | 0 draft picks | 0 pending

## League format

Scoring: `H2H_POINTS`. Starting lineup and bench: QB x1, RB x2, WR x2, TE x1, D/ST x1, K x1, BENCH x7, IR x1, FLEX x1.

## Files in this snapshot

| File | Contents |
| --- | --- |
| `metadata.json` | Fetch timestamps, week, per-step success flags. Read first. |
| `player_index.json` | **Name to owner lookup for every classified player. Use this to answer "who owns X?".** |
| `ownership.json` | Positive ownership: player id to owner, plus availability. |
| `rosters.json` | Every team's starters, bench, and IR. |
| `available_players.json` | ESPN free agent and waiver pool. |
| `transactions.json` | Ownership changes separated from lineup-only moves. |
| `matchups.json` | Current-period matchups and live scores. |
| `league.json` | Settings, scoring rules, roster slots, teams. |
| `raw/` | Unmodified ESPN responses. Artifact only, not committed. |

