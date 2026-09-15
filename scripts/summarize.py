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
    ownership = load("ownership.json") or {}
    transactions = load("transactions.json") or {}
    matchups = load("matchups.json") or {}
    league = load("league.json") or {}

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
