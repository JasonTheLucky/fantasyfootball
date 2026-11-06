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

Read in this order: data/gates.json, data/metadata.json, data/SNAPSHOT.md, data/jason_x.json, data/player_index.json. Then as needed: recent_form_rankings.json, top_available.json, injuries.json, nfl_schedule.json, ownership.json, rosters.json, available_players.json, transactions.json, matchups.json, league.json.

All files come from one snapshot run. Confirm league_id, season and scoring_period_id agree across every file you read, and that each file's fetched_at matches metadata.started_at. If they disagree, say so and treat the mismatched file as unavailable rather than mixing runs.

Timestamps are already converted: use metadata.fetched_at_ct_label for snapshot time and never convert UTC yourself. Waiver processing times use waiver_process_ct.

DATA STATUS

Read gates.json. Show only `DATA STATUS: PASS · ESPN snapshot [fetched_at_ct_label]` or `DATA STATUS: FAIL · ESPN snapshot [fetched_at_ct_label] — [brief material failure]`.

gates.data_status is the snapshot verdict; do not re-derive it. PASS also requires your own ownership, acquisition-cost, Section 4A and PDF gates to pass. Gates marked critical false => FAIL. Gates marked non-critical false => continue, but state plainly what is degraded (for example "no return dates: injury feed unavailable") rather than silently omitting it. gates.failed_non_critical lists them.

JASON X ROSTER

Read jason_x.json. Do not rebuild the roster. It gives starters, bench, injured_reserve, open_starting_slots, capacity, faab, waiver_rank, each player's bye_week and upcoming opponents, bye_pressure for the next two weeks, and injury_designations.

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

Bid ladder as share of original budget: every-week starter or league-altering 25-40%; reliable starter at a position of need 10-20%; flex or high-upside path 3-8%; stash/injury/bye 1-3%; lottery ticket minimum viable. minimum_bid can be $0, but if Jason's waiver_rank is weak bid at least $1 on anything genuinely wanted.

Budget discipline: before mid-season preserve roughly 20% of the original budget for injury replacement, and explicitly flag any recommendation that breaches it.

Contested-bid check: compare Jason's remaining and waiver_rank against all 11 other teams. State how many can outbid him and name plausible competitors for that player given their needs. If a market-value bid is likely to lose, recommend an above-market bid or a cheaper alternative, and say which.

INJURIES AND IR — FLAG EVERYWHERE

Injury state is authoritative from the snapshot and must travel with the player into every section. A compact `injury` block is attached inline to each player in rosters.json, player_index.json, recent_form_rankings.json, jason_x.json and top_available.json; injuries.json holds the full record keyed by the same ESPN player id, adding injury type, body location and a one-line beat note. Join by player id only. Never source a designation or return date from the web when the snapshot has one.

`on_nfl_ir` and `in_fantasy_ir_slot` are different things and routinely disagree. The first means the NFL team placed the player on injured reserve; the second means a manager parked them in this league's fantasy IR slot. injuries.json→ir_on_nfl_but_active_slot lists players burning an active roster spot while on NFL IR; ir_slot_but_not_on_nfl_ir lists fantasy IR slots holding someone the NFL has not designated. When either involves Jason X it belongs in Sections 1, 2 and 9.

Wherever a player appears, show their designation if they have one. Mark `on_nfl_ir` true as `IR` prominently and a fantasy IR slot as `IR SLOT`; when both, `IR`. Use the designation verbatim: IR, IR-R, OUT, DOUBTFUL, QUESTIONABLE, DAY_TO_DAY, SUSPENSION. `IR-R` means designated to return and must never be shown as plain IR.

State the expected return whenever present, using expected_return_label. A label of `out for the season` means exactly that and must never be rendered as a date. A near-term date is an estimate: say "expected", not "returns".

Never recommend adding, starting or stashing a player labelled `out for the season`, and never recommend starting a player with on_nfl_ir true. An IR player may be a legitimate stash if roster space allows and the return lands inside the horizon discussed, but the recommendation must state the return date and the roster-spot cost. Recent form and current health are independent facts: a strong three-game average does not survive an IR designation.

In the PDF carry the designation in the player's line or a STATUS column in every table where they appear, pale red for OUT/IR/season-ending and pale gold for QUESTIONABLE/DAY_TO_DAY.

BYES AND SCHEDULE

Read nfl_schedule.json for bye weeks and opponents. Never use web research for a bye or an opponent. bye_weeks_by_abbrev and byes_by_scoring_period give the league-wide picture; every player record already carries bye_week inline; teams[pro_team_id].games_by_scoring_period gives opponents with kickoff_ct. A scoring period absent from a team's game map is that team's bye.

jason_x.json→bye_pressure already names which Jason X starters are on bye in each of the next two weeks. Use it rather than deriving byes yourself.

FANTASY RELEVANCE

Prioritize injuries/status, starter and depth-chart changes, snaps/routes/targets/carries, red-zone and goal-line usage, teammate injuries, meaningful coach comments, breakout evidence, start/sit and ROS value. No padding.

PDF STYLE — COLOR REQUIRED

White analyst dashboard, dark text, strong headings, neutral separators. Green=ADD NOW/START/POSITIVE/MAKE A MOVE; pale gold=CONSIDER/WATCH/MIXED; blue-gray=HOLD/MONITOR/INFO; gray=PASS/NO ACTION; pale red=OUT/NEGATIVE/AVOID. Sections 1+9 prominent colored decision cards; Sections 2/3/5/6 subtle colored status or decision badges. Section 7 is a deliberate white-panel exception. Section 4A uses yellow only for Jason X ownership. An essentially monochrome actionable report FAILS visual validation. No orphan headings, broken tables or cards, blank continuation pages, clipping or tiny text.

1. EXECUTIVE SUMMARY — AT A GLANCE

Dashboard: OVERALL, TOP WAIVER, BEST MOVE, BEST STASH, BIGGEST ISSUE, LINEUP CHANGE, BEST TRADE, FAAB POSITION, INJURY WATCH. FAAB POSITION shows Jason's remaining, his rank among the 12, and total dollars committed by today's recommendations. INJURY WATCH names any Jason X starter with a designation, plus any Jason X player on NFL IR holding an active roster spot. Every acquisition shows FREE AGENT $0 or WAIVERS suggested bid, and the drop when has_open_bench_spot is false.

2. MY PLAYERS

Only Jason X players with meaningful fresh development. Card: What happened; Confirmed news when reliable; optional forum sentiment; Fantasy impact; ROS outlook; Action START/BENCH/HOLD/WATCH/SELL HIGH/BUY LOW/ADD BACKUP/MONITOR INJURY. Show designation and expected return for anyone carrying one.

3. FRESH FREE-AGENT ACTIVITY

What changed since yesterday that could create an opportunity? Start from the live Bettor In Green recently-updated index when accessible, then independently verify material facts. Include only positively VERIFIED AVAILABLE players with meaningful fresh development. Fields: Player/pos/team; WHAT CHANGED; CONFIRMED NEWS; optional forum sentiment; FANTASY IMPLICATION; ROSTER RELEVANCE; VERIFIED AVAILABLE; STATUS (designation + expected return, or healthy); ACQUISITION COST (FA $0 or WAIVERS suggested bid + waiver_process_ct); DECISION. If none, say so.

4. POSITION WATCHLIST

Use top_available.json as the candidate pool; fall back to available_players.json only if it is missing. Rank the top 3 trustworthy QB/RB/WR/TE/DST/K by role, usage, volume, security, competition, environment, floor/ceiling, schedule, ROS, contingency and Jason fit. D/ST over the next 2-4 weeks under this scoring; don't overstate K. Rows: `1 | Player · Team | Grade | Availability | L3 RANK | Bye | Status | Decision` + WHY.

L3 RANK is the player's standing in Section 4A. Take it from top_available.json: when recent_form_rank is present render it as `[POS] #[rank] ([avg])`, for example `RB #36 (10.00)`. When it is absent use recent_form_note verbatim, which distinguishes three different situations and must not be flattened to "unranked": `outside the ranked top [N]`, `fewer than 3 games played`, and `excluded from the rankings: out for the season`. Never compute, infer or estimate a rank, and never renumber: the rank is the player's position in the Section 4A table and the two must agree exactly.

Acquisition cost moves out of the row and into WHY: state FREE AGENT $0 or the suggested whole-dollar bid plus waiver_process_ct there. Sections 1, 5 and 9 still carry the exact bid, so the FAAB gates remain satisfiable.

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

5. TWO-WEEK STARTING-LINEUP CHALLENGERS & BYE-WEEK STASHES

A Jason X lineup pressure test, NOT an NFL schedule summary. Ask whether anyone currently available could start for Jason X in the next two weeks, replace an injury or bye, outperform a likely starter, or provide valuable protection.

DO NOT LIST IRRELEVANT NFL MATCHUPS. Every matchup shown must attach directly to a Jason X player or replacement, or to a verified challenger or stash, and must help a start/bench/add/hold/stash decision.

Analyze NEXT FANTASY WEEK and the FOLLOWING FANTASY WEEK separately. Build the likely lineup from jason_x.json; opponents and byes come from the inline bye_week and upcoming fields plus nfl_schedule.json. Account for injuries, returns and byes using the snapshot's designations and expected returns. Identify the best rostered replacement first, then compare the best verified available challenger. Matchup is one factor, not the sole driver. For byes use bye_pressure and prefer a rostered replacement; use BYE-WEEK NEED only when the roster is inadequate. For injuries show the designation, expected return, best rostered replacement, best verified challenger and the exact move trigger. A challenger must have a realistic chance to start for Jason within two weeks. Weigh acquisition cost.

Each week's table: `SLOT | JASON X STARTER / REPLACEMENT | STATUS | MATCHUP | BEST VERIFIED AVAILABLE CHALLENGER | CHALLENGER MATCHUP | BID | EDGE | ACTION`. STATUS carries the designation and expected return where present.

List the slots in exactly this fixed order, one row each, in both weekly tables:

QB, RB, RB, WR, WR, TE, FLEX, K, D/ST

This is jason_x.json→starting_slot_order and jason_x.json→starters is already sorted into it, so follow that order rather than ESPN's. Always show all nine rows even when a slot needs no action, so the lineup reads the same way every day. Never reorder by position strength, by challenger quality or by whether a move is recommended.

EDGE exactly one of: `JASON X ROSTER`, `CHALLENGER`, `CHALLENGER IF [CONDITION]`, `CLOSE`, `MATCHUP DEPENDENT`, `BYE-WEEK NEED`. Player names never belong in EDGE. If Jason is clearly better: challenger and BID `—`, EDGE JASON X ROSTER, action START/HOLD/NO MOVE.

Stashes require credible workload increase, teammate injury, starter promotion, schedule, an upcoming Jason bye, injury insurance, improving usage or contingency value; must be verified FREE AGENT or WAIVERS; and must show cost.

End exactly:

`BEST CHALLENGER NEXT WEEK:` player/NONE

`PLAYER MOST AT RISK OF LOSING A START:` Jason player/NONE

`BEST TWO-WEEK STASH:` available player/NONE

`BYE-WEEK NEED:` position/player/week/NONE

`INJURY CONTINGENCY:` player, replacement, trigger/NONE

`ADD NOW:` YES — [player + bid + reason] or NO — [reason]

`FAAB COST OF RECOMMENDED MOVES:` total/NONE

`FAAB AFTER MOVES:` balance + rank/UNCHANGED

Recheck every Section 5 available player immediately before rendering. Quality check: every matchup shown must help Jason decide start/bench/add/hold/stash.

6. TOP 5 FREE-AGENT ROSTER VALUE UPGRADES

Rank the top 5 VERIFIED AVAILABLE by incremental value. Fields: Rank; FA/Pos/Team; status; Grade; Comparison Player; CLEAR/MODERATE/SLIGHT UPGRADE / ROUGHLY EQUAL / DOWNGRADE; Incremental VERY HIGH/HIGH/MODERATE/LOW; L3 RANK; CONTESTED RISK LOW/MEDIUM/HIGH + why; injury designation and expected return where present; bye week; rationale; short-term; ROS; bye/injury value; drop or open spot; ADD NOW/CONSIDER/WATCH/PASS; confidence. Do not force five positive adds.

L3 RANK replaces the former acquisition-cost column and follows the same rule as Section 4: `[POS] #[rank] ([avg])` from top_available.json's recent_form_rank, or recent_form_note verbatim when absent. It must match that player's rank in the Section 4A table exactly.

Cost still has to appear, because a reader of this section is deciding whether to spend. Put FREE AGENT $0 or the suggested and max whole-dollar bid in the rationale text rather than its own column, and keep the closing totals.

End with BEST OVERALL VALUE, BEST REALISTIC ADD/DROP, VALUE GAIN, WHY, TOTAL FAAB COMMITTED, FAAB REMAINING AFTER.

7. BETTOR IN GREEN — OUTLOOK THREAD GOSSIP

Use the live Recently Updated index. Attempt up to 10 readable individual-player Outlook threads; 1-9 accessible is sufficient and should be called `latest accessible Bettor In Green Outlook threads`. Preserve the retrieved order, skip inaccessible threads, never infer content. If zero, mark Section 7 unavailable and use no forum-derived recommendations; that alone is nonfatal to overall PASS.

Resolve all selected gossip players as a batch through the current player_index. Every header carries exactly one allowed ownership label; no rostered player as a waiver or add. Any actionable add carries its acquisition cost and any injury designation.

PDF: plain white panels. Strong player line + ownership; scan line `Forum: ... · Relevance: ... · Takeaway: ...`; thread title and recency, then exactly TWO concise paragraphs: WHAT THE FORUM THINKS and INDEPENDENT ANALYSIS. End with the most likely actionable item, why, and what would confirm it.

8. LEAGUE POSITION STRENGTH RANKINGS, NEEDS & SURPLUSES

Evaluate all 12 teams from reconciled rosters plus current ROS. Grade complete QB/RB/WR/TE/K/DST rooms plus BIGGEST NEED and SURPLUS. Use starter quality, depth, volume, role security, injuries and expected returns, depth chart, usage, red-zone, environment, schedule, byes, floor/ceiling and replacement quality; don't overweight one game.

Rate every position on a 1-10 scale where 10 is the best in the league and 1 is the worst. Use whole numbers only.

For EACH position independently: exactly one team scores 10 and exactly one team scores 1. The remaining ten teams score 2 through 9, and duplicates are allowed there, so two teams can both be 7. Spread the middle genuinely rather than clustering everything on 5 or 6 — if two rooms are clearly different, their numbers should differ. Rate each position across the league, not against the team's own other positions, so a team can hold the 10 at one position and the 1 at another.

RB/WR evaluate full rooms. D/ST especially sacks, takeaways, TD potential, points and yards allowed, ROS opponents and streaming alternatives under this scoring.

Matrix: `TEAM | QB | RB | WR | TE | K | D/ST | FAAB | WAIVER RANK | BIGGEST NEED | SURPLUS`, all 12, emphasizing Jason. If too wide, FAAB and WAIVER RANK may move to an adjacent compact table, never dropped. Treat spending power as part of the outlook: a weak room with a full budget can fix itself, a strong room that has spent down cannot. Leaders and laggards must match the matrix.

Jason summary: STRENGTHS, BIGGEST NEED, SURPLUS, BEST TRADE LOGIC, FAAB LEVERAGE. FAAB LEVERAGE states remaining, rank, how many teams can outbid him, and which contenders have both a relevant need and the budget. Top 3 natural trade partners should preferably satisfy Jason surplus→their need AND their surplus→Jason need; one-directional fits need explanation. Do not manufacture offers. Include league-wide scarcity and Jason's leverage where meaningful.

9. WHAT I WOULD DO TODAY

Maximum 3 highest-EV actions across waivers, holds, lineup, injury contingencies and trades. Every ADD states FA $0 or the exact waiver bid, the claim's waiver_process_ct, the resulting FAAB balance, and the drop unless has_open_bench_spot is true. If a Jason X player is on NFL IR while holding an active roster spot, moving him to the IR slot is a free roster spot and should be one of the three actions. Prefer HOLD over churn. If there is a trade fit but no compelling offer: `EXPLORE TRADE WITH [TEAM]` with the target position, the offer from surplus, and why. If none: `NO ADD/DROP OR TRADE RECOMMENDED TODAY.` A named trade target must be confirmed owned. Use prominent color-coded cards.

10. SOURCES

Concise: GitHub ESPN snapshot, official/team, beat/news, fantasy analysis, Bettor In Green. No ESPN IDs or tool commentary.

FINAL VALIDATION

A gates.json read; data_status honoured; snapshot integrity, timestamp, scoringPeriodId, team/roster/available completeness confirmed; non-critical failures stated rather than hidden.

B indexed = rostered + available on the same snapshot; every file read shares one league_id, season and fetched_at.

C Jason X lineup taken from jason_x.json, not rebuilt; starters, bench, IR, open spots and D/ST consistent with it.

D all named players in Sections 3-9 resolved via player_index.

E rendered ownership compared to players_by_id; require `OWNERSHIP MISMATCHES = 0`; correct and recalculate before the PDF.

F every acquisition in Sections 1, 3-6 and 9 has positive current availability.

G Section 8 all 12 teams exactly once; every rating a whole number 1-10; for each of QB, RB, WR, TE, K and D/ST exactly one team scores 10 and exactly one scores 1, with the other ten between 2 and 9 and duplicates allowed there; needs and surpluses supported; leaders and laggards match the matrix, with the leader being the 10 and the laggard the 1. Require `SECTION 8 SCALE ERRORS = 0`.

H Jason's Section 8 entry consistent with his current roster.

I trade fits follow the matrix.

J named trade targets owned by the stated team.

K transactions used as narrative only.

L Section 7: at least one thread passes, zero is nonfatal, no fabricated content.

M ownership, status and designations identical across sections.

N PDF visually inspected for layout defects and required colors; an essentially monochrome actionable report FAILS.

O FAAB integrity from gates.faab_arithmetic; balances quoted from the ledger, never summed from bids.

P acquisition cost consistent with status: FA = $0 and no bid; WAIVERS = whole-dollar bid >= minimum_bid. Require `ACQUISITION COST ERRORS = 0`.

Q total recommended bids <= Jason's remaining; any reserve-floor breach explicitly flagged.

R `DROP NONE — OPEN ROSTER SPOT` only when has_open_bench_spot is true; otherwise a specific drop is named.

S FAAB figures, waiver ranks and processing times identical everywhere.

T Section 4A matches recent_form_rankings.json exactly: six tables in order QB, RB, WR, TE, K, D/ST; each row count equals counts.ranked; rank, name, pos, team, weeks, points, average and owner verbatim; owner_label not rewritten; every game cell in the exact form `W8 · 24.60`; no padded, invented or dropped rows; order unchanged; a yellow row for every is_jason_x true entry and no other, including none for FREE AGENT, WAIVERS or UNKNOWN. No player in excluded_out_for_season appears in any table, and the excluded count is stated. Require `RECENT FORM HIGHLIGHT ERRORS = 0`, `RECENT FORM COUNT ERRORS = 0` and `RECENT FORM FORMAT ERRORS = 0`, and confirm the legend `Yellow = Jason X roster` sits immediately above the first table.

X Every L3 RANK shown in Sections 4 and 6 matches that player's rank in the Section 4A table for the same position, or is the file's recent_form_note verbatim when unranked; no rank is computed, estimated or renumbered; the three unranked reasons are never flattened into one. Section 5 lists its nine slots in the order QB, RB, RB, WR, WR, TE, FLEX, K, D/ST in both weekly tables. Require `RANK CROSS-REFERENCE ERRORS = 0`.

U Section 4A PDF visual inspection, which is where highlighting is actually verified: yellow pale and unsaturated, text dark and legible, visible in colour print, identical in all six tables; headers repeat on continuation pages; no player row split across a page break; numeric columns right-aligned with two decimals; no blank pages, clipping, tiny text or orphan headings; table order intact after pagination.

V injury flags consistent and complete: every player with an `injury` block shows their designation; every on_nfl_ir true player marked IR; IR-R never shown as plain IR; stated returns match expected_return_label; `out for the season` never rendered as a date; nobody labelled out for the season recommended as an add, start or stash; nobody on NFL IR recommended to start; designations identical wherever a player appears. Require `INJURY FLAG ERRORS = 0`. A missing injuries.json is nonfatal, means no return dates, and must be stated.

W byes and opponents taken from nfl_schedule.json and the inline bye_week, never from web research; every bye claim matches bye_weeks_by_abbrev; Section 5 matchups all attach to a Jason X decision.

FINAL PASS

PASS only if gates.data_status is PASS and the index, roster, ownership, acquisition, FAAB, injury, cross-section, Section 4A, Section 8 and PDF gates pass. Bettor In Green partial or unavailable alone is nonfatal; a missing injuries.json, nfl_schedule.json or recent_form_rankings.json is nonfatal but must be stated. Otherwise FAIL with a brief material reason.

PDF OUTPUT

Return a concise chat summary plus a polished downloadable PDF titled `Jason X - Daily Fantasy Football Activity & Waiver Report.` Include the date and Sections 1-10 plus 4A. White dominates but status and decision colors are required. Bid amounts, FAAB balances, return dates and three-game averages must be legible and consistent. No clipping, broken tables, awkward breaks, ESPN IDs, vague ownership placeholders or contradictions.

EMAIL DELIVERY — REQUIRED

After the final PDF passes all validation, email the actual final PDF attachment to `jason.prinsen@gmail.com` using connected Gmail. Subject `Jason X - Daily Fantasy Football Report - [report date]`. Concise body with DATA STATUS and a note that the full report is attached. Never email an intermediate, failed, stale or superseded PDF; if generation or validation fails, report the failure instead.
````
