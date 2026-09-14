#!/usr/bin/env python3
"""
Fetch a complete, freshly-retrieved snapshot of an ESPN fantasy football league.

Purpose
-------
This script exists so that downstream consumers (an LLM producing a fantasy
report, for example) never have to infer league state from a cached web page.
It talks to the ESPN API directly, sends the request headers ESPN actually
requires (notably ``X-Fantasy-Filter`` for the player pool), discovers the
current scoringPeriodId instead of hard-coding a week, and writes a set of
normalized JSON files plus a metadata file describing the fetch itself.

Critically, it resolves player *ownership* positively:
  - every player on all N rosters is recorded with the team that owns him
  - the free-agent / waiver pool is fetched explicitly from ESPN
so a consumer never has to guess "he wasn't on a roster, so he's probably free."

Outputs (in --out-dir, default ./data)
--------------------------------------
  league.json             league settings, teams, standings, status
  rosters.json            normalized rosters for every team (starters/bench/IR)
  transactions.json       transaction log, with ownership-changing moves split out
  available_players.json  ESPN FREEAGENT + WAIVERS pool
  ownership.json          the ownership gate: id -> owner, open slots, counts
  matchups.json           schedule / boxscore for the current scoring period
  metadata.json           fetch timestamps, scoringPeriodId, per-step success flags
  raw/*.json              unmodified ESPN responses, for auditing

Only the Python standard library is used, so this runs on a bare GitHub runner
with no pip install step.

Exit codes
----------
  0  all critical data retrieved
  1  a critical step failed (league, rosters, or roster completeness)
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable

# --------------------------------------------------------------------------
# ESPN constants
# --------------------------------------------------------------------------

READ_HOST = "https://lm-api-reads.fantasy.espn.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# lineupSlotId -> label. Slot 20 is bench, 21 is IR; everything else is a
# starting slot for scoring purposes.
LINEUP_SLOTS: dict[int, str] = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
    14: "DB", 15: "DP", 16: "D/ST", 17: "K", 18: "P", 19: "HC",
    20: "BENCH", 21: "IR", 22: "UNKNOWN22", 23: "FLEX", 24: "ER",
    25: "ROOKIE",
}
BENCH_SLOT = 20
IR_SLOT = 21

# defaultPositionId -> label
POSITIONS: dict[int, str] = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 6: "P", 7: "HC",
    9: "DT", 10: "DE", 11: "LB", 12: "CB", 13: "S", 14: "DB",
    15: "DP", 16: "D/ST",
}

# proTeamId -> NFL team abbreviation (0 = free agent / no pro team)
PRO_TEAMS: dict[int, str] = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG",
    20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF",
    26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL",
    34: "HOU",
}

# Transaction item types that actually move a player between owners.
OWNERSHIP_ITEM_TYPES = {"ADD", "DROP", "TRADE"}
# Transaction types that are lineup-only shuffles, not ownership changes.
LINEUP_ONLY_TYPES = {"ROSTER", "FUTURE_ROSTER"}

# ESPN reports a healthy player as ACTIVE on the player object and NORMAL on the
# roster entry. Anything else (QUESTIONABLE, DOUBTFUL, OUT, INJURY_RESERVE,
# DAY_TO_DAY, SUSPENSION) is a real designation worth surfacing. D/ST entries
# carry no injury status at all.
HEALTHY_INJURY_STATUSES = {"ACTIVE", "NORMAL", "", None}


def is_injury_designated(player: dict) -> bool:
    """True when ESPN has an actual injury designation on this player."""
    for key in ("injury_status", "roster_injury_status"):
        value = player.get(key)
        if value not in HEALTHY_INJURY_STATUSES:
            return True
    return bool(player.get("injured"))


class FetchError(RuntimeError):
    """Raised when an ESPN request cannot be completed after retries."""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class EspnClient:
    """Minimal ESPN fantasy API client with retries and cache-busting."""

    def __init__(
        self,
        season: int,
        league_id: str,
        swid: str | None = None,
        espn_s2: str | None = None,
        retries: int = 4,
        timeout: int = 90,
    ) -> None:
        self.season = season
        self.league_id = league_id
        self.swid = swid
        self.espn_s2 = espn_s2
        self.retries = retries
        self.timeout = timeout
        self.base = (
            f"{READ_HOST}/apis/v3/games/ffl/seasons/{season}"
            f"/segments/0/leagues/{league_id}"
        )
        self.call_log: list[dict[str, Any]] = []
        self._ctx = ssl.create_default_context()

    # -- internals ---------------------------------------------------------

    def _headers(self, fantasy_filter: dict | None) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-US,en;q=0.9",
            # Defeat any intermediary cache; ESPN honours these.
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
            "Referer": "https://fantasy.espn.com/",
        }
        if fantasy_filter is not None:
            # The header ESPN requires to query/filter the player pool.
            headers["X-Fantasy-Filter"] = json.dumps(
                fantasy_filter, separators=(",", ":")
            )
            headers["X-Fantasy-Source"] = "kona"
            headers["X-Fantasy-Platform"] = "kona-PROD"
        if self.swid and self.espn_s2:
            # Only needed for private leagues.
            headers["Cookie"] = f"SWID={self.swid}; espn_s2={self.espn_s2}"
        return headers

    def get(
        self,
        path: str = "",
        views: Iterable[str] | str | None = None,
        params: dict[str, Any] | None = None,
        fantasy_filter: dict | None = None,
        label: str | None = None,
    ) -> Any:
        """GET the league endpoint (optionally a sub-path) and return parsed JSON."""
        query: dict[str, Any] = dict(params or {})
        if views:
            query["view"] = [views] if isinstance(views, str) else list(views)
        # Unique cache-buster per call so nothing can serve us a stale body.
        query["cb"] = f"{int(time.time() * 1000)}{random.randint(1000, 9999)}"

        url = self.base + path + "?" + urllib.parse.urlencode(query, doseq=True)
        headers = self._headers(fantasy_filter)
        started = time.time()
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            try:
                request = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self._ctx
                ) as response:
                    body = response.read()
                    if response.headers.get("Content-Encoding") == "gzip":
                        body = gzip.GzipFile(fileobj=io.BytesIO(body)).read()
                    parsed = json.loads(body.decode("utf-8"))
                self.call_log.append(
                    {
                        "label": label or (path or "league"),
                        "url": _redact(url),
                        "status": 200,
                        "attempts": attempt,
                        "bytes": len(body),
                        "ms": round((time.time() - started) * 1000),
                    }
                )
                return parsed
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:  # pragma: no cover - best effort only
                    pass
                last_error = FetchError(f"HTTP {exc.code} for {label or path}: {detail}")
                # 4xx other than 429 will not improve by retrying.
                if 400 <= exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = FetchError(f"{type(exc).__name__} for {label or path}: {exc}")

            if attempt < self.retries:
                time.sleep(min(2 ** attempt, 10) + random.random())

        self.call_log.append(
            {
                "label": label or (path or "league"),
                "url": _redact(url),
                "status": "error",
                "attempts": self.retries,
                "error": str(last_error),
            }
        )
        raise last_error or FetchError(f"unknown failure for {label or path}")


def _redact(url: str) -> str:
    return url


# --------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------


def team_display_name(team: dict) -> str:
    """ESPN returns either `name`, or `location` + `nickname` on older payloads."""
    name = (team.get("name") or "").strip()
    if name:
        return name
    parts = [team.get("location") or "", team.get("nickname") or ""]
    joined = " ".join(p.strip() for p in parts if p and p.strip())
    return joined or f"Team {team.get('id')}"


def owner_names(team: dict, members_by_id: dict[str, dict]) -> list[str]:
    names: list[str] = []
    for owner_id in team.get("owners") or []:
        member = members_by_id.get(owner_id) or {}
        first = (member.get("firstName") or "").strip()
        last = (member.get("lastName") or "").strip()
        display = (member.get("displayName") or "").strip()
        full = " ".join(p for p in (first, last) if p) or display
        names.append(full or owner_id)
    return names


def normalize_player(entry_player: dict) -> dict:
    """Flatten the fields a report actually needs out of an ESPN player object."""
    ownership = entry_player.get("ownership") or {}
    return {
        "player_id": entry_player.get("id"),
        "name": entry_player.get("fullName"),
        "first_name": entry_player.get("firstName"),
        "last_name": entry_player.get("lastName"),
        "position": POSITIONS.get(entry_player.get("defaultPositionId"), "UNKNOWN"),
        "position_id": entry_player.get("defaultPositionId"),
        "pro_team": PRO_TEAMS.get(entry_player.get("proTeamId"), "UNKNOWN"),
        "pro_team_id": entry_player.get("proTeamId"),
        "jersey": entry_player.get("jersey"),
        "active": entry_player.get("active"),
        "injured": entry_player.get("injured", False),
        "injury_status": entry_player.get("injuryStatus"),
        "eligible_slots": [
            LINEUP_SLOTS.get(s, str(s)) for s in entry_player.get("eligibleSlots") or []
        ],
        "eligible_slot_ids": entry_player.get("eligibleSlots") or [],
        "droppable": entry_player.get("droppable"),
        "percent_owned": _round(ownership.get("percentOwned")),
        "percent_started": _round(ownership.get("percentStarted")),
        "percent_change": _round(ownership.get("percentChange")),
        "auction_value_average": _round(ownership.get("auctionValueAverage")),
        "last_news_date": _ms_to_iso(entry_player.get("lastNewsDate")),
    }


def _round(value: Any, digits: int = 2) -> Any:
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return value


def _ms_to_iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def build_rosters(
    teams: list[dict],
    members_by_id: dict[str, dict],
    lineup_slot_counts: dict[str, int],
) -> list[dict]:
    """Normalize every team's roster into starters / bench / IR buckets."""
    starting_slot_counts = {
        int(slot): count
        for slot, count in (lineup_slot_counts or {}).items()
        if count and int(slot) not in (BENCH_SLOT, IR_SLOT)
    }
    bench_capacity = int((lineup_slot_counts or {}).get(str(BENCH_SLOT), 0) or 0)
    ir_capacity = int((lineup_slot_counts or {}).get(str(IR_SLOT), 0) or 0)

    rosters: list[dict] = []
    for team in sorted(teams, key=lambda t: t.get("id", 0)):
        entries = ((team.get("roster") or {}).get("entries")) or []
        starters: list[dict] = []
        bench: list[dict] = []
        injured_reserve: list[dict] = []
        filled_starting_slots: dict[int, int] = {}

        for entry in entries:
            pool_entry = entry.get("playerPoolEntry") or {}
            player_obj = pool_entry.get("player") or {}
            slot_id = entry.get("lineupSlotId")
            record = normalize_player(player_obj)
            record.update(
                {
                    "lineup_slot_id": slot_id,
                    "lineup_slot": LINEUP_SLOTS.get(slot_id, str(slot_id)),
                    "acquisition_type": entry.get("acquisitionType"),
                    "acquisition_date": _ms_to_iso(entry.get("acquisitionDate")),
                    "roster_injury_status": entry.get("injuryStatus"),
                    "applied_total": _round(pool_entry.get("appliedStatTotal")),
                    "keeper_value": pool_entry.get("keeperValue"),
                    "lineup_locked": pool_entry.get("lineupLocked"),
                    "pending_transaction_ids": entry.get("pendingTransactionIds"),
                }
            )
            if slot_id == BENCH_SLOT:
                bench.append(record)
            elif slot_id == IR_SLOT:
                injured_reserve.append(record)
            else:
                starters.append(record)
                filled_starting_slots[slot_id] = filled_starting_slots.get(slot_id, 0) + 1

        open_starting_slots = {
            LINEUP_SLOTS.get(slot, str(slot)): required - filled_starting_slots.get(slot, 0)
            for slot, required in starting_slot_counts.items()
            if required - filled_starting_slots.get(slot, 0) > 0
        }

        record_block = team.get("record") or {}
        overall = record_block.get("overall") or {}
        transaction_counter = team.get("transactionCounter") or {}

        rosters.append(
            {
                "team_id": team.get("id"),
                "team_name": team_display_name(team),
                "abbrev": team.get("abbrev"),
                "owners": owner_names(team, members_by_id),
                "owner_ids": team.get("owners") or [],
                "division_id": team.get("divisionId"),
                "record": {
                    "wins": overall.get("wins"),
                    "losses": overall.get("losses"),
                    "ties": overall.get("ties"),
                    "points_for": _round(overall.get("pointsFor")),
                    "points_against": _round(overall.get("pointsAgainst")),
                    "percentage": _round(overall.get("percentage"), 3),
                },
                "playoff_seed": team.get("playoffSeed"),
                "current_projected_rank": team.get("currentProjectedRank"),
                "waiver_rank": team.get("waiverRank"),
                "transaction_locked": team.get("isTransactionLocked"),
                "acquisitions_used": transaction_counter.get("acquisitions"),
                "acquisition_budget_spent": transaction_counter.get("acquisitionBudgetSpent"),
                "trades_used": transaction_counter.get("trades"),
                "drops_used": transaction_counter.get("drops"),
                "counts": {
                    "total": len(entries),
                    "starters": len(starters),
                    "bench": len(bench),
                    "injured_reserve": len(injured_reserve),
                },
                "capacity": {
                    "bench_capacity": bench_capacity,
                    "bench_open": max(bench_capacity - len(bench), 0),
                    "ir_capacity": ir_capacity,
                    "ir_open": max(ir_capacity - len(injured_reserve), 0),
                },
                "open_starting_slots": open_starting_slots,
                "starters": starters,
                "bench": bench,
                "injured_reserve": injured_reserve,
            }
        )
    return rosters


def normalize_transactions(raw_transactions: list[dict], teams_by_id: dict[int, str]) -> dict:
    """Split the transaction log into ownership changes vs lineup-only moves."""
    ownership_changes: list[dict] = []
    lineup_moves: list[dict] = []
    draft_picks: list[dict] = []

    for transaction in raw_transactions:
        tx_type = transaction.get("type")
        items: list[dict] = []
        for item in transaction.get("items") or []:
            items.append(
                {
                    "item_type": item.get("type"),
                    "player_id": item.get("playerId"),
                    "from_team_id": item.get("fromTeamId"),
                    "from_team": teams_by_id.get(item.get("fromTeamId")),
                    "to_team_id": item.get("toTeamId"),
                    "to_team": teams_by_id.get(item.get("toTeamId")),
                    "from_slot": LINEUP_SLOTS.get(item.get("fromLineupSlotId")),
                    "to_slot": LINEUP_SLOTS.get(item.get("toLineupSlotId")),
                    "overall_pick_number": item.get("overallPickNumber"),
                    "is_keeper": item.get("isKeeper"),
                }
            )

        normalized = {
            "id": transaction.get("id"),
            "type": tx_type,
            "status": transaction.get("status"),
            "execution_type": transaction.get("executionType"),
            "is_pending": transaction.get("isPending"),
            "scoring_period_id": transaction.get("scoringPeriodId"),
            "team_id": transaction.get("teamId"),
            "team": teams_by_id.get(transaction.get("teamId")),
            "bid_amount": transaction.get("bidAmount"),
            "proposed_date": _ms_to_iso(transaction.get("proposedDate")),
            "processed_date": _ms_to_iso(transaction.get("processDate")),
            "is_league_manager": transaction.get("isLeagueManager"),
            "items": items,
        }

        if tx_type == "DRAFT":
            draft_picks.append(normalized)
        elif any(i["item_type"] in OWNERSHIP_ITEM_TYPES for i in items):
            ownership_changes.append(normalized)
        elif tx_type in LINEUP_ONLY_TYPES:
            lineup_moves.append(normalized)
        else:
            ownership_changes.append(normalized)

    def sort_key(tx: dict) -> str:
        return tx.get("processed_date") or tx.get("proposed_date") or ""

    ownership_changes.sort(key=sort_key, reverse=True)
    lineup_moves.sort(key=sort_key, reverse=True)

    executed_ownership = [
        t for t in ownership_changes if t.get("status") == "EXECUTED" and not t.get("is_pending")
    ]
    pending = [
        t for t in (ownership_changes + lineup_moves) if t.get("is_pending")
    ]

    return {
        "counts": {
            "total": len(raw_transactions),
            "ownership_changes": len(ownership_changes),
            "executed_ownership_changes": len(executed_ownership),
            "lineup_only_moves": len(lineup_moves),
            "draft_picks": len(draft_picks),
            "pending": len(pending),
        },
        "executed_ownership_changes": executed_ownership,
        "pending_transactions": pending,
        "lineup_only_moves": lineup_moves,
        "all_ownership_changes": ownership_changes,
        "draft_picks": draft_picks,
    }


def build_ownership(
    rosters: list[dict],
    available_players: list[dict],
) -> dict:
    """The ownership gate: a positive answer for every player we know about."""
    rostered: dict[str, dict] = {}
    for team in rosters:
        for bucket in ("starters", "bench", "injured_reserve"):
            for player in team[bucket]:
                rostered[str(player["player_id"])] = {
                    "player_id": player["player_id"],
                    "name": player["name"],
                    "position": player["position"],
                    "pro_team": player["pro_team"],
                    "owner_team_id": team["team_id"],
                    "owner_team_name": team["team_name"],
                    "lineup_slot": player["lineup_slot"],
                    "status": "ROSTERED",
                    "injury_status": player.get("injury_status"),
                }

    available: dict[str, dict] = {}
    for player in available_players:
        available[str(player["player_id"])] = {
            "player_id": player["player_id"],
            "name": player["name"],
            "position": player["position"],
            "pro_team": player["pro_team"],
            "status": player.get("availability_status"),
            "waiver_process_date": player.get("waiver_process_date"),
            "percent_owned": player.get("percent_owned"),
            "injury_status": player.get("injury_status"),
        }

    conflicts = sorted(set(rostered) & set(available))

    by_position: dict[str, int] = {}
    for player in available.values():
        by_position[player["position"]] = by_position.get(player["position"], 0) + 1

    return {
        "explanation": (
            "Ownership is resolved positively. rostered_players covers every player "
            "on all league rosters (starters, bench, IR). available_players is ESPN's "
            "own FREEAGENT/WAIVERS pool, not an inference from roster absence. A player "
            "absent from both lists is unknown to this snapshot and must not be treated "
            "as available."
        ),
        "counts": {
            "rostered": len(rostered),
            "available": len(available),
            "available_by_position": dict(sorted(by_position.items())),
            "conflicts": len(conflicts),
        },
        "conflicting_player_ids": conflicts,
        "rostered_player_ids": sorted(int(pid) for pid in rostered),
        "available_player_ids": sorted(int(pid) for pid in available),
        "rostered_players": rostered,
        "available_players": available,
        "team_needs": [
            {
                "team_id": team["team_id"],
                "team_name": team["team_name"],
                "open_starting_slots": team["open_starting_slots"],
                "bench_open": team["capacity"]["bench_open"],
                "ir_open": team["capacity"]["ir_open"],
                "roster_size": team["counts"]["total"],
                "injury_designations": [
                    {
                        "player_id": p["player_id"],
                        "name": p["name"],
                        "position": p["position"],
                        "pro_team": p["pro_team"],
                        "injury_status": p.get("injury_status"),
                        "lineup_slot": p["lineup_slot"],
                        "is_starting": p["lineup_slot"]
                        not in ("BENCH", "IR"),
                    }
                    for bucket in ("starters", "bench", "injured_reserve")
                    for p in team[bucket]
                    if is_injury_designated(p)
                ],
            }
            for team in rosters
        ],
    }


# --------------------------------------------------------------------------
# Fetch steps
# --------------------------------------------------------------------------


def fetch_player_pool(
    client: EspnClient,
    scoring_period_id: int,
    statuses: list[str],
    page_size: int = 1000,
    max_pages: int = 12,
) -> list[dict]:
    """Page through ESPN's player pool using the X-Fantasy-Filter header."""
    collected: dict[int, dict] = {}
    offset = 0

    for _ in range(max_pages):
        fantasy_filter = {
            "players": {
                "filterStatus": {"value": statuses},
                "limit": page_size,
                "offset": offset,
                "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
                "filterRanksForScoringPeriodIds": {"value": [scoring_period_id]},
            }
        }
        payload = client.get(
            views="kona_player_info",
            params={"scoringPeriodId": scoring_period_id},
            fantasy_filter=fantasy_filter,
            label=f"player_pool@{offset}",
        )
        page = payload.get("players") or []
        for wrapper in page:
            player_obj = wrapper.get("player") or {}
            record = normalize_player(player_obj)
            record.update(
                {
                    "availability_status": wrapper.get("status"),
                    "on_team_id": wrapper.get("onTeamId"),
                    "waiver_process_date": _ms_to_iso(wrapper.get("waiverProcessDate")),
                    "roster_locked": wrapper.get("rosterLocked"),
                    "trade_locked": wrapper.get("tradeLocked"),
                    "draft_auction_value": wrapper.get("draftAuctionValue"),
                }
            )
            pid = record.get("player_id")
            if isinstance(pid, int):
                collected[pid] = record

        if len(page) < page_size:
            break
        offset += page_size

    ordered = sorted(
        collected.values(),
        key=lambda p: (-(p.get("percent_owned") or 0), p.get("name") or ""),
    )
    return ordered


def discover_scoring_period(
    status: dict, override: int | None, top_level_scoring_period: Any = None
) -> tuple[int, dict]:
    """Determine the scoring period to report on, preferring ESPN's own status.

    ESPN reports the live scoring period at the payload top level
    (``scoringPeriodId``); ``status`` carries ``latestScoringPeriod`` and
    ``currentMatchupPeriod``. We prefer the top-level value, then fall back.
    """
    current = status.get("currentMatchupPeriod")
    latest = status.get("latestScoringPeriod")
    espn_scoring_period = (
        top_level_scoring_period
        if isinstance(top_level_scoring_period, int)
        else status.get("scoringPeriodId")
    )
    detail = {
        "espn_scoring_period_id": espn_scoring_period,
        "latest_scoring_period": latest,
        "current_matchup_period": current,
        "first_scoring_period": status.get("firstScoringPeriod"),
        "final_scoring_period": status.get("finalScoringPeriod"),
        "is_active": status.get("isActive"),
        "season_complete": status.get("latestScoringPeriod") == status.get("finalScoringPeriod"),
        "override_applied": override is not None,
    }
    if override is not None:
        return override, detail
    for candidate in (espn_scoring_period, latest, current):
        if isinstance(candidate, int) and candidate > 0:
            return candidate, detail
    return 1, detail


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch a fresh ESPN fantasy league snapshot.")
    parser.add_argument(
        "--league-id",
        default=os.environ.get("ESPN_LEAGUE_ID", "1787259003"),
        help="ESPN league id (default: %(default)s, or $ESPN_LEAGUE_ID)",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=int(os.environ.get("ESPN_SEASON") or 0) or None,
        help="Season year. Defaults to the current NFL season.",
    )
    parser.add_argument(
        "--scoring-period",
        type=int,
        default=int(os.environ.get("ESPN_SCORING_PERIOD") or 0) or None,
        help="Override the discovered scoringPeriodId (normally leave unset).",
    )
    parser.add_argument(
        "--out-dir",
        default=os.environ.get("ESPN_OUT_DIR", "data"),
        help="Directory to write JSON output into (default: %(default)s)",
    )
    parser.add_argument(
        "--no-raw",
        action="store_true",
        help="Skip writing the unmodified ESPN responses.",
    )
    args = parser.parse_args(argv)

    # Team names contain emoji; keep console output safe on non-UTF-8 terminals.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    season = args.season or default_season()
    out_dir = os.path.abspath(args.out_dir)
    raw_dir = os.path.join(out_dir, "raw")
    os.makedirs(out_dir, exist_ok=True)
    if not args.no_raw:
        os.makedirs(raw_dir, exist_ok=True)

    started_at = datetime.now(timezone.utc)
    client = EspnClient(
        season=season,
        league_id=args.league_id,
        swid=os.environ.get("ESPN_SWID") or None,
        espn_s2=os.environ.get("ESPN_S2") or None,
    )

    steps: dict[str, bool] = {}
    errors: list[str] = []

    def write(name: str, payload: Any) -> None:
        path = os.path.join(out_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False)
            handle.write("\n")
        print(f"  wrote {os.path.relpath(path, out_dir)} ({os.path.getsize(path):,} bytes)")

    def write_raw(name: str, payload: Any) -> None:
        if args.no_raw:
            return
        write(os.path.join("raw", name), payload)

    print(f"ESPN snapshot: league {args.league_id}, season {season}")
    print(f"Started {started_at.isoformat()}")

    # ---- 1. League core (settings, teams, members, status) ----------------
    print("\n[1/6] league settings, teams, status")
    try:
        league_raw = client.get(
            views=["mSettings", "mTeam", "mRoster", "mStandings"], label="league_core"
        )
        steps["league_fetch_success"] = True
    except FetchError as exc:
        print(f"  CRITICAL: {exc}", file=sys.stderr)
        errors.append(str(exc))
        steps["league_fetch_success"] = False
        write(
            "metadata.json",
            build_metadata(
                started_at, season, args.league_id, None, {}, steps, errors, client, 0, 0, 0, 0
            ),
        )
        return 1

    write_raw("league_core.json", league_raw)

    settings = league_raw.get("settings") or {}
    status = league_raw.get("status") or {}
    teams = league_raw.get("teams") or []
    members_by_id = {m.get("id"): m for m in (league_raw.get("members") or [])}
    expected_team_count = settings.get("size") or len(teams)

    scoring_period_id, period_detail = discover_scoring_period(
        status, args.scoring_period, league_raw.get("scoringPeriodId")
    )
    print(
        f"  league '{settings.get('name')}' | teams {len(teams)}/{expected_team_count} "
        f"| scoringPeriodId {scoring_period_id}"
    )

    roster_settings = settings.get("rosterSettings") or {}
    lineup_slot_counts = roster_settings.get("lineupSlotCounts") or {}

    write(
        "league.json",
        {
            "league_id": args.league_id,
            "season": season,
            "name": settings.get("name"),
            "size": expected_team_count,
            "scoring_period_id": scoring_period_id,
            "scoring_period_detail": period_detail,
            "status": status,
            "scoring_type": (settings.get("scoringSettings") or {}).get("scoringType"),
            "roster": {
                "lineup_slot_counts": {
                    LINEUP_SLOTS.get(int(slot), slot): count
                    for slot, count in lineup_slot_counts.items()
                    if count
                },
                "lineup_slot_counts_raw": lineup_slot_counts,
                "position_limits": roster_settings.get("positionLimits"),
                "roster_locktype": roster_settings.get("rosterLocktimeType"),
            },
            "acquisition_settings": settings.get("acquisitionSettings"),
            "schedule_settings": settings.get("scheduleSettings"),
            "trade_settings": settings.get("tradeSettings"),
            "draft_settings": {
                k: v
                for k, v in (settings.get("draftSettings") or {}).items()
                if k != "pickOrder"
            },
            "scoring_settings": settings.get("scoringSettings"),
            "teams": [
                {
                    "team_id": t.get("id"),
                    "team_name": team_display_name(t),
                    "abbrev": t.get("abbrev"),
                    "owners": owner_names(t, members_by_id),
                    "record": (t.get("record") or {}).get("overall"),
                    "playoff_seed": t.get("playoffSeed"),
                    "waiver_rank": t.get("waiverRank"),
                }
                for t in sorted(teams, key=lambda x: x.get("id", 0))
            ],
        },
    )

    # ---- 2. Rosters -------------------------------------------------------
    print("\n[2/6] rosters for every team")
    rosters = build_rosters(teams, members_by_id, lineup_slot_counts)
    teams_with_players = sum(1 for r in rosters if r["counts"]["total"] > 0)
    rosters_complete = (
        len(rosters) == expected_team_count and teams_with_players == expected_team_count
    )
    steps["roster_fetch_success"] = rosters_complete
    if not rosters_complete:
        message = (
            f"roster completeness failed: {len(rosters)} teams returned, "
            f"{teams_with_players} with players, expected {expected_team_count}"
        )
        print(f"  CRITICAL: {message}", file=sys.stderr)
        errors.append(message)
    else:
        total_rostered = sum(r["counts"]["total"] for r in rosters)
        print(f"  {len(rosters)} teams, {total_rostered} rostered players")

    write(
        "rosters.json",
        {
            "league_id": args.league_id,
            "season": season,
            "scoring_period_id": scoring_period_id,
            "fetched_at": started_at.isoformat(),
            "team_count": len(rosters),
            "expected_team_count": expected_team_count,
            "complete": rosters_complete,
            "teams": rosters,
        },
    )

    # ---- 3. Transactions --------------------------------------------------
    print("\n[3/6] transactions")
    teams_by_id = {r["team_id"]: r["team_name"] for r in rosters}
    transactions_payload: dict[str, Any]
    try:
        # NOTE: mTransactions2 returns 400 if an X-Fantasy-Filter is supplied,
        # so this request intentionally sends none.
        tx_raw = client.get(views="mTransactions2", label="transactions")
        write_raw("transactions_raw.json", tx_raw)
        raw_transactions = tx_raw.get("transactions") or []
        transactions_payload = normalize_transactions(raw_transactions, teams_by_id)
        steps["transactions_fetch_success"] = True
        counts = transactions_payload["counts"]
        print(
            f"  {counts['total']} total | {counts['executed_ownership_changes']} executed "
            f"ownership changes | {counts['draft_picks']} draft picks"
        )
    except FetchError as exc:
        print(f"  WARNING: {exc}", file=sys.stderr)
        errors.append(str(exc))
        steps["transactions_fetch_success"] = False
        transactions_payload = {"counts": {}, "error": str(exc)}

    transactions_payload.update(
        {
            "league_id": args.league_id,
            "season": season,
            "scoring_period_id": scoring_period_id,
            "fetched_at": started_at.isoformat(),
        }
    )
    write("transactions.json", transactions_payload)

    # ---- 4. Free agent / waiver pool -------------------------------------
    print("\n[4/6] free agent + waiver pool (X-Fantasy-Filter)")
    available: list[dict] = []
    try:
        available = fetch_player_pool(client, scoring_period_id, ["FREEAGENT", "WAIVERS"])
        steps["player_pool_fetch_success"] = len(available) > 0
        free_agents = sum(1 for p in available if p.get("availability_status") == "FREEAGENT")
        waivers = sum(1 for p in available if p.get("availability_status") == "WAIVERS")
        print(f"  {len(available)} available ({free_agents} free agents, {waivers} on waivers)")
        if not available:
            errors.append("player pool returned zero players")
    except FetchError as exc:
        print(f"  WARNING: {exc}", file=sys.stderr)
        errors.append(str(exc))
        steps["player_pool_fetch_success"] = False

    write(
        "available_players.json",
        {
            "league_id": args.league_id,
            "season": season,
            "scoring_period_id": scoring_period_id,
            "fetched_at": started_at.isoformat(),
            "source": "ESPN kona_player_info with X-Fantasy-Filter filterStatus FREEAGENT+WAIVERS",
            "count": len(available),
            "counts_by_status": {
                "FREEAGENT": sum(1 for p in available if p.get("availability_status") == "FREEAGENT"),
                "WAIVERS": sum(1 for p in available if p.get("availability_status") == "WAIVERS"),
            },
            "players": available,
        },
    )

    # ---- 5. Matchups for the current period ------------------------------
    print("\n[5/6] matchups / boxscore for current period")
    try:
        matchup_raw = client.get(
            views=["mMatchupScore", "mBoxscore"],
            params={"scoringPeriodId": scoring_period_id},
            label="matchups",
        )
        write_raw("matchups_raw.json", matchup_raw)
        schedule = [
            game
            for game in (matchup_raw.get("schedule") or [])
            if game.get("matchupPeriodId") == period_detail.get("current_matchup_period")
        ] or (matchup_raw.get("schedule") or [])
        current = [
            {
                "matchup_id": g.get("id"),
                "matchup_period_id": g.get("matchupPeriodId"),
                "winner": g.get("winner"),
                "playoff_tier_type": g.get("playoffTierType"),
                "home": _matchup_side(g.get("home"), teams_by_id, scoring_period_id),
                "away": _matchup_side(g.get("away"), teams_by_id, scoring_period_id),
            }
            for g in schedule
        ]
        write(
            "matchups.json",
            {
                "league_id": args.league_id,
                "season": season,
                "scoring_period_id": scoring_period_id,
                "matchup_period_id": period_detail.get("current_matchup_period"),
                "fetched_at": started_at.isoformat(),
                "count": len(current),
                "matchups": current,
            },
        )
        steps["matchup_fetch_success"] = True
        print(f"  {len(current)} matchups for this period")
    except FetchError as exc:
        print(f"  WARNING: {exc}", file=sys.stderr)
        errors.append(str(exc))
        steps["matchup_fetch_success"] = False

    # ---- 6. Ownership reconciliation -------------------------------------
    print("\n[6/6] ownership reconciliation")
    ownership = build_ownership(rosters, available)
    ownership.update(
        {
            "league_id": args.league_id,
            "season": season,
            "scoring_period_id": scoring_period_id,
            "fetched_at": started_at.isoformat(),
        }
    )
    write("ownership.json", ownership)
    steps["ownership_reconciled"] = (
        steps.get("roster_fetch_success", False)
        and steps.get("player_pool_fetch_success", False)
        and ownership["counts"]["conflicts"] == 0
    )
    print(
        f"  {ownership['counts']['rostered']} rostered | "
        f"{ownership['counts']['available']} available | "
        f"{ownership['counts']['conflicts']} conflicts"
    )
    if ownership["counts"]["conflicts"]:
        errors.append(
            f"{ownership['counts']['conflicts']} players appear both rostered and available"
        )

    # ---- metadata --------------------------------------------------------
    metadata = build_metadata(
        started_at,
        season,
        args.league_id,
        scoring_period_id,
        period_detail,
        steps,
        errors,
        client,
        len(rosters),
        expected_team_count,
        ownership["counts"]["rostered"],
        ownership["counts"]["available"],
        league_name=settings.get("name"),
    )
    write("metadata.json", metadata)

    critical_ok = steps.get("league_fetch_success") and steps.get("roster_fetch_success")
    print(
        f"\n{'OK' if critical_ok else 'FAILED'}: "
        f"{metadata['duration_seconds']}s, {len(client.call_log)} ESPN calls, "
        f"{len(errors)} problem(s)"
    )
    for message in errors:
        print(f"  - {message}")
    return 0 if critical_ok else 1


def _matchup_side(
    side: dict | None, teams_by_id: dict[int, str], scoring_period_id: int
) -> dict | None:
    """Normalize one side of a matchup.

    ESPN leaves ``totalPoints`` at 0.0 until a matchup is finalized and reports
    the in-progress score in ``totalPointsLive``, so ``points`` below resolves to
    whichever value actually reflects the current score.
    """
    if not side:
        return None
    final_points = side.get("totalPoints")
    live_points = side.get("totalPointsLive")
    by_period = side.get("pointsByScoringPeriod") or {}
    period_points = by_period.get(str(scoring_period_id))

    candidates = [c for c in (final_points, live_points, period_points) if c]
    points = _round(candidates[0]) if candidates else _round(final_points)

    return {
        "team_id": side.get("teamId"),
        "team_name": teams_by_id.get(side.get("teamId")),
        "points": points,
        "is_final": bool(final_points),
        "total_points_final": _round(final_points),
        "total_points_live": _round(live_points),
        "points_this_period": _round(period_points),
        "projected_points": _round(side.get("totalProjectedPoints")),
        "projected_points_live": _round(side.get("totalProjectedPointsLive")),
        "win_probability": _round(side.get("winProbability"), 4),
        "adjustment": _round(side.get("adjustment")),
    }


def build_metadata(
    started_at: datetime,
    season: int,
    league_id: str,
    scoring_period_id: int | None,
    period_detail: dict,
    steps: dict[str, bool],
    errors: list[str],
    client: EspnClient,
    team_count: int,
    expected_team_count: int,
    rostered_count: int = 0,
    available_count: int = 0,
    league_name: str | None = None,
) -> dict:
    finished_at = datetime.now(timezone.utc)
    all_ok = all(steps.get(k, False) for k in ("league_fetch_success", "roster_fetch_success"))
    return {
        "fetched_at": finished_at.isoformat(),
        "fetched_at_epoch": int(finished_at.timestamp()),
        "started_at": started_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
        "league_id": league_id,
        "league_name": league_name,
        "season": season,
        "scoringPeriodId": scoring_period_id,
        "scoring_period_detail": period_detail,
        "team_count": team_count,
        "expected_team_count": expected_team_count,
        "team_count_complete": team_count == expected_team_count,
        "rostered_player_count": rostered_count,
        "available_player_count": available_count,
        "roster_fetch_success": steps.get("roster_fetch_success", False),
        "transactions_fetch_success": steps.get("transactions_fetch_success", False),
        "player_pool_fetch_success": steps.get("player_pool_fetch_success", False),
        "matchup_fetch_success": steps.get("matchup_fetch_success", False),
        "ownership_reconciled": steps.get("ownership_reconciled", False),
        "league_fetch_success": steps.get("league_fetch_success", False),
        "critical_success": bool(all_ok),
        "errors": errors,
        "source": "ESPN fantasy v3 API, fetched server-side (no web cache layer)",
        "api_host": READ_HOST,
        "espn_calls": client.call_log,
        "github": {
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "workflow": os.environ.get("GITHUB_WORKFLOW"),
            "sha": os.environ.get("GITHUB_SHA"),
            "ref": os.environ.get("GITHUB_REF"),
            "actor": os.environ.get("GITHUB_ACTOR"),
            "event_name": os.environ.get("GITHUB_EVENT_NAME"),
            "repository": os.environ.get("GITHUB_REPOSITORY"),
        },
        "generator": "scripts/fetch_espn.py",
    }


def default_season() -> int:
    """NFL seasons span calendar years; before March, the season is last year."""
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 3 else now.year - 1


if __name__ == "__main__":
    sys.exit(main())
