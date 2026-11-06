#!/usr/bin/env python3
"""
Render a compact digest of the snapshot produced by scripts/fetch_espn.py.

Writes the digest to three places:
  - stdout, so it appears in the GitHub Actions job log
  - $GITHUB_STEP_SUMMARY, so it appears on the run page
  - data/SNAPSHOT.md, a committed single-file overview

The committed digest matters because a consumer can read one small Markdown file
to confirm freshness and completeness before deciding whether to parse the much
larger JSON payloads.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

DATA_DIR = os.environ.get("ESPN_OUT_DIR", "data")


def load(name: str) -> dict | None:
    path = os.path.join(DATA_DIR, name)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def tick(value: Any) -> str:
    return "yes" if value else "NO"


def build() -> str:
    metadata = load("metadata.json")
    if metadata is None:
        return (
            "# ESPN Fantasy Snapshot\n\n"
            "**The snapshot failed before metadata could be written.** No usable data "
            "was produced by this run; do not treat any file in `data/` as current.\n"
        )

    rosters = load("rosters.json") or {}
    jason_x = load("jason_x.json") or {}
    schedule = load("nfl_schedule.json") or {}
    gates_file = load("gates.json") or {}
    ownership = load("ownership.json") or {}
    transactions = load("transactions.json") or {}
    matchups = load("matchups.json") or {}
    league = load("league.json") or {}
    recent_form = load("recent_form_rankings.json") or {}
    injuries = load("injuries.json") or {}

    critical = metadata.get("critical_success")
    lines: list[str] = []
    add = lines.append

    add("# ESPN Fantasy Snapshot")
    add("")
    add(f"**{'USABLE' if critical else 'NOT USABLE'}** "
        f"- {metadata.get('league_name')} ({metadata.get('season')})")
    add("")
    add("| Field | Value |")
    add("| --- | --- |")
    add(f"| Fetched at (UTC) | `{metadata.get('fetched_at')}` |")
    if metadata.get("fetched_at_ct_label"):
        add(f"| Fetched at (CT) | **{metadata.get('fetched_at_ct_label')}** |")
    if gates_file.get("data_status"):
        add(f"| DATA STATUS | **{gates_file.get('data_status')}** |")
    add(f"| Scoring period (week) | **{metadata.get('scoringPeriodId')}** |")
    add(f"| League id | `{metadata.get('league_id')}` |")
    add(f"| Teams retrieved | {metadata.get('team_count')} / "
        f"{metadata.get('expected_team_count')} |")
    add(f"| Rostered players | {metadata.get('rostered_player_count')} |")
    add(f"| Available players (FA + waivers) | {metadata.get('available_player_count')} |")
    add(f"| Players in lookup index | {metadata.get('player_index_count')} |")
    add(f"| Fetch duration | {metadata.get('duration_seconds')}s |")
    add(f"| ESPN calls | {len(metadata.get('espn_calls') or [])} |")
    run_id = (metadata.get("github") or {}).get("run_id")
    if run_id:
        add(f"| Workflow run | `{run_id}` |")
    add("")

    add("## Freshness and completeness gates")
    add("")
    add("| Gate | Passed |")
    add("| --- | --- |")
    add(f"| League settings retrieved | {tick(metadata.get('league_fetch_success'))} |")
    add(f"| All {metadata.get('expected_team_count')} rosters retrieved | "
        f"{tick(metadata.get('roster_fetch_success'))} |")
    add(f"| Transactions retrieved | {tick(metadata.get('transactions_fetch_success'))} |")
    add(f"| Player pool retrieved | {tick(metadata.get('player_pool_fetch_success'))} |")
    add(f"| Matchups retrieved | {tick(metadata.get('matchup_fetch_success'))} |")
    add(f"| Ownership reconciled, no conflicts | "
        f"{tick(metadata.get('ownership_reconciled'))} |")
    add(f"| Player lookup index complete | {tick(metadata.get('player_index_built'))} |")
    add(f"| Recent form rankings built | {tick(metadata.get('recent_form_success'))} |")
    add(f"| NFL injury report retrieved | {tick(metadata.get('injury_report_success'))} |")
    add(f"| NFL schedule and byes retrieved | "
        f"{tick(metadata.get('nfl_schedule_success'))} |")
    add("")

    errors = metadata.get("errors") or []
    if errors:
        add("### Problems reported")
        add("")
        for message in errors:
            add(f"- {message}")
        add("")

    # ---- matchups -------------------------------------------------------
    games = matchups.get("matchups") or []
    if games:
        add(f"## Week {matchups.get('scoring_period_id')} matchups")
        add("")
        add("| Away | Pts | Home | Pts | Final |")
        add("| --- | --- | --- | --- | --- |")
        for game in games:
            home = game.get("home") or {}
            away = game.get("away") or {}
            add(
                f"| {away.get('team_name')} | {away.get('points')} "
                f"| {home.get('team_name')} | {home.get('points')} "
                f"| {tick(home.get('is_final'))} |"
            )
        add("")

    # ---- rosters --------------------------------------------------------
    teams = rosters.get("teams") or []
    if teams:
        add("## Rosters")
        add("")
        add("| # | Team | Owner | Roster | Start | Bench | IR | Bench open |")
        add("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for team in teams:
            counts = team.get("counts") or {}
            capacity = team.get("capacity") or {}
            owners = ", ".join(team.get("owners") or []) or "-"
            add(
                f"| {team.get('team_id')} | {team.get('team_name')} | {owners} "
                f"| {counts.get('total')} | {counts.get('starters')} "
                f"| {counts.get('bench')} | {counts.get('injured_reserve')} "
                f"| {capacity.get('bench_open')} |"
            )
        add("")

    # ---- FAAB -----------------------------------------------------------
    faab_settings = league.get("faab") or {}
    if faab_settings.get("enabled") and teams:
        budget = faab_settings.get("budget_per_team")
        add(f"## FAAB remaining (budget ${budget} per team)")
        add("")
        add("| Team | Remaining | Spent | % left | Waiver rank |")
        add("| --- | --- | --- | --- | --- |")
        ranked = sorted(
            teams,
            key=lambda t: -((t.get("faab") or {}).get("remaining") or 0),
        )
        for team in ranked:
            faab = team.get("faab") or {}
            add(
                f"| {team.get('team_name')} | **${faab.get('remaining')}** "
                f"| ${faab.get('spent')} | {faab.get('percent_remaining')}% "
                f"| {team.get('waiver_rank')} |"
            )
        add("")
        add(f"Minimum bid ${faab_settings.get('minimum_bid')}. Waivers process at "
            f"{faab_settings.get('waiver_process_hour')}:00 UTC on "
            f"{', '.join(faab_settings.get('waiver_process_days') or [])}. "
            "Waiver rank breaks ties between equal bids.")
        add("")

    # ---- injuries -------------------------------------------------------
    needs = ownership.get("team_needs") or []
    designated = [
        (team.get("team_name"), player)
        for team in needs
        for player in team.get("injury_designations") or []
    ]
    if designated:
        add(f"## Injury designations ({len(designated)})")
        add("")
        add("| Team | Player | Pos | Status | Slot | Starting |")
        add("| --- | --- | --- | --- | --- | --- |")
        for team_name, player in designated:
            add(
                f"| {team_name} | {player.get('name')} | {player.get('position')} "
                f"| {player.get('injury_status')} | {player.get('lineup_slot')} "
                f"| {tick(player.get('is_starting'))} |"
            )
        add("")

    # ---- availability ---------------------------------------------------
    counts = ownership.get("counts") or {}
    by_position = counts.get("available_by_position") or {}
    if by_position:
        add("## Available player pool by position")
        add("")
        add("| " + " | ".join(by_position) + " | Total |")
        add("| " + " | ".join("---" for _ in by_position) + " | --- |")
        add("| " + " | ".join(str(v) for v in by_position.values())
            + f" | {counts.get('available')} |")
        add("")
        add(
            "This pool comes from ESPN's own `filterStatus` of `FREEAGENT` and "
            "`WAIVERS`. Availability is never inferred from a player's absence "
            "from the rosters above."
        )
        add("")

    # ---- Jason X ---------------------------------------------------------
    if jason_x.get("team_name"):
        cap = jason_x.get("capacity") or {}
        faab = jason_x.get("faab") or {}
        rec = jason_x.get("record") or {}
        add(f"## {jason_x['team_name']}")
        add("")
        add(f"Record {rec.get('wins')}-{rec.get('losses')} · FAAB "
            f"**${faab.get('remaining')}** of ${faab.get('budget')} · waiver rank "
            f"{jason_x.get('waiver_rank')} · bench open **{cap.get('bench_open')}** · "
            f"IR open {cap.get('ir_open')}")
        add("")
        if not jason_x.get("has_open_bench_spot"):
            add("No open bench spot: any add needs a drop.")
            add("")
        add("| Slot | Player | Pos | Bye | Next | Status |")
        add("| --- | --- | --- | --- | --- | --- |")
        for player in jason_x.get("starters") or []:
            upcoming = (player.get("upcoming") or [{}])[0]
            if upcoming.get("bye"):
                nxt = "BYE"
            elif upcoming.get("opponent"):
                nxt = ("vs " if upcoming.get("at_home") else "@ ") + upcoming["opponent"]
            else:
                nxt = "-"
            badge = player.get("injury") or {}
            status = badge.get("designation") or "-"
            if badge.get("expected_return_label"):
                status = f"{status} (back {badge['expected_return_label']})"
            add(f"| {player.get('lineup_slot')} | {player.get('name')} "
                f"| {player.get('position')} | W{player.get('bye_week')} | {nxt} "
                f"| {status} |")
        add("")
        for block in jason_x.get("bye_pressure") or []:
            names = ", ".join(
                f"{p['name']} ({p['lineup_slot']})" for p in block["starters_on_bye"]
            )
            add(f"**Week {block['scoring_period_id']} bye pressure:** {names}.")
            add("")

    # ---- upcoming byes ---------------------------------------------------
    byes_by_period = schedule.get("byes_by_scoring_period") or {}
    if byes_by_period:
        current = metadata.get("scoringPeriodId") or 0
        upcoming_byes = {
            week: teams for week, teams in byes_by_period.items()
            if int(week) >= current
        }
        if upcoming_byes:
            add("## Upcoming NFL byes")
            add("")
            add("| Week | Teams on bye |")
            add("| --- | --- |")
            for week in sorted(upcoming_byes, key=int):
                add(f"| {week} | {', '.join(upcoming_byes[week])} |")
            add("")

    # ---- injuries -------------------------------------------------------
    inj_counts = injuries.get("counts") or {}
    if inj_counts:
        add("## Injuries and IR")
        add("")
        add(f"{inj_counts.get('in_league_pool')} players in this league's pool carry "
            f"an NFL designation; {inj_counts.get('on_nfl_ir')} are on injured "
            f"reserve ({inj_counts.get('rostered_on_nfl_ir')} of them rostered). "
            f"{inj_counts.get('jason_x_affected')} affect Jason X.")
        add("")

        active_slot = injuries.get("ir_on_nfl_but_active_slot") or []
        if active_slot:
            add("**On NFL injured reserve while holding an active roster spot** "
                "- these could be moved to the fantasy IR slot:")
            add("")
            add("| Player | Fantasy team | Expected return |")
            add("| --- | --- | --- |")
            for row in active_slot:
                add(f"| {row.get('name')} | {row.get('fantasy_team')} "
                    f"| {row.get('expected_return')} |")
            add("")

        empty_slot = injuries.get("ir_slot_but_not_on_nfl_ir") or []
        if empty_slot:
            names = ", ".join(
                f"{r.get('name')} ({r.get('fantasy_team')})" for r in empty_slot
            )
            add(f"**In a fantasy IR slot without an NFL IR designation:** {names}.")
            add("")

        jason_hits = [
            v for v in (injuries.get("players") or {}).values() if v.get("is_jason_x")
        ]
        if jason_hits:
            add("Jason X players with a designation:")
            add("")
            add("| Player | Slot | Designation | Expected return | Injury |")
            add("| --- | --- | --- | --- | --- |")
            for v in sorted(jason_hits, key=lambda x: str(x.get("lineup_slot"))):
                mark = " **(NFL IR)**" if v.get("on_nfl_ir") else ""
                add(f"| {v.get('name')}{mark} | {v.get('lineup_slot')} "
                    f"| {v.get('designation')} "
                    f"| {v.get('expected_return_label') or '-'} "
                    f"| {v.get('injury_type') or '-'} |")
            add("")

    # ---- recent form ----------------------------------------------------
    # Locals are rf_counts / rf_block: build() already binds `counts` above for
    # the availability block, and reusing the name would shadow a live variable.
    rf_counts = recent_form.get("counts") or {}
    if rf_counts:
        add("## Recent form (last 3 completed games)")
        add("")
        rf_fetched = recent_form.get("fetched_at")
        if rf_fetched and rf_fetched != metadata.get("started_at"):
            add(f"**From an earlier run:** this section comes from a snapshot started "
                f"`{rf_fetched}`, while the rest of this digest is from "
                f"`{metadata.get('started_at')}`.")
            add("")
        add(f"Weeks considered: {(recent_form.get('weeks') or {}).get('considered')}. "
            f"Ranked {recent_form.get('ranked_total')} of "
            f"{recent_form.get('expected_total')} expected.")
        add("")
        add("| Pos | Limit | Qualifying | Ranked | At limit |")
        add("| --- | --- | --- | --- | --- |")
        for pos, rf_block in rf_counts.items():
            add(f"| {pos} | {rf_block.get('limit')} | {rf_block.get('qualifying')} "
                f"| {rf_block.get('ranked')} | {tick(rf_block.get('sufficient'))} |")
        add("")
        check = recent_form.get("scoring_crosscheck") or {}
        add(f"ESPN `appliedTotal` vs recomputed league scoring: max absolute delta "
            f"**{check.get('max_abs_delta')}** across {check.get('values_checked')} "
            f"values (tolerance {check.get('tolerance')}). `appliedTotal` is the "
            f"authoritative value; this is drift detection only.")
        add("")
        rf_own = recent_form.get("ownership") or {}
        add(f"{rf_own.get('is_jason_x_count')} ranked player(s) are on "
            f"`{rf_own.get('jason_x_team_name_observed')}` (team id "
            f"{rf_own.get('jason_x_team_id')}) and carry `is_jason_x`, which drives "
            f"the pale-yellow row highlight in the daily PDF. "
            f"{rf_own.get('unknown')} owner(s) unresolved.")
        add("")
        if rf_own.get("jason_x_name_id_agree") is False:
            add(f"**Team renamed:** team id {rf_own.get('jason_x_team_id')} selects "
                f"{rf_own.get('jason_x_by_id_count')} player(s) while the name "
                f"`{rf_own.get('jason_x_team_name')}` selects "
                f"{rf_own.get('jason_x_by_name_count')}. Highlighting follows the id.")
            add("")
        failed = (recent_form.get("weeks") or {}).get("failed") or []
        if failed:
            add(f"**Incomplete:** scoring periods {failed} could not be fetched, so "
                f"these rankings are built from the remaining weeks.")
            add("")
        top = sorted(
            (e for rows in (recent_form.get("rankings") or {}).values() for e in rows),
            key=lambda e: (-(e.get("avg_3_game_exact") or 0), e.get("player_id") or 0),
        )[:5]
        if top:
            add("Top 5 overall by three-game average:")
            add("")
            add("| # | Player | Pos | Team | Weeks | Avg | Owner |")
            add("| --- | --- | --- | --- | --- | --- | --- |")
            for i, entry in enumerate(top, 1):
                weeks = ", ".join(
                    str(g.get("scoring_period_id")) for g in entry.get("games") or []
                )
                owner = entry.get("owner_label") or "UNKNOWN"
                if entry.get("is_jason_x"):
                    owner = f"**{owner}**"
                add(f"| {i} | {entry.get('name')} | {entry.get('position')} "
                    f"| {entry.get('pro_team')} | {weeks} "
                    f"| {entry.get('avg_3_game_display')} | {owner} |")
            add("")
            add("Owner in **bold** means `is_jason_x` is true.")
            add("")

    # ---- transactions ---------------------------------------------------
    tx_counts = transactions.get("counts") or {}
    recent = transactions.get("executed_ownership_changes") or []
    if tx_counts:
        add("## Transactions")
        add("")
        add(
            f"{tx_counts.get('total')} total | "
            f"{tx_counts.get('executed_ownership_changes')} executed ownership changes | "
            f"{tx_counts.get('lineup_only_moves')} lineup-only moves | "
            f"{tx_counts.get('draft_picks')} draft picks | "
            f"{tx_counts.get('pending')} pending"
        )
        add("")
        if recent:
            add("Most recent ownership changes:")
            add("")
            add("| When | Team | Type | Moves |")
            add("| --- | --- | --- | --- |")
            rostered = ownership.get("rostered_players") or {}
            available = ownership.get("available_players") or {}

            def player_name(player_id: Any) -> str:
                key = str(player_id)
                record = rostered.get(key) or available.get(key)
                return (record or {}).get("name") or f"player {player_id}"

            for tx in recent[:12]:
                moves = ", ".join(
                    f"{item.get('item_type')} {player_name(item.get('player_id'))}"
                    for item in tx.get("items") or []
                    if item.get("item_type") in ("ADD", "DROP", "TRADE")
                ) or "-"
                when = (tx.get("processed_date") or tx.get("proposed_date") or "")[:16]
                add(f"| {when} | {tx.get('team')} | {tx.get('type')} | {moves} |")
            add("")

    # ---- consumption notes ----------------------------------------------
    roster_slots = ((league.get("roster") or {}).get("lineup_slot_counts")) or {}
    if roster_slots:
        add("## League format")
        add("")
        add(f"Scoring: `{league.get('scoring_type')}`. Starting lineup and bench: "
            + ", ".join(f"{slot} x{count}" for slot, count in roster_slots.items())
            + ".")
        add("")

    add("## Files in this snapshot")
    add("")
    add("| File | Contents |")
    add("| --- | --- |")
    add("| `metadata.json` | Fetch timestamps, week, per-step success flags. Read first. |")
    add("| `recent_form_rankings.json` | Top 24 QB / 60 RB / 60 WR / 24 TE / 24 K / "
        "20 D/ST by average points over each player's last three completed games, "
        "with owner and Jason X flag. |")
    add("| `injuries.json` | NFL designations and expected return dates, by player "
        "id, plus the two IR cross-checks. A compact `injury` block is also inline "
        "on every player elsewhere. |")
    add("| `gates.json` | Machine-readable pass/fail per snapshot check, and the "
        "DATA STATUS verdict. Read this instead of deriving it. |")
    add("| `jason_x.json` | Jason X precomputed: starters, bench, IR, open spots, "
        "FAAB, bye pressure, injuries, upcoming opponents. |")
    add("| `nfl_schedule.json` | Every NFL team's bye week and per-week opponent. "
        "Use instead of web research for byes and matchups. |")
    add("| `top_available.json` | Top 25 available per position with acquisition "
        "cost, bye, injury and opponents. A trimmed available_players.json. |")
    add("| `player_index.json` | **Name to owner lookup for every classified player. "
        "Use this to answer \"who owns X?\".** |")
    add("| `ownership.json` | Positive ownership: player id to owner, plus availability. |")
    add("| `rosters.json` | Every team's starters, bench, and IR. |")
    add("| `available_players.json` | ESPN free agent and waiver pool. |")
    add("| `transactions.json` | Ownership changes separated from lineup-only moves. |")
    add("| `matchups.json` | Current-period matchups and live scores. |")
    add("| `league.json` | Settings, scoring rules, roster slots, teams. |")
    add("| `raw/` | Unmodified ESPN responses. Artifact only, not committed. |")
    add("")

    return "\n".join(lines) + "\n"


def main() -> int:
    digest = build()

    # League team names contain emoji and smart quotes. Force UTF-8 on stdout so
    # this renders identically on a Linux runner and a Windows console (which
    # otherwise defaults to cp1252 and raises UnicodeEncodeError).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    # 1. job log
    sys.stdout.write(digest)

    # 2. run page summary
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(digest)
        except OSError as exc:
            print(f"warning: could not write step summary: {exc}", file=sys.stderr)

    # 3. committed digest
    if os.path.isdir(DATA_DIR):
        try:
            with open(os.path.join(DATA_DIR, "SNAPSHOT.md"), "w", encoding="utf-8") as handle:
                handle.write(digest)
        except OSError as exc:
            print(f"warning: could not write SNAPSHOT.md: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
