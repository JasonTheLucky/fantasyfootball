# Jason X — Daily Fantasy Report Prompt

The prompt for the ChatGPT scheduled job that produces the daily Jason X
activity and waiver report. Copy the fenced block below verbatim.

See the [README](../README.md) for what each data file contains, how ownership
resolves, and why FAAB, injuries and participation work the way they do.

## What changed in this revision

The pipeline now precomputes things the prompt used to derive, so several
instructions became lookups:

| Was | Now |
| --- | --- |
| Derive DATA STATUS from several files | Read `gates.json` → `data_status` |
| "Rebuild Jason X starters/bench/IR/open spots every run" | Read `jason_x.json` |
| Web research for byes and opponents | Read `nfl_schedule.json` |
| Read and sort the ~880 KB free-agent pool | Read `top_available.json` (~145 KB) |
| Convert snapshot time to CT | `fetched_at_ct_label` is already CT |
| Injury status without a return date | `injury` block inline on every player |
| Hand-grade Section 8, full league on Tuesdays only | `position_ranks.json`: all 12 teams ranked 1–12 per position, every day |
| Guess trade partners | `position_ranks.json` → `trade_fits`: trades that improve both lineups |
| Standings never mentioned | `jason_x.json` → `season_outlook` |
| Byes looked at two weeks ahead | `jason_x.json` → `bye_calendar` through Week 17 |
| IR returns left as "reassess" | `jason_x.json` → `ir_outlook` |
| Opponent's injuries invisible | `current_matchup` → `opponent_lineup_risk` |

The report structure also changed: each fact now has one home section (see
ONE HOME PER FACT in the prompt), Section 5 is one two-week table, Section 6 is
a five-row table, Section 7 keeps only actionable threads, and Section 4A
prints as an appendix after Section 10.

Also corrected: recent form now requires **verified game participation**. ESPN
emits a 0.0 scoring row for players who did not play, and counting those as
games understated form badly (one player averaged 6.83 instead of 20.90). A game
counts only when ESPN's games-played marker is present; a genuine 0.00 from a
game the player did appear in still counts.

## The prompt

````text
Create the daily Jason X fantasy-football activity + waiver report for my 12-team ESPN redraft league, focused mainly on the last ~24 hours.

AUTHORITATIVE DATA

Use connected GitHub repo JasonTheLucky/fantasyfootball as authoritative ESPN league state. Never trigger GitHub Actions. GitHub alone determines roster/ownership/availability/FAAB/injury/bye state. Web research supplies current NFL/team/beat/fantasy news and Bettor In Green sentiment only; external sources never establish league availability, ownership, FAAB, an injury designation or a bye.

Read in this order: data/gates.json, data/metadata.json, data/SNAPSHOT.md, data/jason_x.json, data/player_index.json, data/position_ranks.json. Then as needed: recent_form_rankings.json, top_available.json, injuries.json, nfl_schedule.json, ownership.json, rosters.json, available_players.json, transactions.json, matchups.json, league.json.

All files come from one snapshot run. Confirm league_id, season and scoring_period_id agree across every file you read, and that each file's fetched_at matches metadata.started_at. If they disagree, say so and treat the mismatched file as unavailable rather than mixing runs.

Timestamps are already converted: use metadata.fetched_at_ct_label for snapshot time and never convert UTC yourself. Waiver processing times use waiver_process_ct.

DATA STATUS

Read gates.json. Show only `DATA STATUS: PASS · ESPN snapshot [fetched_at_ct_label]` or `DATA STATUS: FAIL · ESPN snapshot [fetched_at_ct_label] — [brief material failure]`.

gates.data_status is the snapshot verdict; do not re-derive it. PASS also requires your own ownership, acquisition-cost, Section 4A and PDF gates to pass. Gates marked critical false => FAIL. Gates marked non-critical false => continue, but state plainly what is degraded (for example "no return dates: injury feed unavailable") rather than silently omitting it. gates.failed_non_critical lists them.

JASON X ROSTER

Read jason_x.json. Do not rebuild the roster. It gives starters, bench, injured_reserve, open_starting_slots, capacity, faab, waiver_rank, each player's bye_week, upcoming opponents, projected_points and ros_projected_points (ESPN's rest-of-season projection), bye_pressure for the next two weeks, and injury_designations. It also gives season_outlook (standings against the playoff cut line), bye_calendar (every remaining week's starter byes with the best bench cover), ir_outlook (what happens to the roster when each fantasy-IR player is activated) and, inside current_matchup, my_lineup_risk and opponent_lineup_risk. Use these as published; never recompute them.

These strategy blocks are supplementary, like position_ranks.json. If one is missing, for example because the snapshot predates them, the report still runs and DATA STATUS does not fail for that reason alone. Render the affected part as `[BLOCK] UNAVAILABLE — not in this snapshot` and carry on without it: no SEASON tile standings without season_outlook, no BYE CALENDAR without bye_calendar, no IR ACTIVATION plan without ir_outlook, and no flagged-starter line in MATCHUP without the lineup-risk blocks. Never rebuild a missing block yourself. List every unavailable block once in Section 10's QA line.

has_open_bench_spot is the authority on whether an add needs a drop. Use `DROP NONE — OPEN ROSTER SPOT` only when it is true; otherwise every add names and justifies a specific drop.

OWNERSHIP GATE

player_index.json is the primary identity and ownership resolver. Validate rostered + available = indexed on the same snapshot. For every named player in Sections 3-9 use both documented normalization forms plus suffix-stripped variants → by_normalized_name → ESPN ID → players_by_id → fantasy_status/fantasy_team; resolve collisions by NFL team + position. Never infer availability from roster absence, and never match by name where an ESPN id is available.

Allowed labels only: `OWNED: JASON X`, `OWNED: [Fantasy Team Name]`, `FREE AGENT`, `WAIVERS`, `OWNERSHIP UNRESOLVED — [specific reason]`. Never recommend unresolved players.

Before rendering Sections 3-9 build one resolved_players map for every named player. Immediately before the PDF, re-read players_by_id for every player treated as available/add/claim/stash. If ROSTERED, remove/reclassify/recalculate. Repeat until `OWNERSHIP MISMATCHES = 0`. Transactions are audit and narrative only; never replay them over current state. Section 4A and Section 10 are exempt from name resolution because they resolve ownership by ESPN player id inside the snapshot.

LEAGUE

1 QB,2 RB,2 WR,1 TE,1 FLEX,1 D/ST,1 K,7 bench,1 IR. Full PPR. Passing .04/yd,4 TD,-2 INT,+2 300-399,+4 400+,+2 50+ TD. Rush .1/yd,6 TD,+2 100-199,+4 200+,+2 50+ TD. Rec 1 PPR,.1/yd,6 TD,same bonuses. K PAT1, missed FG -1, FG 0-39=3,40-49=4,50-59=5,60+=6. D/ST strongly rewards sacks/turnovers/TD/low points and especially low yardage.

FAAB & ACQUISITION COST

FAAB is authoritative from the snapshot. Read league.json→faab for budget_per_team, minimum_bid and waiver settings; read jason_x.json→faab for Jason's balance and each team's rosters.json faab block for theirs. These ESPN ledger figures are the only balances to quote. NEVER sum transaction bid_amount to derive remaining budget: failed claims carry real bids that were never charged, and the ledger can legitimately disagree with a sum of winning bids. transactions.json→waiver_bids is narrative only, for observed market prices.

Resolve availability and cost separately for every acquisition. `FREE AGENT = $0, instant, uncontested`. `WAIVERS = FAAB bid, processes later, can be outbid`. Never bid on a free agent or call a waiver claim free. If the pool has zero free agents, say every add this cycle is contested.

For each WAIVERS recommendation state SUGGESTED BID, MAX BID and the player's waiver_process_ct. Whole-dollar bids, at least minimum_bid, never above Jason's remaining.

Bid ladder: read jason_x.json→faab_plan. Its tiers are shares of REMAINING budget and are already costed in dollars, so quote those figures and never recompute a share of the original. A ladder against the original breaks once a manager has spent down: with $51 of $200 left, "25-40% of original" is $50-80 and cannot be bid at all.

Never recommend a tier whose affordable is false. faab_plan.discretionary is remaining minus the reserve held back for injury replacement; a bid above it is permitted but must be explicitly flagged as breaching the reserve, with the dollar figure named. minimum_bid can be $0, but if Jason's waiver_rank is weak bid at least $1 on anything genuinely wanted.

Budget discipline: state remaining, rank among the 12, and the balance left after the recommendation. When Jason is near the bottom of the league in remaining budget, say so and prefer one above-market bid on the best target over several mid-sized bids he cannot all afford.

Contested-bid check: compare Jason's remaining and waiver_rank against all 11 other teams. State how many can outbid him and name plausible competitors for that player given their needs. If a market-value bid is likely to lose, recommend an above-market bid or a cheaper alternative, and say which.

INJURIES AND IR — FLAG EVERYWHERE

Injury state is authoritative from the snapshot and must travel with the player into every section. A compact `injury` block is attached inline to each player in rosters.json, player_index.json, recent_form_rankings.json, jason_x.json and top_available.json; injuries.json holds the full record keyed by the same ESPN player id, adding injury type, body location and a one-line beat note. Join by player id only. Never source a designation or return date from the web when the snapshot has one.

The `injury` block and injuries.json are the only designation source. The raw `injury_status` / `roster_injury_status` field on the player pool is ESPN's sticky fantasy-side flag and is routinely stale: it leaves `INJURY_RESERVE`, `OUT` or `QUESTIONABLE` on players who are active and playing. Never treat that raw field as a designation and never reconcile against it. A player whose `injury_status` reads `INJURY_RESERVE` but who has no `injury` block is healthy and available — the live NFL injury feed does not list them, which is the authoritative answer. Expect dozens of these every snapshot (recent run: ~106, including active starters like De'Von Achane, Jalen Hurts, Davante Adams and Tyreek Hill). A blank STATUS cell for such a player is correct, not an unresolved contradiction, and must not block certification: gates.json PASS already accounts for this. A player carries a designation if and only if they appear in the live injury feed. (ESPN's fantasy field can also lag projections; a `projected_points` of 0.0 is a projection quirk, not an injury.)

`on_nfl_ir` and `in_fantasy_ir_slot` are different things and routinely disagree. The first means the NFL team placed the player on injured reserve; the second means a manager parked them in this league's fantasy IR slot. injuries.json→ir_on_nfl_but_active_slot lists players burning an active roster spot while on NFL IR; ir_slot_but_not_on_nfl_ir lists fantasy IR slots holding someone the NFL has not designated. When either involves Jason X, Section 2's ROSTER SPOT PLAN explains it, Section 1 INJURY WATCH names it, and any resulting move is a Section 9 action.

Wherever a player appears, show their designation if they have one. Mark `on_nfl_ir` true as `IR` prominently and a fantasy IR slot as `IR SLOT`; when both, `IR`. Use the designation verbatim: IR, IR-R, OUT, DOUBTFUL, QUESTIONABLE, DAY_TO_DAY, SUSPENSION. `IR-R` means designated to return and must never be shown as plain IR.

State the expected return whenever present, using expected_return_label. A label of `out for the season` means exactly that and must never be rendered as a date. A near-term date is an estimate: say "expected", not "returns".

Never recommend adding, starting or stashing a player labelled `out for the season`, and never recommend starting a player with on_nfl_ir true. An IR player may be a legitimate stash if roster space allows and the return lands inside the horizon discussed, but the recommendation must state the return date and the roster-spot cost. Recent form and current health are independent facts: a strong three-game average does not survive an IR designation.

In the PDF carry the designation in the player's line or a STATUS column in every table where they appear, pale red for OUT/IR/season-ending and pale gold for QUESTIONABLE/DAY_TO_DAY.

WHAT CHANGED SINCE THE LAST SNAPSHOT

Read changes_since_last.json first among the analytical files. This is the delta the report's "last ~24 hours" framing needs: every other file is point-in-time state, and an injury designation changing is the highest-value daily signal there is.

The window is one snapshot interval, roughly six hours, not exactly 24 hours. previous_snapshot_at_ct says when the comparison baseline was taken; state that window rather than implying a clean 24-hour sweep. If comparable is false there was no previous snapshot, so say the delta is unavailable and never present empty lists as "nothing changed".

injury_changes is sorted with Jason X players first and carries direction: new, changed or cleared. jason_x_injury_changes is the subset affecting Jason. became_available are players a rival dropped who are now addable. became_rostered are players claimed, which is how a target you were weighing yesterday disappears; when a player you would otherwise recommend appears there, say so explicitly rather than recommending someone already gone. changed_team is a trade or a move between teams.

Lead Section 1 with the most consequential change, and build Section 3 from these lists rather than inferring recency from transactions.json. A Jason X starter whose designation worsened outranks any waiver opportunity.

FORWARD-LOOKING PROJECTIONS

Section 4A ranks the last three games PLAYED, which is backward-looking. Every decision it feeds is about the next game, so weigh it against projected_points, which is ESPN's projection for projection_scoring_period_id, the current week. It appears inline on jason_x.json players and on top_available.json entries.

Use both and say when they disagree: strong recent form with a weak projection usually means a bye, an injury or a hard matchup, and a weak average with a strong projection usually means a role that has just changed. Neither number alone is a recommendation.

A projection of 0.0 is usually real, not missing: the player's NFL team is on bye that week, he has no NFL team, or he is not expected to play. Check the player's bye_week and upcoming block before reading 0.0 as poor form, and never describe a bye-week zero as a bad projection. An absent projected_points means ESPN published no projection at all; treat it as unknown, never as zero.

BYES AND SCHEDULE

Read nfl_schedule.json for bye weeks and opponents. Never use web research for a bye or an opponent. bye_weeks_by_abbrev and byes_by_scoring_period give the league-wide picture; every player record already carries bye_week inline; teams[pro_team_id].games_by_scoring_period gives opponents with kickoff_ct. A scoring period absent from a team's game map is that team's bye.

jason_x.json→bye_pressure already names which Jason X starters are on bye in each of the next two weeks. Use it rather than deriving byes yourself.

FANTASY RELEVANCE

Prioritize injuries/status, starter and depth-chart changes, snaps/routes/targets/carries, red-zone and goal-line usage, teammate injuries, meaningful coach comments, breakout evidence, start/sit and ROS value. No padding.

ONE HOME PER FACT

Write each fact in full in one section only. Everywhere else, name the player and point to the home in a few words, for example `Shrader — Action 1` or `contingency: Section 5`. Repeating the same reasoning across sections makes the report longer without telling the reader anything new.

- A recommended move (add, cost, drop, claim timing, FAAB after): Section 9 only. Sections 1, 2, 4, 5 and 6 name the player and the action number.
- The injury contingency chain (who replaces whom if a starter is ruled out, including the both-out case): Section 5 `INJURY CONTINGENCY:` only. Sections 1, 2 and 9 say who is in doubt and point there.
- FAAB position (remaining, rank among the 12, teams able to outbid, reserve): Section 1 FAAB POSITION and Section 8 FAAB LEVERAGE only. Other sections state a FAAB figure only when they recommend a bid.
- Drop order and IR activation: Section 2 ROSTER SPOT PLAN only. Every drop named anywhere comes from its cut order.
- A player's injury designation and expected return are the exception: they travel with the player into every section, as INJURIES AND IR requires.

Keep the evidence that checks passed out of the reader's way. Every check still runs on every player, but phrases such as `verified AVAILABLE using ESPN player ID`, `instant, uncontested`, `Contested risk: LOW — free agent instant` or `not a claim of added historical statistics` appear once, in Section 10's QA line, never row by row. When every candidate in a table or list is a free agent, say `All FREE AGENT $0, instant` once in its header and leave cost out of the rows. Show cost in a row only when it differs: a WAIVERS player with his suggested bid and waiver_process_ct.

PDF STYLE — COLOR REQUIRED

White analyst dashboard, dark text, strong headings, neutral separators. Green=ADD NOW/START/POSITIVE/MAKE A MOVE; pale gold=CONSIDER/WATCH/MIXED; blue-gray=HOLD/MONITOR/INFO; gray=PASS/NO ACTION; pale red=OUT/NEGATIVE/AVOID. Sections 1+9 prominent colored decision cards; Sections 2/3/5/6 subtle colored status or decision badges. Section 7 is a deliberate white-panel exception. Section 4A uses yellow only for Jason X ownership. An essentially monochrome actionable report FAILS visual validation. No orphan headings, broken tables or cards, blank continuation pages, clipping or tiny text.

1. EXECUTIVE SUMMARY — AT A GLANCE

Open with WHAT CHANGED: the single most consequential item from changes_since_last.json, or `NO MATERIAL CHANGE SINCE [previous_snapshot_at_ct]` when there is none. A Jason X designation worsening leads over anything else.

Dashboard: OVERALL, WHAT CHANGED, SEASON, MATCHUP, TOP WAIVER, BEST MOVE, BEST STASH, BIGGEST ISSUE, LINEUP CHANGE, BEST TRADE, FAAB POSITION, INJURY WATCH.

Each tile gives its conclusion in one or two lines. A tile about a move names the player and his Section 9 action number; cost, drop and FAAB after live in Section 9. When two tiles would name the same move, keep BEST MOVE and leave the other out. Do not draw a tile that has nothing to report; list those tiles on one line instead, for example `NO ACTION TODAY: LINEUP CHANGE, BEST TRADE`.

SEASON comes from jason_x.json→season_outlook: seed, record, cushion_games against cut_line_team, regular_season_games_left, points_for_rank (points scored is the seeding tiebreaker when seeding_tiebreaker says so) and the status with its guidance. The season status and the matchup posture answer different questions. The posture decides this week's start/sit risk. The season status decides how much to give up now for later: stashes, playoff-week schedule (playoff_weeks) and trades. Say which way each one pushes when they pull in different directions.

MATCHUP comes from jason_x.json→current_matchup: the opponent, the projected margin, the win probability and the posture. State the posture's consequence in the same breath, because it changes what a good lineup looks like: a favourite protects the floor and an underdog chases ceiling, from the same roster. Then name each side's flagged starters from my_lineup_risk and opponent_lineup_risk, with projected_points_in_doubt. A margin smaller than Jason's points in doubt is not safe, whatever the posture says; an opponent starter ruled out widens it.

FAAB POSITION is the home for Jason's FAAB standing: remaining, rank among the 12, how many teams can outbid him, the reserve, and the dollars committed by today's actions. INJURY WATCH names every Jason X starter with a designation and any Jason X player on NFL IR holding an active roster spot, and points to Section 5 for the contingency.

2. MY PLAYERS

Only Jason X players with meaningful fresh development. Card: What happened; Confirmed news when reliable; optional forum sentiment; Fantasy impact; ROS outlook; Action START/BENCH/HOLD/WATCH/SELL HIGH/BUY LOW/ADD BACKUP/MONITOR INJURY. Show designation and expected return for anyone carrying one. A card points to Section 5 for an injury contingency and to Section 9 for a move rather than restating either.

End Section 2 with ROSTER SPOT PLAN, the home for every drop decision:

- CUT ORDER: Jason X's bench ranked from first to drop to last, at least the first three, each with a one-line reason. Weigh ros_projected_points, recent form, role, injury and expected return, bye fit against bye_calendar, and positional need. A player on NFL IR holding a bench spot ranks near the top unless he is due back soon and would start.
- IR ACTIVATION: from jason_x.json→ir_outlook.activation_plans, who returns when (expected_return_label, days_until_expected_return) and the roster_effect: a swap with a player on NFL IR, an open bench spot, or a forced drop. When swap_at_risk is true, say that the swap partner is due back no later, so the clean swap can disappear and the activation would then cost cut #1. When ir_outlook.move_to_ir_now names anyone, that move frees a roster spot at no cost and belongs in Section 9.

Every drop named anywhere in the report comes from the cut order, in order: the first add drops cut #1 and a second add drops cut #2. The same player is never the named drop for two different adds. WATCH or conditional ideas in Sections 4 and 6 say `would cost cut #1` instead of naming a drop of their own.

3. FRESH FREE-AGENT ACTIVITY

What changed since yesterday that could create an opportunity? Start from the live Bettor In Green recently-updated index when accessible, then independently verify material facts. Include only positively VERIFIED AVAILABLE players with meaningful fresh development. Fields: Player/pos/team; WHAT CHANGED; CONFIRMED NEWS; optional forum sentiment; FANTASY IMPLICATION; ROSTER RELEVANCE; VERIFIED AVAILABLE; STATUS (designation + expected return, or healthy); ACQUISITION COST (FA $0 or WAIVERS suggested bid + waiver_process_ct); DECISION. Only players who are an opportunity get a row. A changed player who became less actionable, such as a free agent moving to OUT, gets one line beneath the table. If none, say so.

4. POSITION WATCHLIST

Use top_available.json as the candidate pool; fall back to available_players.json only if it is missing. Rank the top 3 trustworthy QB/RB/WR/TE/DST/K by role, usage, volume, security, competition, environment, floor/ceiling, schedule, ROS, contingency and Jason fit. D/ST over the next 2-4 weeks under this scoring; don't overstate K. Rows: `1 | Player · Team | Grade | Availability | L3 RANK | Bye | Status | Decision` + WHY.

L3 RANK is the player's standing in Section 4A. Take it from top_available.json: when recent_form_rank is present render it as `[POS] #[rank] ([avg])`, for example `RB #36 (10.00)`. When it is absent use recent_form_note verbatim, which distinguishes three different situations and must not be flattened to "unranked": `outside the ranked top [N]`, `fewer than 3 games played`, and `excluded from the rankings: out for the season`. Never compute, infer or estimate a rank, and never renumber: the rank is the player's position in the Section 4A table and the two must agree exactly.

Acquisition cost is not a column. When every candidate in a position table is a free agent, its header says `All FREE AGENT $0, instant` and WHY leaves cost out. A WAIVERS candidate's WHY states the suggested whole-dollar bid and waiver_process_ct. Section 9 carries the exact cost for anything recommended, so the FAAB gates remain satisfiable. WHY never names a drop: a recommended add points to its Section 9 action, and a WATCH idea says `would cost cut #1` from Section 2.

If fewer than 3 are trustworthy, say so.

4A. RECENT FORM RANKINGS — LAST 3 GAMES PLAYED

Read recent_form_rankings.json from the same snapshot. Every name, week, point value, average and owner comes from that file verbatim. Never recompute or re-average points, never apply scoring rules yourself, and never source rankings or ownership from the web or another section. If the file is missing or ranked_total is 0, render `RECENT FORM RANKINGS UNAVAILABLE — [reason]` and continue; that alone does not fail DATA STATUS.

Verify league_id, season, scoring_period_id and fetched_at coherence, and check counts, diagnostics, scoring_crosscheck and ownership before using it.

Each player is ranked on the average actual fantasy points of their own last three games ACTUALLY PLAYED under this league's exact scoring, not three calendar weeks. State that above the tables, along with: a game counts only when the snapshot confirmed the player appeared in it; a genuine 0.00 from a game he did play still counts; a bye or a game he did not play is excluded entirely rather than counted as zero; and a player with fewer than three confirmed appearances is not ranked. State the weeks covered from weeks.considered, and if weeks.failed is non-empty say which periods are missing.

Players ESPN expects back only next season are already excluded from these rankings by the snapshot, and the limits backfill so the total still reaches 212. Do not add them back, do not treat their absence as missing data, and do not pad a table to compensate. excluded_out_for_season lists who was removed and diagnostics.excluded_out_for_season counts them; state that count in one line beneath the method lines so the omission is visible. Players whose expected return is a date inside this season are still ranked, carrying their designation in the STATUS column.

Immediately above the first table, on its own line:

Yellow = Jason X roster

Render six tables in this order, verbatim from rankings: top 24 QB, top 60 RB, top 60 WR, top 24 TE, top 24 K, top 20 D/ST. Those limits are 24 / 60 / 60 / 24 / 24 / 20, total 212. Head each table from counts.[POSITION]: when sufficient is true use the limit, for example `TOP 24 QB — LAST 3 GAMES`; when false state the real number and the reason. Add a subtitle naming the week span from counts.[POSITION].weeks_used. Never pad, never invent a placeholder row, never add or drop a player.

Columns in this exact order: RANK | PLAYER | POS | TEAM | GAME 1 (OLDEST) | GAME 2 | GAME 3 (LATEST) | 3-GAME AVG | STATUS | OWNER. TEAM is pro_team. STATUS is the injury designation plus expected return, or blank. OWNER is owner_label exactly as written: a fantasy team name, FREE AGENT, WAIVERS or UNKNOWN. Never replace a team name with a generic label such as `Owned`.

Game cells come from each entry's games array, oldest first. Render every cell in exactly this form: capital W, the scoring period number, a space, a middle dot, a space, then points to two decimals — `W8 · 24.60`. Use that form in all six tables with no variation. Do not head these columns with week numbers and do not assume rows share a week.

RANK is the entry's rank. 3-GAME AVG is avg_3_game_display verbatim. Keep rows in file order: sorted by unrounded average descending, ties by ESPN player id. Never re-sort, re-rank or reorder, and never let highlighting change order.

Highlight the entire row pale yellow, dark text preserved, for every entry whose is_jason_x is true and no other entry. Read that flag from the file; never decide ownership yourself and never match on a team name, because the snapshot resolves Jason X by fantasy team id and ESPN team names change mid-season. If ownership.jason_x_name_id_agree is false, add one line under the legend saying Jason X's ESPN team name has changed to ownership.jason_x_team_name_observed and that highlighting follows the team id. is_jason_x is true for starters, bench, IR and D/ST alike. Never highlight another team's player, a FREE AGENT, a WAIVERS or an UNKNOWN row. Do not use a bright or saturated yellow: numbers must stay legible and the shading must survive colour printing. Apply the same yellow in all six tables. Yellow is reserved for Jason X ownership here; keep green, pale gold, blue-gray, gray and pale red doing their usual jobs elsewhere.

Never derive availability from these rankings: player_index.json remains authoritative, and a strong average never overrides an IR designation.

PDF layout: tables may span pages; repeat headers on every continuation page; never split a player row across a page break; readable font with no shrink-to-fit; right-align numeric columns; two-decimal precision throughout; no blank pages, clipping, tiny text or orphan headings.

Incorporate noteworthy recent-form findings into Sections 1, 4, 5, 6 and 9 only where role, health and ROS evidence agree.

Placement: in the PDF, Section 4A prints last, after Section 10, as an appendix headed `APPENDIX — 4A. RECENT FORM RANKINGS — LAST 3 GAMES PLAYED`. It is reference data, and printing its ~10 pages between the watchlist and the lineup decisions breaks the reading flow. Its content, method lines, legend, table rules and gates are unchanged, and other sections still cite it by L3 RANK.

5. TWO-WEEK STARTING-LINEUP CHALLENGERS & BYE-WEEK STASHES

A Jason X lineup pressure test, NOT an NFL schedule summary. Ask whether anyone currently available could start for Jason X in the next two weeks, replace an injury or bye, outperform a likely starter, or provide valuable protection.

DO NOT LIST IRRELEVANT NFL MATCHUPS. Every matchup shown must attach directly to a Jason X player or replacement, or to a verified challenger or stash, and must help a start/bench/add/hold/stash decision.

Let the matchup set the risk posture. jason_x.json→current_matchup gives the projected margin, the win probability and a posture; follow its guidance. As a heavy favourite prefer the safer projection and avoid volatile swings, because variance is the only way to lose. As an underdog prefer upside, because a safe lineup still loses. In a coin flip small edges decide it. Say which posture you are reasoning under rather than leaving it implicit.

Cover NEXT FANTASY WEEK and the FOLLOWING FANTASY WEEK in one table. Build the likely lineup from jason_x.json; opponents and byes come from the inline bye_week and upcoming fields plus nfl_schedule.json. Weigh projected_points alongside recent form for every start/sit call. Account for injuries, returns and byes using the snapshot's designations and expected returns. Identify the best rostered replacement first, then compare the best verified available challenger. Matchup is one factor, not the sole driver. For byes use bye_pressure and prefer a rostered replacement; use BYE-WEEK NEED only when the roster is inadequate. For injuries show the designation, expected return, best rostered replacement, best verified challenger and the exact move trigger. A challenger must have a realistic chance to start for Jason within two weeks. Weigh acquisition cost.

The table: `SLOT | JASON X STARTER | STATUS | W[next] MATCHUP | W[following] MATCHUP | EDGE | ACTION`, with the real week numbers in the headers. STATUS carries the designation and expected return where present. A matchup cell reads `vs DET` or `@ NE`, or `BYE`.

List the slots in exactly this fixed order, one row each:

QB, RB, RB, WR, WR, TE, FLEX, K, D/ST

This is jason_x.json→starting_slot_order and jason_x.json→starters is already sorted into it, so follow that order rather than ESPN's. Always show all nine rows even when a slot needs no action, so the lineup reads the same way every day. Never reorder by position strength, by challenger quality or by whether a move is recommended.

EDGE exactly one of: `JASON X ROSTER`, `CHALLENGER`, `CHALLENGER IF [CONDITION]`, `CLOSE`, `MATCHUP DEPENDENT`, `BYE-WEEK NEED`. When the two weeks differ, prefix each with its week: `W9 CLOSE · W10 BYE-WEEK NEED`. Player names never belong in EDGE.

A row whose EDGE is JASON X ROSTER in both weeks needs no challenger: ACTION is `START`, or `LOCKED` for a player whose game has started, and nothing more. Every other row is a decision row, and only decision rows carry detail: in ACTION name the week it applies to, the best rostered replacement, then the best verified available challenger with his matchup, and the trigger for the move. A recommended add points to its Section 9 action.

After the table, BYE CALENDAR from jason_x.json→bye_calendar: one compact line per remaining week in which a Jason X starter is on bye, through the end of the season, giving the starters on bye, their bench cover, and `NO COVER` for each slot in uncovered_slots. Mark playoff weeks. Summarize the weeks with no starter on bye in one line. An uncovered slot inside the two-week table is a BYE-WEEK NEED there; one further out gets its lead time in weeks, because stashing a cover early costs a bench spot for every week until then.

Stashes require credible workload increase, teammate injury, starter promotion, schedule, an upcoming Jason bye, injury insurance, improving usage or contingency value; must be verified FREE AGENT or WAIVERS; and must show cost.

End exactly:

`BEST CHALLENGER NEXT WEEK:` player/NONE

`PLAYER MOST AT RISK OF LOSING A START:` Jason player/NONE

`BEST TWO-WEEK STASH:` available player/NONE

`BYE-WEEK NEED:` position/player/week/NONE

`INJURY CONTINGENCY:` player, replacement, trigger/NONE. This is the one place the full chain is written, including what happens if two starters are ruled out together.

`ADD NOW:` YES — [player], see Action [n] or NO — [reason]

Recheck every Section 5 available player immediately before rendering. Quality check: every matchup shown must help Jason decide start/bench/add/hold/stash.

6. TOP 5 FREE-AGENT ROSTER VALUE UPGRADES

Rank the top 5 VERIFIED AVAILABLE across all positions by incremental value to Jason X. This is the cross-position ranking of the same pool Section 4 already explained, so it is a five-row table and never a set of cards that re-explain Section 4's reasoning. Do not force five positive adds: a PASS row is fine, and fewer than five rows is fine when fewer are worth naming.

Table: `RANK | PLAYER · POS/TEAM | STATUS | L3 RANK | REPLACES | GAIN | CONFIDENCE | DECISION`.

- STATUS: designation and expected return, or blank.
- REPLACES: the Jason X player he would displace in the lineup, or `bench depth`.
- GAIN: CLEAR / MODERATE / SLIGHT UPGRADE, ROUGHLY EQUAL or DOWNGRADE, plus the number behind it in a few words, for example `W10 K need` or `+2 proj vs Washington Jr.`.
- DECISION: ADD NOW with its Section 9 action number, CONSIDER, WATCH or PASS. A WATCH or CONSIDER that would need a drop says `would cost cut #1`.

L3 RANK replaces the former acquisition-cost column and follows the same rule as Section 4: `[POS] #[rank] ([avg])` from top_available.json's recent_form_rank, or recent_form_note verbatim when absent. It must match that player's rank in the Section 4A table exactly.

Cost still has to be visible, because a reader of this section is deciding whether to spend. When all five are free agents, the table header says `All FREE AGENT $0, instant`; a WAIVERS player's DECISION cell carries his suggested and max whole-dollar bid.

End with one line: `BEST OVERALL VALUE:` [player], see Action [n], or `NONE WORTH A ROSTER SPOT TODAY`.

7. BETTOR IN GREEN — OUTLOOK THREAD GOSSIP

Use the live Recently Updated index. Attempt up to 10 readable individual-player Outlook threads; 1-9 accessible is sufficient and should be called `latest accessible Bettor In Green Outlook threads`. Preserve the retrieved order, skip inaccessible threads, never infer content. If zero, mark Section 7 unavailable and use no forum-derived recommendations; that alone is nonfatal to overall PASS.

Summarize only threads Jason can act on: a Jason X player, a VERIFIED AVAILABLE player, or a player named in Section 8's trade offer. A thread about another team's rostered player with no trade angle is skipped. Say in one line how many threads were read and how many were skipped for that reason. If none qualifies, say so in that line and render no panels.

Resolve all selected gossip players as a batch through the current player_index. Every header carries exactly one allowed ownership label; no rostered player as a waiver or add. Any actionable add carries its acquisition cost and any injury designation.

PDF: plain white panels. Strong player line + ownership; scan line `Forum: ... · Relevance: ... · Takeaway: ...`; thread title and recency, then exactly TWO concise paragraphs: WHAT THE FORUM THINKS and INDEPENDENT ANALYSIS. End with the most likely actionable item, why, and what would confirm it.

8. LEAGUE POSITION STRENGTH RANKINGS, NEEDS & SURPLUSES

Show all 12 teams in every report, on every day of the week. Never shorten this section to Jason X plus his trade partners, and never defer the full matrix to another day.

The ranks come from position_ranks.json, which the pipeline computes from ESPN's rest-of-season projections for every rostered player, weighted toward starting slots (its usage string gives the method). Render them verbatim. Never re-rank, adjust or re-judge them: grading 72 cells by hand every day made them move on noise. If position_ranks.json is missing or its complete is false, render `POSITION RANKS UNAVAILABLE — [reason]` in place of the matrix and do not grade rooms by hand.

Rank each position from 1 to 12, where 1 is the strongest room in the league and 12 is the weakest. For EACH position independently, every rank from 1 to 12 is used exactly once: no ties, no shared ranks, no skipped numbers. position_ranks.json already guarantees this; check it rather than fixing it. A team can hold #1 at one position and #12 at another.

Your judgement goes into BIGGEST NEED, SURPLUS and the Jason summary. Use starter quality, depth, volume, role security, injuries and expected returns, depth chart, usage, red-zone, environment, schedule, byes, floor/ceiling and replacement quality; don't overweight one game. RB/WR evaluate full rooms. D/ST especially sacks, takeaways, TD potential, points and yards allowed, ROS opponents and streaming alternatives under this scoring. BIGGEST NEED is normally one of the team's `weakest` positions in the matrix and SURPLUS one of its `strongest`; when you depart from that, for example because an injured starter returns next week, say why in a few words.

Matrix: from position_ranks.json→matrix, in its order, `TEAM | QB | RB | WR | TE | K | D/ST | FAAB | WAIVER RANK | BIGGEST NEED | SURPLUS`, all 12 teams, one row each, emphasizing Jason. Position cells are the whole-number ranks 1-12 from the file. Directly under the matrix, on its own line: `Position ranks: 1 = strongest room in the league, 12 = weakest; each rank used once per position.` If too wide, FAAB and WAIVER RANK may move to an adjacent compact table, never dropped. Treat spending power as part of the outlook: a weak room with a full budget can fix itself, a strong room that has spent down cannot. The leader at a position is the team ranked 1 and the laggard is the team ranked 12 (position_ranks.json→leaders and laggards); any leader or laggard named in the text must match.

Jason summary: STRENGTHS, BIGGEST NEED, SURPLUS, BEST TRADE, FAAB LEVERAGE. FAAB LEVERAGE is the home for Jason's bidding power against the league: remaining, rank, how many teams can outbid him, and which contenders have both a relevant need and the budget.

BEST TRADE comes from position_ranks.json→trade_fits, which tests every 1-for-1 and 2-for-1 trade between Jason X and each team and keeps only those that improve both teams' best rest-of-season lineups. Partners are sorted best first. Before naming anyone, confirm every player in the trade is still owned by the stated team in player_index.json. Then:

- If the first partner has mutual true, present its best_trade as one concrete offer: what Jason gives, what he gets, each side's lineup gain (jason_gain, their_gain), and why it works for them in terms of their weakest positions in the matrix. For a 2-for-1, say it frees a Jason X roster spot, and whether the partner has the bench spot to take two (their_bench_open) or must drop someone. Add one line on what the projections cannot see, such as fresh injury news or the playoff-week schedule from season_outlook, when it would change the answer. Name at most one alternative partner, in one line.
- If no partner has mutual true: `NO TRADE FIT TODAY` in one line.

Never list partners as `explore only` without an offer, and never invent a trade the file does not contain. Include league-wide scarcity and Jason's leverage where meaningful.

9. WHAT I WOULD DO TODAY

Maximum 3 highest-EV actions across waivers, holds, lineup, injury contingencies and trades, numbered `ACTION 1` to `ACTION 3` so other sections can point to them. This is the home for every move: the only place the full add, cost, drop, claim timing and FAAB after are written. Every ADD states FA $0 or the exact waiver bid, the claim's waiver_process_ct, the resulting FAAB balance, and the drop from Section 2's cut order unless has_open_bench_spot is true. If jason_x.json→ir_outlook.move_to_ir_now names a player, that move frees a roster spot at no cost and should be one of the three actions. An injury action says who to check and when, then points to Section 5's INJURY CONTINGENCY rather than restating it. Prefer HOLD over churn.

When Section 8 presents a concrete trade offer and it beats the alternatives, `SEND TRADE OFFER TO [TEAM]` with the exact players is one of the three actions. A named trade target must be confirmed owned. Close with one line: the total FAAB committed today and the balance after, or `NO ADD/DROP OR TRADE RECOMMENDED TODAY.` Use prominent color-coded cards.

10. SOURCES

Concise: GitHub ESPN snapshot, official/team, beat/news, fantasy analysis, Bettor In Green. No ESPN IDs or tool commentary.

End with one QA line. It is the one place the report shows that its checks passed: every acquisition verified available by ESPN player id against player_index.json, free-agent adds instant and uncontested, and the validation counters (ownership mismatches, acquisition cost errors, Section 4A counts and highlights, Section 8 rank errors, duplication errors), each with its value.

FINAL VALIDATION

A gates.json read; data_status honoured; snapshot integrity, timestamp, scoringPeriodId, team/roster/available completeness confirmed; non-critical failures stated rather than hidden.

B indexed = rostered + available on the same snapshot; every file read shares one league_id, season and fetched_at.

C Jason X lineup taken from jason_x.json, not rebuilt; starters, bench, IR, open spots and D/ST consistent with it.

D all named players in Sections 3-9 resolved via player_index.

E rendered ownership compared to players_by_id; require `OWNERSHIP MISMATCHES = 0`; correct and recalculate before the PDF.

F every acquisition in Sections 1, 3-6 and 9 has positive current availability.

G Section 8 shows all 12 teams exactly once, every day. Every position cell is a whole-number rank from 1 to 12 copied verbatim from position_ranks.json, none re-judged; for each of QB, RB, WR, TE, K and D/ST the twelve ranks are exactly 1 through 12, each used once, with no ties and no gaps; leaders and laggards named in the text match the matrix, the leader being rank 1 and the laggard rank 12; the rank legend line sits under the matrix. Needs and surpluses are supported by the ranks, or the departure is explained. Require `SECTION 8 RANK ERRORS = 0`.

H Jason's Section 8 entry consistent with his current roster.

I the trade in Section 8 is trade_fits' best_trade for the named partner, or `NO TRADE FIT TODAY` when no partner has mutual true; no partner is listed without an offer; every player in it is owned by the stated team.

J named trade targets owned by the stated team.

K transactions used as narrative only.

L Section 7: at least one thread passes, zero is nonfatal, no fabricated content.

M ownership, status and designations identical across sections.

N PDF visually inspected for layout defects and required colors; an essentially monochrome actionable report FAILS.

O FAAB integrity from gates.faab_arithmetic; balances quoted from the ledger, never summed from bids.

P acquisition cost consistent with status: FA = $0 and no bid; WAIVERS = whole-dollar bid >= minimum_bid. Require `ACQUISITION COST ERRORS = 0`.

Q total recommended bids <= Jason's remaining; any reserve-floor breach explicitly flagged.

R `DROP NONE — OPEN ROSTER SPOT` only when has_open_bench_spot is true; otherwise a specific drop is named, taken from Section 2's cut order.

S FAAB figures, waiver ranks and processing times identical everywhere.

T Section 4A matches recent_form_rankings.json exactly: six tables in order QB, RB, WR, TE, K, D/ST; each row count equals counts.ranked; rank, name, pos, team, weeks, points, average and owner verbatim; owner_label not rewritten; every game cell in the exact form `W8 · 24.60`; no padded, invented or dropped rows; order unchanged; a yellow row for every is_jason_x true entry and no other, including none for FREE AGENT, WAIVERS or UNKNOWN. No player in excluded_out_for_season appears in any table, and the excluded count is stated. Require `RECENT FORM HIGHLIGHT ERRORS = 0`, `RECENT FORM COUNT ERRORS = 0` and `RECENT FORM FORMAT ERRORS = 0`, and confirm the legend `Yellow = Jason X roster` sits immediately above the first table.

Y Delta, projections, FAAB and matchup used as published: Section 1 opens with the leading change from changes_since_last.json or states no material change with the baseline time; comparable false is reported as unavailable rather than as stability; no player listed in became_rostered is recommended as an add; every recommended bid comes from faab_plan with affordable true and any reserve breach flagged in dollars; no projection of 0.0 is described as poor form without first checking for a bye; absent projections are called unknown rather than zero; and the matchup posture from current_matchup is stated and consistent with the start/sit advice; Section 1's SEASON tile matches season_outlook (seed, record, cushion_games, status) and MATCHUP names both sides' flagged starters from the lineup-risk blocks; the bye calendar and IR activation plan match bye_calendar and ir_outlook. Require `STALE RECOMMENDATION ERRORS = 0`.

Z One home per fact: each recommended move is written in full only in Section 9; the injury contingency chain only in Section 5's INJURY CONTINGENCY; Jason's FAAB standing only in Section 1 FAAB POSITION and Section 8 FAAB LEVERAGE, plus where a bid is recommended; every drop comes from Section 2's cut order, and no player is the named drop for two different adds; Section 6 is one table that does not re-explain Section 4; Section 7 holds only actionable threads; check evidence such as `verified AVAILABLE` or `instant, uncontested` appears only in Section 10's QA line and table headers; Section 4A prints as the appendix after Section 10. Require `DUPLICATION ERRORS = 0`.

X Every L3 RANK shown in Sections 4 and 6 matches that player's rank in the Section 4A table for the same position, or is the file's recent_form_note verbatim when unranked; no rank is computed, estimated or renumbered; the three unranked reasons are never flattened into one. Section 5 lists its nine slots in the order QB, RB, RB, WR, WR, TE, FLEX, K, D/ST in its two-week table, and only decision rows carry challenger detail. Require `RANK CROSS-REFERENCE ERRORS = 0`.

U Section 4A PDF visual inspection, which is where highlighting is actually verified: yellow pale and unsaturated, text dark and legible, visible in colour print, identical in all six tables; headers repeat on continuation pages; no player row split across a page break; numeric columns right-aligned with two decimals; no blank pages, clipping, tiny text or orphan headings; table order intact after pagination.

V injury flags consistent and complete: every player with an `injury` block shows their designation; every on_nfl_ir true player marked IR; IR-R never shown as plain IR; stated returns match expected_return_label; `out for the season` never rendered as a date; nobody labelled out for the season recommended as an add, start or stash; nobody on NFL IR recommended to start; designations identical wherever a player appears. Require `INJURY FLAG ERRORS = 0`. A missing injuries.json is nonfatal, means no return dates, and must be stated.

W byes and opponents taken from nfl_schedule.json and the inline bye_week, never from web research; every bye claim matches bye_weeks_by_abbrev; Section 5 matchups all attach to a Jason X decision.

FINAL PASS

PASS only if gates.data_status is PASS and the index, roster, ownership, acquisition, FAAB, injury, cross-section, Section 4A, Section 8, duplication and PDF gates pass. Bettor In Green partial or unavailable alone is nonfatal; a missing injuries.json, nfl_schedule.json, recent_form_rankings.json or position_ranks.json, or a missing jason_x.json strategy block (season_outlook, bye_calendar, ir_outlook, my_lineup_risk, opponent_lineup_risk), is nonfatal but must be stated. Gates that check one of those inputs pass when the input is absent and the report says so. Otherwise FAIL with a brief material reason.

PDF OUTPUT

Return a concise chat summary plus a polished downloadable PDF titled `Jason X - Daily Fantasy Football Activity & Waiver Report.` Include the date and Sections 1-10 in order, followed by Section 4A as the appendix. White dominates but status and decision colors are required. Bid amounts, FAAB balances, return dates and three-game averages must be legible and consistent. No clipping, broken tables, awkward breaks, ESPN IDs, vague ownership placeholders or contradictions.

EMAIL DELIVERY — REQUIRED

After the final PDF passes all validation, email the actual final PDF attachment to `jason.prinsen@gmail.com` using connected Gmail. Subject `Jason X - Daily Fantasy Football Report - [report date]`. Concise body with DATA STATUS and a note that the full report is attached. Never email an intermediate, failed, stale or superseded PDF; if generation or validation fails, report the failure instead.
````
