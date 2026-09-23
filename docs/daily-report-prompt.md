# Jason X — Daily Fantasy Report Prompt

The prompt used to generate the daily Jason X activity and waiver report from the
snapshot this repo publishes. Copy the fenced block below verbatim.

It reads the committed snapshot in `data/` as authoritative league state. See the
[README](../README.md) for what each file contains, how to resolve a player name
through `player_index.json`, and why FAAB remaining must come from ESPN's ledger
rather than a sum of bid amounts.

## What the FAAB revision changed

FAAB support was added to the snapshot after this prompt was first written, so the
prompt was revised to use it. The substantive changes:

- **Availability and cost are now separate questions.** `FREE AGENT` is a $0
  instant add; `WAIVERS` requires a FAAB bid, processes later, and can be outbid.
  The earlier prompt treated all available players as equally addable. That is
  materially wrong here — snapshots regularly show **zero** free agents, meaning
  every add costs budget and is contested.
- **An explicit bid ladder**, so recommended bids are consistent instead of
  invented per run.
- **A contested-bid check**, because Jason X is weak on both spending power and
  waiver rank, so bidding at market value tends to lose.
- **Roster-spot reality check.** `DROP NONE — OPEN ROSTER SPOT` is only valid when
  `bench_open` is actually greater than zero; Jason X is frequently full.
- **FAAB as a competitive dimension in Section 8**, since a weak position group
  with a full budget can be fixed and a strong one that has spent down cannot.
- **New validation gates O–S** covering FAAB arithmetic, cost/status consistency,
  total spend against remaining balance, and drop accuracy.

One caveat encoded in the prompt: quote each player's `waiver_process_date` rather
than the league-level `waiver_process_hour`. Those two have not agreed in observed
snapshots, and the per-player timestamp is explicit and unambiguous.

## The prompt

````text
Create the daily Jason X fantasy-football activity + waiver report for my 12-team ESPN redraft league, focused mainly on the last ~24 hours.

AUTHORITATIVE DATA & OWNERSHIP

Use connected GitHub repo JasonTheLucky/fantasyfootball as authoritative ESPN league state. Read data/metadata.json first, then data/SNAPSHOT.md and data/player_index.json before player-level analysis; use ownership.json, rosters.json, available_players.json, transactions.json, matchups.json and league.json as needed. Confirm snapshot integrity, completeness, current scoringPeriodId and convert snapshot time to CT. Never trigger GitHub Actions. GitHub alone determines this league's roster/ownership/availability. Web research supplies current NFL/team/beat/fantasy news and Bettor In Green sentiment. External articles, roster percentages and forums never establish availability.

FAAB is authoritative from the same snapshot. Read league.json → faab for budget_per_team, minimum_bid, acquisition_type and waiver settings. Read each team's faab block in rosters.json (budget, spent, remaining, percent_remaining) and ownership.json → team_needs → faab plus waiver_rank. These come from ESPN's own per-team ledger and are the only figures to quote. NEVER sum bid_amount from transactions.json to derive remaining budget. Failed claims (status beginning FAILED_) carry real bid amounts but were never charged, and ESPN's ledger can legitimately disagree with a sum of winning bids. Use transactions.json → waiver_bids (each with a succeeded flag) for narrative only: who bid what, who lost a claim, and observed market prices.

DATA STATUS

Show only `DATA STATUS: PASS · ESPN snapshot [time] CT` or `DATA STATUS: FAIL · ESPN snapshot [time] CT — [brief material failure]`. PASS requires successful snapshot/index/ownership/FAAB/final assertion/PDF gates.

Use player_index.json as primary identity + ownership resolver. Validate rostered + available = indexed on the same successful snapshot. For every named player in Sections 3-9 resolve name using BOTH documented punctuation-as-separator and punctuation-deleted normalization forms plus suffix-stripped variants → by_normalized_name → ESPN ID → players_by_id → fantasy_status/fantasy_team. Use team+position to resolve collisions. Never force weak matches or infer availability from roster absence. Allowed labels only: `OWNED: JASON X`, `OWNED: [Fantasy Team Name]`, `FREE AGENT`, `WAIVERS`, `OWNERSHIP UNRESOLVED — [specific reason]`. Never recommend unresolved players.

Before rendering Sections 3-9 build one authoritative resolved_players map for EVERY named player. Immediately before final PDF re-read players_by_id for every player treated as FREE AGENT, WAIVERS, VERIFIED AVAILABLE, ADD, ADD NOW, CLAIM, WAIVER TARGET, STASH or otherwise obtainable without trade. If ROSTERED, remove/reclassify and recalculate affected sections. Repeat until `OWNERSHIP MISMATCHES = 0`. Transactions are audit/narrative only; never replay them over current state. If player_index alone is invalid but snapshot valid, deterministic fallback may use complete ownership+rosters+available files. Rebuild Jason X starters/bench/IR/open spots and verify D/ST every run.

LEAGUE

1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, 1 D/ST, 1 K, 7 bench, 1 IR. Full PPR. Passing .04/yd, 4 TD, -2 INT, +2 300-399, +4 400+, +2 50+ TD. Rushing .1/yd, 6 TD, +2 100-199, +4 200+, +2 50+ TD. Receiving 1 PPR, .1/yd, 6 TD, same bonuses. K PAT1, missed FG -1, FG 0-39=3, 40-49=4, 50-59=5, 60+=6. D/ST strongly rewards sacks/turnovers/TD/low points and especially low yardage; penalizes high points/yards.

FAAB & ACQUISITION COST

Availability and cost are different questions. Resolve both for every acquisition.

FREE AGENT  = $0, instant, uncontested. No bid. Add immediately.
WAIVERS     = requires a FAAB bid, processes later, and can be outbid.

Never recommend a bid on a FREE AGENT, and never describe a WAIVERS player as a free or instant add. If the pool contains zero free agents, say so plainly — it means every add this cycle costs budget and is contested.

For each WAIVERS recommendation state: SUGGESTED BID, MAX BID, and the processing time taken from that player's waiver_process_date converted to CT. Prefer waiver_process_date over the league-level waiver_process_hour; the per-player timestamp is explicit and the two have not always agreed. Bids must be whole dollars, at least minimum_bid, and never exceed Jason X's remaining balance.

Bid ladder, as a share of the original budget_per_team:
  Clear every-week starter / league-altering role change   25-40%
  Reliable weekly starter at a position of need            10-20%
  Flex or high-upside speculative with a real path          3-8%
  Stash, injury insurance, bye cover                        1-3%
  Pure lottery ticket, unlikely to be contested             minimum viable bid

minimum_bid may be $0, so $0 claims can be legal, but ties break on waiver_rank. Bid at least $1 on anything genuinely wanted whenever Jason X's waiver_rank is not strong.

Budget discipline: do not drop Jason X below roughly 20% of the original budget before mid-season; that reserve is injury-replacement capacity. Flag explicitly when a recommendation would breach it.

Contested-bid check: compare Jason X's remaining balance and waiver_rank against the other 11 teams. State how many teams can outbid him and name the plausible competitors for that specific player given their roster needs. When most of the league can outbid him, a bid at market value is likely to lose — either bid above market or recommend a cheaper alternative. Say which.

Verify open bench and IR spots from the current snapshot every run. Only use `DROP NONE — OPEN ROSTER SPOT` when bench_open is actually greater than zero; otherwise every add must name a specific drop, and the drop must be justified against the incoming player.

FANTASY RELEVANCE

Prioritize injuries/status, starter changes, depth chart, snaps/routes/targets/carries, red-zone/goal-line, teammate injuries, meaningful coach comments, transactions, breakout evidence, start/sit and ROS value. No routine padding.

PDF STYLE — COLOR IS REQUIRED, NOT OPTIONAL

Three reading speeds: Sections 1+9 = 5-second; 2/3/6/high-relevance gossip = 2-minute; 4/5/full7/8/Sources = deep read. Clean white analyst dashboard, dark text, strong headings, neutral separators, efficient whitespace. White dominates, but decision/status color MUST be visibly used throughout. Green = ADD NOW/START/POSITIVE/MAKE A MOVE; pale gold = CONSIDER/WATCH/MIXED; blue-gray = HOLD/MONITOR/INFO; gray = PASS/NO ACTION; pale red = OUT/NEGATIVE/AVOID. Sections 1 and 9 must have prominent colored decision cards/badges. Sections 2, 3, 5 and 6 must use subtle colored status headers or decision badges. Tables may remain white/light-gray but important decisions must use the defined color system. Section 7 is the deliberate exception: white content panels with restrained text/labels rather than colored panels. An essentially monochrome report with actionable recommendations FAILS PDF visual validation and must be corrected before delivery. No orphan headings, broken cards/tables, nearly blank continuation pages, clipping or tiny text.

1. EXECUTIVE SUMMARY — AT A GLANCE

Determine most important roster development, best currently available waiver player, best add/drop, best two-week stash, best trade logic if genuinely compelling, and overall MAKE A MOVE/HOLD ROSTER/MONITOR. Compact dashboard: OVERALL, TOP WAIVER, BEST MOVE, BEST STASH, BIGGEST ISSUE, LINEUP CHANGE, BEST TRADE, FAAB POSITION.

FAAB POSITION shows Jason X remaining, rank among the 12 teams, and total dollars committed by today's recommendations. Every acquisition card must carry its cost — FREE AGENT $0 or WAIVERS with the suggested bid — and must pass the ownership gate. Where no bench spot is open, the card must name the drop.

2. MY PLAYERS

Only current Jason X players with meaningful fresh development. Each card: What happened; Confirmed news when reliable; optional forum sentiment; Fantasy impact; ROS outlook; Action START/BENCH/HOLD/WATCH/SELL HIGH/BUY LOW/ADD BACKUP/MONITOR INJURY. Use subtle colored status header and prominent color-coded Action.

3. FRESH FREE-AGENT ACTIVITY

What changed since yesterday that could create a waiver opportunity? Start from live Bettor In Green recently updated index when accessible; broadly scan waiver/streaming, injuries, Outlooks and player/general threads around 24h. Forum is discovery/sentiment only. Independently verify material facts. Include only positively VERIFIED AVAILABLE players with meaningful fresh development. Fields: Player/pos/team; WHAT CHANGED; CONFIRMED NEWS; optional forum sentiment; FANTASY IMPLICATION; ROSTER RELEVANCE; VERIFIED AVAILABLE; ACQUISITION COST (FREE AGENT $0, or WAIVERS with suggested bid and processing time in CT); DECISION. If none, say so.

4. POSITION-BY-POSITION FREE-AGENT WATCHLIST

Evaluate entire current verified available pool and rank top 3 trustworthy QB/RB/WR/TE/DST/K by role, usage, volume, security, competition, environment, floor/ceiling, schedule, ROS, contingencies and Jason fit. Candidate universe must originate from positively available pool. D/ST next 2-4 weeks under league scoring; don't overstate K. Compact rows `1 | Player · Team | Grade | Availability | Bid | Decision` + WHY. Bid is `$0 FA` for free agents or the suggested whole-dollar bid for waiver players. If fewer than 3 trustworthy candidates, say so.

5. TWO-WEEK STARTING-LINEUP CHALLENGERS & BYE-WEEK STASHES

Purpose: answer `Over the next two fantasy weeks, is anyone currently available worth adding because they could start for Jason X, replace an injured/bye-week player, outperform one of my likely starters, or provide valuable advance protection?` This is a Jason X lineup pressure test, NOT an NFL schedule summary.

DO NOT LIST IRRELEVANT NFL MATCHUPS. Never summarize schedules as generic team-vs-team prose. Every matchup displayed must attach directly to a current Jason X player, projected starter, rostered injury/bye replacement, positively verified available challenger, or positively verified available two-week stash being seriously considered. If a matchup does not affect a Jason X lineup, roster, injury contingency, bye-week or acquisition decision, omit it. The reader should never have to translate NFL teams back into Jason X players.

Analyze NEXT FANTASY WEEK and FOLLOWING FANTASY WEEK separately. For each: build most likely Jason X lineup from current authoritative roster; account for injuries/status/expected returns/byes; identify best current roster replacement first; only then search positively verified available pool for a material improvement; compare starter/replacement directly with challenger; matchup is one factor, not sole driver; do not force challengers.

For byes: best rostered replacement first, then compare with best verified available alternative; use BYE-WEEK NEED only if roster lacks adequate replacement. For injuries: show injured player/status, best rostered replacement, best verified available challenger if one exists, and exact trigger for move. Prefer conditional recommendations such as `ADD [player] only if [starter] is ruled out`.

Pressure-test player quality, role, volume, usage, injury, matchup, environment, floor/ceiling, league scoring and likelihood Jason would genuinely start the challenger. A player is a challenger only if there is a realistic start scenario in the next two weeks. Weigh acquisition cost: a marginal upgrade is not worth a large bid, and a cheap add with a real start path can be.

REQUIRED TABLE — ONE FOR EACH WEEK:

`SLOT | JASON X STARTER / REPLACEMENT | STATUS | MATCHUP | BEST VERIFIED AVAILABLE CHALLENGER | CHALLENGER MATCHUP | BID | EDGE | ACTION`

EDGE exactly one of: `JASON X ROSTER`, `CHALLENGER`, `CHALLENGER IF [CONDITION]`, `CLOSE`, `MATCHUP DEPENDENT`, `BYE-WEEK NEED`. Player names never belong in EDGE. If Jason X clearly better, challenger = `—`, BID = `—`, EDGE = JASON X ROSTER, action START/HOLD/NO MOVE.

After both weeks identify true advance stashes only when justified by workload increase, teammate injury, starter promotion, schedule, upcoming Jason bye need, injury insurance, improving usage or contingency value. Every stash must be positively verified FREE AGENT or WAIVERS, and each must show its cost.

End Section 5 exactly with:

`BEST CHALLENGER NEXT WEEK:` player or NONE.

`PLAYER MOST AT RISK OF LOSING A START:` Jason X player or NONE.

`BEST TWO-WEEK STASH:` verified available player or NONE.

`BYE-WEEK NEED:` affected position/player/week or NONE.

`INJURY CONTINGENCY:` specific player, replacement and trigger or NONE.

`ADD NOW:` YES — [player + bid + reason] or NO — [reason].

`FAAB COST OF RECOMMENDED MOVES:` total dollars, or NONE.

`FAAB AFTER MOVES:` remaining balance and rank, or UNCHANGED.

Every Section 5 FA/waiver player must already exist in resolved_players and be rechecked immediately before rendering. If ROSTERED, remove and recalculate. No unresolved challenger/stash/acquisition. Quality check: `Does every NFL matchup shown help Jason decide whom to start, bench, add, hold or stash?` If no, remove it.

6. TOP 5 FREE-AGENT ROSTER VALUE UPGRADES

Rank top 5 VERIFIED AVAILABLE by incremental value to Jason X. Fields: Rank; FA/Pos/Team; verified status; Grade; Comparison Player; CLEAR/MODERATE/SLIGHT UPGRADE / ROUGHLY EQUAL / DOWNGRADE; Incremental VERY HIGH/HIGH/MODERATE/LOW; ACQUISITION COST (FREE AGENT $0 or WAIVERS with suggested and max bid); CONTESTED RISK LOW/MEDIUM/HIGH with reasoning; rationale; short-term; ROS; bye/injury value; drop/open spot; ADD NOW/CONSIDER/WATCH/PASS; confidence. Do not force five positive adds. End BEST OVERALL VALUE, BEST REALISTIC ADD/DROP, VALUE GAIN, WHY, TOTAL FAAB COMMITTED, FAAB REMAINING AFTER.

7. BETTOR IN GREEN — OUTLOOK THREAD GOSSIP

Use live Bettor In Green index sorted Recently Updated. Attempt up to 10 most recently updated readable individual-player Outlook threads; exclude generic/non-player. 1-9 accessible is sufficient and should be called `latest accessible Bettor In Green Outlook threads`. Preserve reliably retrieved order; skip inaccessible threads and never infer content. If zero qualifying threads can be reliably retrieved, mark Section 7 unavailable and use no forum-derived recommendations. Zero is supplemental-source unavailability and does NOT independently fail overall DATA STATUS if authoritative gates pass.

Resolve ALL selected gossip players as a batch through current player_index before rendering, using all documented normalization variants and team+position collision resolution. Every header exactly one allowed ownership label. No rostered player as waiver/add. Any actionable add surfaced here carries cost per the FAAB & ACQUISITION COST rules.

Section 7 PDF: plain white, no colored content panels. Strong player line + ownership. Scan line `Forum: ... · Relevance: ... · Takeaway: ...`. Thread title/recency + exactly TWO concise paragraphs: WHAT THE FORUM THINKS and INDEPENDENT ANALYSIS. End most likely actionable/why/what confirms.

8. LEAGUE POSITION STRENGTH RANKINGS, NEEDS & SURPLUSES

Evaluate ALL 12 teams from current reconciled rosters + current ROS information. Grade complete QB/RB/WR/TE/K/DST rooms plus BIGGEST NEED and SURPLUS POSITION. Use starter quality, depth, volume, role security, injuries/returns, suspensions, depth chart, snaps/routes/targets/carries, red-zone, environment, QB/OL, schedule, byes, floor/ceiling, breakout/regression, contingency and replacement quality. Do not overweight one game.

Position scale exactly `1-Great`, `2-Strong`, `3-Average`, `4-Weak`. For EACH position exactly ONE team = 1-Great and exactly ONE = 4-Weak; all others Strong/Average. Internally order all 12. RB/WR evaluate full rooms. D/ST especially consider sacks, takeaways, TD potential, points/yards allowed, ROS opponents and streaming alternatives under this scoring.

Primary matrix: `TEAM | QB | RB | WR | TE | K | D/ST | FAAB | WAIVER RANK | BIGGEST NEED | SURPLUS`, all 12 teams; subtly emphasize Jason X; Section 8 alone may landscape. If width becomes a problem, FAAB and WAIVER RANK may move to a compact adjacent table rather than being dropped. Treat spending power as part of each team's outlook: a weak room with a full budget can fix itself, a strong room that has spent down cannot. Below matrix, POSITION LEADERS & LAGGARDS must exactly match matrix.

Jason summary: STRENGTHS, BIGGEST NEED, SURPLUS, BEST TRADE LOGIC, FAAB LEVERAGE. FAAB LEVERAGE states his remaining balance, his rank, how many teams can outbid him, and which contenders have both an unmet need at a position Jason also wants and the budget to win it. TOP 3 NATURAL TRADE PARTNERS should preferably satisfy `JASON X SURPLUS → THEIR NEED` AND `THEIR SURPLUS → JASON X NEED`; one-directional fits require explanation. Do not manufacture specific offers. Include league-wide scarcity/surplus and Jason leverage when meaningful.

9. WHAT I WOULD DO TODAY

Maximum 3 highest-EV actions across waivers, holds, lineup/prep, injury contingencies and trade exploration. Show ADD/DROP or, only if genuinely compelling, GIVE/RECEIVE/REASON. Every ADD states cost — FREE AGENT $0 or WAIVERS with the exact whole-dollar bid — plus when the claim processes in CT, and the resulting FAAB balance. Use `DROP NONE — OPEN ROSTER SPOT` only when bench_open is greater than zero; otherwise name the drop. Prefer HOLD over churn. If promising trade fit but no compelling offer, use `EXPLORE TRADE WITH [TEAM]` with target position/offer from surplus/why. If none: `NO ADD/DROP OR TRADE RECOMMENDED TODAY.` Every add passes final ownership assertion; named trade target must be confirmed owned. Use prominent color-coded action cards.

10. SOURCES

Concise: GitHub ESPN snapshot, official/team, beat/news, fantasy analysis, Bettor In Green. No ESPN IDs or implementation/tool commentary.

FINAL VALIDATION — MANDATORY

A re-read metadata and validate snapshot integrity/current timestamp/scoringPeriodId/team/roster/available completeness.

B indexed = rostered + available, same successful snapshot.

C rebuild Jason X starters/bench/IR/open spots/DST.

D every unique named player Sections 3-9 resolved through current player_index.

E compare actual rendered ownership label to players_by_id; require `OWNERSHIP MISMATCHES = 0`; any mismatch => STOP/correct/reclassify/recalculate/rerun.

F every acquisition in Sections 1,3-6,9 requires positive current availability.

G Section 8 all 12 exactly once; each position exactly one Great and one Weak; others Strong/Average; comparative ROS; RB/WR full rooms; needs/surpluses supported; leaders/laggards match.

H Jason Section 8 consistent with current roster.

I trade fits follow matrix.

J named trade targets resolve to stated team.

K transactions narrative only.

L Section 7: target up to 10; 1+ qualifying thread passes; 1-9 sufficient; zero unavailable but nonfatal if authoritative gates pass; no fabricated inaccessible content.

M ownership/status identical across sections.

N PDF visual inspection: orphan headings, splits, blank continuation pages, grids, clipping, overlaps, tiny text, broken rows/cards, IDs/placeholders and Section 8 matrix. ALSO verify required decision/status colors are visibly present throughout Sections 1-6 and 9. Essentially monochrome actionable report = FAIL and must be corrected. Section 7 is intentional white-panel exception.

O FAAB integrity: faab.enabled is true and for all 12 teams remaining == budget - spent. Report FAAB as FAIL only if this arithmetic breaks.

P every recommended acquisition carries a cost consistent with its resolved status: FREE AGENT means $0 and no bid; WAIVERS means a whole-dollar bid at or above minimum_bid. No bid on a free agent, no free add of a waiver player. Require `ACQUISITION COST ERRORS = 0`.

Q sum of all recommended bids does not exceed Jason X's remaining balance, and any breach of the reserve floor is flagged rather than silent.

R drops match reality: `DROP NONE — OPEN ROSTER SPOT` only when bench_open > 0; otherwise a specific drop is named.

S FAAB figures, waiver ranks and waiver-processing times identical wherever they appear.

FINAL PASS

PASS only if authoritative snapshot/index/roster/ownership/acquisition/FAAB/cross-section/Section8/PDF gates pass. Bettor In Green partial/unavailable alone is nonfatal. Otherwise FAIL with brief material reason.

PDF OUTPUT

Return concise chat summary + polished downloadable PDF titled `Jason X - Daily Fantasy Football Activity & Waiver Report.` Include date and Sections 1-10. White dominates but required status/decision colors must be visibly used. Bid amounts and FAAB balances must be legible and consistent throughout. No clipping, broken tables, awkward page breaks, ESPN IDs, vague ownership placeholders, contradictions or implementation clutter.

EMAIL DELIVERY — REQUIRED

After final PDF passes all required validation, email actual final PDF attachment to `jason.prinsen@gmail.com` using connected Gmail. Subject: `Jason X - Daily Fantasy Football Report - [report date]`. Concise body with DATA STATUS + note full report attached. Do not email intermediate/failed/stale/superseded PDF. If generation/validation fails, do not send misleading email; report failure in scheduled-task result.
````
