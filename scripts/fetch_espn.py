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
import math
import os
import random
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

# --------------------------------------------------------------------------
# ESPN constants
# --------------------------------------------------------------------------

READ_HOST = "https://lm-api-reads.fantasy.espn.com"

# ESPN's public NFL injury feed. A different host from the fantasy API, and the
# only place that carries an expected return date. No authentication needed.
#
# The fantasy API gives only injuryStatus (ACTIVE / QUESTIONABLE / OUT /
# INJURY_RESERVE / DOUBTFUL / DAY_TO_DAY / SUSPENSION) and an `injured` boolean.
# This feed adds returnDate, the injury type and location, an IR versus IR-R
# designation (IR-R means designated to return), and a one-line beat note.
#
# The feed omits athlete.id, but every record's links carry it as /id/<digits>/,
# so the join stays on ESPN player id rather than on names. Verified against
# this league's pool: ids recovered from 800 of 800 records, 419 of them present
# in player_index.json, and the id join agreed with a name join on all 419 with
# zero disagreements.
INJURY_FEED_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
)
ATHLETE_ID_IN_LINK = re.compile(r"/id/(\d+)")

# ESPN reports a season-ending injury by setting returnDate to the Super Bowl.
# Anything at or beyond this is reported as out for the season rather than as a
# date that reads like a real return.
SEASON_ENDING_RETURN_MONTH = (2, 1)   # (month, day) from which a date is "season"

# NFL statuses that mean the player is on injured reserve.
NFL_IR_STATUSES = {"Injured Reserve", "Injured Reserve - Designated to Return"}

# The report is written for US Central time, so every timestamp is published in
# both UTC and CT rather than asking the consumer to convert.
#
# zoneinfo is preferred, but it raises ZoneInfoNotFoundError on a stock Windows
# install (no system tzdata, and this project takes no third-party packages), so
# a deterministic US Central rule is implemented as a fallback. Without it the
# GitHub runner and a local run would print different times for the same
# snapshot. metadata.timezone_source records which path produced the values.
CENTRAL_TZ_NAME = "America/Chicago"
CT_STANDARD_OFFSET = -6        # CST
CT_DAYLIGHT_OFFSET = -5        # CDT

# How many available players per position to publish in the trimmed pool. The
# full pool is ~850 players and ~800 KB; this is the slice a report can actually
# use, sorted by rostered percentage.
TOP_AVAILABLE_PER_POSITION = 25

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
# Per-position ranking limits for the recent-form rankings. This is the single
# source of truth for those numbers: the JSON artifact, SNAPSHOT.md, the daily
# report prompt, the README and the test suite all trace back here. Total 212.
RECENT_FORM_LIMITS: dict[str, int] = {
    "QB": 24, "RB": 60, "WR": 60, "TE": 24, "K": 24, "D/ST": 20,
}

# Number of completed games a player must have to be ranked. Exactly this many,
# never fewer, never padded.
RECENT_FORM_GAMES = 3

# Players ESPN expects back only next season are excluded from the recent-form
# rankings entirely. Their three-game average is real but useless: a strong
# number next to a player who cannot play again this year is a trap, and two
# such players were sitting in the WR table as free agents. Excluding them
# before the slice means the limits backfill, so the total stays 212.
OUT_FOR_SEASON_LABEL = "out for the season"

# The order Jason X's starting slots are presented in, everywhere.
STARTING_SLOT_ORDER = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "D/ST"]

# ESPN's games-played marker inside a weekly actual's inner stats dict. Keys in
# that dict are JSON strings, hence the str.
#
# This is the participation test, and it is load-bearing. ESPN emits a weekly
# actual row with appliedTotal 0.0 for players who did NOT play, which would
# otherwise be counted as a completed game and drag the average down: a player
# who went 18.4 / did-not-play / 16.5 would average 11.63 instead of 17.45.
# Measured across weeks 1-9 of this league, the marker is strictly binary -
# present as 1.0 on 3,721 rows and absent on 1,541 - and the 1,541 absences are
# exactly the rows carrying an empty stats dict. Rows that DO carry the marker
# but still score 0.0 are real appearances (1,073 of them: a receiver targeted
# five times with no catch, a quarterback with one incomplete pass) and must be
# kept.
GAMES_PLAYED_STAT_ID = "210"

# The fantasy team whose players drive the PDF's pale-yellow row highlight. The
# id is the authority: two teams in this league have already been renamed
# mid-season, and a rename must not silently switch the highlighting off. The
# name is kept for display and as a cross-check that the id still points at the
# team we think it does.
JASON_X_TEAM_ID = 1
JASON_X_TEAM_NAME = "Jason X"

# availability_status in player_index.json -> the label shown in the report.
AVAILABILITY_LABELS: dict[str, str] = {
    "FREEAGENT": "FREE AGENT",
    "WAIVERS": "WAIVERS",
}

OWNER_UNKNOWN = "UNKNOWN"

# ESPN rejects a players filter with limit > 1500 (HTTP 400) and returns only 50
# players when no X-Fantasy-Filter is sent at all. 1000 is the page size the
# existing player-pool fetch already proves, and this league's pool is ~1,055,
# so every week takes two requests.
RECENT_FORM_PAGE_SIZE = 1000
RECENT_FORM_MAX_PAGES = 6

# Diagnostic tolerance for the appliedTotal cross-check. Exceeding it records a
# warning; it never changes a ranking.
SCORING_CROSSCHECK_TOLERANCE = 0.01

# Written into the artifact so a reader of the JSON alone understands the rules.
RECENT_FORM_USAGE = (
    "Recent form rankings. Each player is ranked on the average actual fantasy "
    "points of that player's own last three completed games, under this league's "
    "exact scoring. The three games are the three highest scoring periods for "
    "which ESPN reported an actual score for that player, so two players in the "
    "same table can be using different weeks after a bye. A game qualifies "
    "only when the player actually appeared in it: ESPN must have reported an "
    "actual entry (statSourceId 0) for that scoring period AND that entry must "
    "carry the games-played marker (stat 210). The presence of a scoring row "
    "alone is not enough, because ESPN also emits 0.0 rows for players who did "
    "not play, and counting those would understate recent form. A game the "
    "player did appear in counts even when it scored 0.00 - a receiver targeted "
    "with no catch, a quarterback with one incomplete pass - and "
    "diagnostics.zero_point_games_kept reports how many such real appearances "
    "were kept, while diagnostics.not_played_excluded reports how many "
    "did-not-play rows were discarded. A bye or a missed game simply does not "
    "count. A player with fewer than three qualifying games is excluded "
    "entirely; nothing is padded and no placeholder row exists. "
    "Weekly points come from ESPN's appliedTotal on a league-scoped "
    "request, which already applies this league's scoring settings; "
    "scoring_crosscheck independently recomputes them from league.json "
    "scoring_settings and reports the largest disagreement. Lists are sorted by "
    "avg_3_game_exact descending with ties broken by ascending ESPN player id; "
    "avg_3_game_display carries the two decimals to show. owner_label is the "
    "owning fantasy team's current name, or FREE AGENT, or WAIVERS, or UNKNOWN "
    "when the id is absent from player_index.json, resolved by ESPN player id "
    "only. is_jason_x is true only when that index reports fantasy_status ROSTERED "
    "and fantasy_team_id equal to ownership.jason_x_team_id, regardless of "
    "starter, bench or IR slot, and it is what drives the pale-yellow row "
    "highlight in the daily PDF: highlight every row where it is true and no other "
    "row. The team id is the authority rather than the team name because teams in "
    "this league get renamed mid-season; ownership.jason_x_team_name_observed is "
    "the name that id currently carries, and ownership.jason_x_name_id_agree is "
    "false when the name no longer selects the same players as the id. Because "
    "a game counts once the player has appeared in it, a snapshot taken while "
    "games are in progress can count a partially played week for players whose "
    "game has already kicked off; weeks.with_data shows which periods contributed."
)

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
        absolute: bool = False,
    ) -> Any:
        """GET the league endpoint and return parsed JSON.

        ``path`` is appended to the league base URL. Set ``absolute`` when
        ``path`` is a complete URL instead, which the season-scoped schedule
        endpoint needs since it sits outside the league path.
        """
        query: dict[str, Any] = dict(params or {})
        if views:
            query["view"] = [views] if isinstance(views, str) else list(views)
        # Unique cache-buster per call so nothing can serve us a stale body.
        query["cb"] = f"{int(time.time() * 1000)}{random.randint(1000, 9999)}"

        base = path if absolute else self.base + path
        url = base + "?" + urllib.parse.urlencode(query, doseq=True)
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


NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _fold(name: str) -> str:
    """Lowercase and strip accents, leaving punctuation in place."""
    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return folded.lower().replace("&", " and ")


def _strip_suffix(key: str) -> str:
    parts = key.split()
    while len(parts) > 2 and parts[-1] in NAME_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def normalize_name(name: str) -> str:
    """The canonical lookup key: punctuation becomes whitespace.

    "Amon-Ra St. Brown" -> "amon ra st brown"
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", _fold(name)).split())


def normalize_name_tight(name: str) -> str:
    """Alternate key: punctuation is deleted rather than spaced.

    Needed because punctuation inside a name is usually written without a space
    when the punctuation is dropped:
      "A.J. Brown"   -> "aj brown"     (not "a j brown")
      "Ja'Marr Chase"-> "jamarr chase"
      "Jaguars D/ST" -> "jaguars dst"
    Indexing both forms is what lets "AJ Brown" and "A.J. Brown" both resolve.
    """
    return " ".join(re.sub(r"[^a-z0-9\s]+", "", _fold(name)).split())


def name_keys(name: str) -> list[str]:
    """Every lookup key a given player name should answer to."""
    keys: list[str] = []
    for key in (normalize_name(name), normalize_name_tight(name)):
        for variant in (key, _strip_suffix(key)):
            if variant and variant not in keys:
                keys.append(variant)
    return keys


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime:
    """The nth occurrence of a weekday in a month, e.g. 2nd Sunday of March."""
    first = datetime(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _central_offset_hours(moment: datetime) -> int:
    """US Central UTC offset for a moment, by the post-2007 DST rule.

    DST runs from the second Sunday in March to the first Sunday in November,
    switching at 02:00 local time. Implemented explicitly because zoneinfo is
    unavailable on a stock Windows install and this project ships no
    dependencies; ``to_central`` prefers zoneinfo when it is importable.
    """
    naive = moment.replace(tzinfo=None) + timedelta(hours=CT_STANDARD_OFFSET)
    year = naive.year
    start = _nth_weekday(year, 3, 6, 2) + timedelta(hours=2)    # 2nd Sunday Mar
    end = _nth_weekday(year, 11, 6, 1) + timedelta(hours=2)     # 1st Sunday Nov
    return CT_DAYLIGHT_OFFSET if start <= naive < end else CT_STANDARD_OFFSET


_CENTRAL_TZ = None
_CENTRAL_TZ_SOURCE = "manual-us-central-rule"
try:                                              # pragma: no cover - env dependent
    from zoneinfo import ZoneInfo

    _CENTRAL_TZ = ZoneInfo(CENTRAL_TZ_NAME)
    _CENTRAL_TZ_SOURCE = f"zoneinfo:{CENTRAL_TZ_NAME}"
except Exception:                                 # pragma: no cover - env dependent
    _CENTRAL_TZ = None


def central_timezone_source() -> str:
    """Which conversion path is active, so divergence is visible in metadata."""
    return _CENTRAL_TZ_SOURCE


def to_central(moment: datetime | None) -> datetime | None:
    """Convert an aware UTC datetime to US Central."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if _CENTRAL_TZ is not None:
        return moment.astimezone(_CENTRAL_TZ)
    hours = _central_offset_hours(moment.astimezone(timezone.utc))
    # Name the offset so tzname() yields CST/CDT rather than "UTC-06:00".
    label = "CDT" if hours == CT_DAYLIGHT_OFFSET else "CST"
    return moment.astimezone(timezone(timedelta(hours=hours), label))


def to_central_iso(moment: datetime | None) -> str | None:
    central = to_central(moment)
    return central.isoformat() if central else None


def to_central_label(moment: datetime | None) -> str | None:
    """Short human label, e.g. 'Thu Nov 6, 6:04 PM CST'."""
    central = to_central(moment)
    if central is None:
        return None
    offset = central.utcoffset() or timedelta(0)
    name = central.tzname() or (
        "CDT" if offset == timedelta(hours=CT_DAYLIGHT_OFFSET) else "CST"
    )
    hour = central.strftime("%I").lstrip("0") or "12"
    return (
        f"{central.strftime('%a %b')} {central.day}, "
        f"{hour}:{central.strftime('%M %p')} {name}"
    )


def iso_to_central_label(value: str | None) -> str | None:
    """CT label for an ISO-8601 string, used for ESPN timestamps we pass along."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return to_central_label(parsed)


def _round(value: Any, digits: int = 2) -> Any:
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return value


def _ms_to_iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _dollars(value: Any) -> Any:
    """Keep whole dollar amounts as ints so they render as $200, not $200.0."""
    if isinstance(value, (int, float)):
        rounded = round(float(value), 2)
        return int(rounded) if rounded.is_integer() else rounded
    return value


def _faab_block(budget: int | None, transaction_counter: dict) -> dict | None:
    """Per-team FAAB position, or None when the league does not use a budget."""
    if budget is None:
        return None
    spent = transaction_counter.get("acquisitionBudgetSpent") or 0
    return {
        "budget": _dollars(budget),
        "spent": _dollars(spent),
        "remaining": _dollars(budget - spent),
        "percent_remaining": _round(100.0 * (budget - spent) / budget, 1) if budget else None,
    }


def faab_budget(acquisition_settings: dict | None) -> int | None:
    """The per-team FAAB budget, or None when the league does not use one.

    ESPN exposes this as ``acquisitionBudget`` but it is only meaningful when
    ``isUsingAcquisitionBudget`` is set; leagues on pure waiver priority still
    report a budget value that means nothing.
    """
    settings = acquisition_settings or {}
    if not settings.get("isUsingAcquisitionBudget"):
        return None
    budget = settings.get("acquisitionBudget")
    return budget if isinstance(budget, (int, float)) else None


def build_rosters(
    teams: list[dict],
    members_by_id: dict[str, dict],
    lineup_slot_counts: dict[str, int],
    acquisition_settings: dict | None = None,
    injury_report: dict[str, dict] | None = None,
    bye_weeks: dict[int, int] | None = None,
) -> list[dict]:
    """Normalize every team's roster into starters / bench / IR buckets."""
    budget = faab_budget(acquisition_settings)
    injury_report = injury_report or {}
    bye_weeks = bye_weeks or {}
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
            badge = injury_badge(record["player_id"], injury_report)
            if badge:
                record["injury"] = badge
            # Two different things, and they disagree in both directions: a
            # manager can park a healthy player in the fantasy IR slot, and a
            # player on NFL injured reserve can sit on an active bench spot.
            record["in_fantasy_ir_slot"] = slot_id == IR_SLOT
            record["on_nfl_ir"] = bool(badge and badge.get("on_nfl_ir"))
            record["bye_week"] = bye_weeks.get(record.get("pro_team_id"))
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
                "faab": _faab_block(budget, transaction_counter),
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

    # ESPN keeps losing and invalid waiver claims in the log with a FAILED_*
    # status. These must never be read as acquisitions or as FAAB spend.
    failed = [
        t
        for t in (ownership_changes + lineup_moves)
        if (t.get("status") or "").startswith("FAILED")
    ]

    waiver_bids = [
        {
            "team_id": t.get("team_id"),
            "team": t.get("team"),
            "bid_amount": t.get("bid_amount"),
            "status": t.get("status"),
            "succeeded": t.get("status") == "EXECUTED",
            "scoring_period_id": t.get("scoring_period_id"),
            "date": t.get("processed_date") or t.get("proposed_date"),
            "players_added": [
                i.get("player_id")
                for i in t.get("items") or []
                if i.get("item_type") == "ADD"
            ],
        }
        for t in ownership_changes
        if t.get("type") == "WAIVER" and (t.get("bid_amount") or 0)
    ]
    waiver_bids.sort(key=lambda b: (b.get("date") or ""), reverse=True)

    return {
        "counts": {
            "total": len(raw_transactions),
            "ownership_changes": len(ownership_changes),
            "executed_ownership_changes": len(executed_ownership),
            "lineup_only_moves": len(lineup_moves),
            "draft_picks": len(draft_picks),
            "pending": len(pending),
            "failed": len(failed),
            "waiver_bids": len(waiver_bids),
            "waiver_bids_won": sum(1 for b in waiver_bids if b["succeeded"]),
        },
        "waiver_bids": waiver_bids,
        "executed_ownership_changes": executed_ownership,
        "pending_transactions": pending,
        "failed_transactions": failed,
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
                "faab": team.get("faab"),
                "waiver_rank": team.get("waiver_rank"),
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


def build_player_index(
    rosters: list[dict],
    available_players: list[dict],
    injury_report: dict[str, dict] | None = None,
    bye_weeks: dict[int, int] | None = None,
) -> dict:
    """Build one flat, name-addressable table covering every classified player.

    This exists so a consumer can answer "who owns X?" with a single exact
    lookup, instead of re-deriving ownership from the much larger rosters and
    player-pool files. Every player ESPN has classified appears exactly once,
    labelled either ROSTERED (with the owning fantasy team) or AVAILABLE (with
    ESPN's own FREEAGENT/WAIVERS status).

    ``by_normalized_name`` maps both the suffix-preserving and suffix-stripped
    name forms to player ids, so "Michael Pittman" and "Michael Pittman Jr."
    both resolve. Names that map to more than one player are listed in
    ``collisions``; resolve those by position and pro_team.
    """
    injury_report = injury_report or {}
    bye_weeks = bye_weeks or {}
    players: list[dict] = []

    for team in rosters:
        for bucket in ("starters", "bench", "injured_reserve"):
            for player in team[bucket]:
                badge = injury_badge(player["player_id"], injury_report)
                players.append(
                    {
                        "player_id": player["player_id"],
                        "name": player["name"],
                        "normalized_name": normalize_name(player["name"]),
                        "position": player["position"],
                        "pro_team": player["pro_team"],
                        "fantasy_status": "ROSTERED",
                        "fantasy_team": team["team_name"],
                        "fantasy_team_id": team["team_id"],
                        "availability_status": None,
                        "lineup_slot": player["lineup_slot"],
                        "injury_status": player.get("injury_status"),
                        "in_fantasy_ir_slot": player["lineup_slot"] == "IR" or None,
                        "on_nfl_ir": (badge or {}).get("on_nfl_ir") or None,
                        "injury": badge,
                        "bye_week": bye_weeks.get(player.get("pro_team_id")),
                        "percent_owned": player.get("percent_owned"),
                    }
                )

    for player in available_players:
        badge = injury_badge(player["player_id"], injury_report)
        players.append(
            {
                "player_id": player["player_id"],
                "name": player["name"],
                "normalized_name": normalize_name(player["name"]),
                "position": player["position"],
                "pro_team": player["pro_team"],
                "fantasy_status": "AVAILABLE",
                "fantasy_team": None,
                "fantasy_team_id": None,
                "availability_status": player.get("availability_status"),
                "lineup_slot": None,
                "injury_status": player.get("injury_status"),
                "in_fantasy_ir_slot": None,
                "on_nfl_ir": (badge or {}).get("on_nfl_ir") or None,
                "injury": badge,
                "bye_week": bye_weeks.get(player.get("pro_team_id")),
                "percent_owned": player.get("percent_owned"),
            }
        )

    players.sort(key=lambda p: p["normalized_name"])

    # Drop null-valued keys. Roughly a third of these records are available
    # players that carry no fantasy team or lineup slot, and the nulls are pure
    # payload. An absent key means null; this is stated in "usage" below.
    players = [{k: v for k, v in p.items() if v is not None} for p in players]

    by_name: dict[str, list[int]] = {}
    for player in players:
        for key in name_keys(player["name"]):
            bucket = by_name.setdefault(key, [])
            if player["player_id"] not in bucket:
                bucket.append(player["player_id"])

    collisions = {
        name: ids for name, ids in sorted(by_name.items()) if len(ids) > 1
    }

    return {
        "usage": (
            "Authoritative ownership lookup for every classified player in this "
            "league. To resolve a name: lowercase it, strip accents, then build two "
            "candidate keys, one replacing each run of punctuation with a single "
            "space ('A.J. Brown' -> 'a j brown') and one deleting punctuation "
            "outright ('A.J. Brown' -> 'aj brown'); collapse whitespace in both. "
            "Also try each key with a trailing Jr/Sr/II/III/IV/V removed. Look each "
            "candidate up in by_normalized_name to get player_ids, then read the "
            "record from players_by_id. Keys with null values are omitted from "
            "records, so a missing fantasy_team means the player is not rostered. "
            "Report ownership as unresolved only when every candidate key is absent "
            "from by_normalized_name, or when several players survive "
            "disambiguation by position and pro_team. A failed code search or a "
            "partial file read is not grounds for declaring ownership unresolved."
        ),
        "counts": {
            "total": len(players),
            "rostered": sum(1 for p in players if p["fantasy_status"] == "ROSTERED"),
            "available": sum(1 for p in players if p["fantasy_status"] == "AVAILABLE"),
            "unique_name_keys": len(by_name),
            "collisions": len(collisions),
        },
        "collisions": collisions,
        "by_normalized_name": by_name,
        "players_by_id": {str(p["player_id"]): p for p in players},
    }


# --------------------------------------------------------------------------
# NFL schedule and bye weeks
# --------------------------------------------------------------------------


def fetch_pro_schedule(
    client: EspnClient, season: int
) -> tuple[dict[int, dict], dict[str, Any]]:
    """Every NFL team's bye week and per-scoring-period opponent.

    Source is the season endpoint's ``proTeamSchedules_wl`` view, which returns
    ``settings.proTeams`` with ``byeWeek`` and ``proGamesByScoringPeriod``. This
    removes the one genuinely factual thing the report previously had to get
    from web research, and it is also the only way to know a bye without
    guessing: a team's bye period is simply absent from its game map.

    Returns ({pro_team_id: record}, diag).
    """
    diag: dict[str, Any] = {"ok": False, "teams": 0, "error": None}
    try:
        payload = client.get(
            path=f"{READ_HOST}/apis/v3/games/ffl/seasons/{season}",
            views="proTeamSchedules_wl",
            label="pro_schedule",
            absolute=True,
        )
    except FetchError as exc:
        diag["error"] = str(exc)
        return {}, diag

    teams: dict[int, dict] = {}
    for team in (payload.get("settings") or {}).get("proTeams") or []:
        if not isinstance(team, dict):
            continue
        team_id = team.get("id")
        if not isinstance(team_id, int):
            continue
        games: dict[str, dict] = {}
        for period, period_games in (team.get("proGamesByScoringPeriod") or {}).items():
            for game in period_games or []:
                home = game.get("homeProTeamId")
                away = game.get("awayProTeamId")
                if team_id not in (home, away):
                    continue
                at_home = home == team_id
                kickoff = _ms_to_iso(game.get("date"))
                games[str(period)] = {
                    "opponent_pro_team_id": away if at_home else home,
                    "opponent": PRO_TEAMS.get(
                        away if at_home else home, "UNKNOWN"
                    ),
                    "at_home": at_home,
                    "kickoff_utc": kickoff,
                    "kickoff_ct": iso_to_central_label(kickoff),
                    "start_time_tbd": game.get("startTimeTBD"),
                }
                break
        teams[team_id] = {
            "pro_team_id": team_id,
            "abbrev": team.get("abbrev"),
            "name": " ".join(
                p for p in (team.get("location"), team.get("name")) if p
            ).strip()
            or team.get("abbrev"),
            "bye_week": team.get("byeWeek"),
            "games_by_scoring_period": games,
        }

    diag["ok"] = len(teams) > 0
    diag["teams"] = len(teams)
    return teams, diag


def bye_week_map(pro_schedule: dict[int, dict]) -> dict[int, int]:
    """{pro_team_id: bye_week}, excluding the free-agent pseudo-team."""
    return {
        tid: rec["bye_week"]
        for tid, rec in (pro_schedule or {}).items()
        if isinstance(rec.get("bye_week"), int) and rec["bye_week"] > 0
    }


def upcoming_games(
    pro_schedule: dict[int, dict], pro_team_id: Any, weeks: list[int]
) -> list[dict]:
    """The team's games for the given scoring periods, byes marked explicitly."""
    record = (pro_schedule or {}).get(pro_team_id)
    if not record:
        return []
    out = []
    for week in weeks:
        game = (record.get("games_by_scoring_period") or {}).get(str(week))
        if game is None:
            out.append({"scoring_period_id": week, "bye": True})
        else:
            out.append({"scoring_period_id": week, "bye": False, **game})
    return out


# --------------------------------------------------------------------------
# Injury report
# --------------------------------------------------------------------------


def _athlete_id_from_links(athlete: dict) -> str | None:
    """Recover the ESPN athlete id from a record's link URLs.

    The league-wide injury feed omits ``athlete.id`` but every link href looks
    like ``.../nfl/player/_/id/4360761/michael-wilson``. Pulling the id out of
    there keeps the join on an id instead of on a name.
    """
    for link in (athlete or {}).get("links") or []:
        match = ATHLETE_ID_IN_LINK.search(link.get("href") or "")
        if match:
            return match.group(1)
    return None


def _return_date_label(return_date: str | None) -> str | None:
    """Human label for a return date, collapsing the season-ending sentinel."""
    if not return_date:
        return None
    try:
        parsed = datetime.strptime(return_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return return_date
    if (parsed.month, parsed.day) >= SEASON_ENDING_RETURN_MONTH and parsed.month <= 7:
        return "out for the season"
    # "%-d" is not portable to Windows, so strip the leading zero by hand.
    return f"{parsed.strftime('%b')} {parsed.day}"


def fetch_injury_report(timeout: int = 45) -> tuple[dict[str, dict], dict[str, Any]]:
    """ESPN's public NFL injury feed, keyed by ESPN player id as a string.

    Returns (report, diag). Never raises: this is a supplementary source on a
    second host, and the snapshot must survive it being unavailable.
    """
    diag: dict[str, Any] = {
        "source": INJURY_FEED_URL,
        "ok": False,
        "records": 0,
        "with_player_id": 0,
        "designated": 0,
        "error": None,
    }
    report: dict[str, dict] = {}

    request = urllib.request.Request(INJURY_FEED_URL)
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")
    request.add_header("Accept-Encoding", "gzip")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.GzipFile(fileobj=io.BytesIO(body)).read()
            payload = json.loads(body.decode("utf-8"))
    except Exception as exc:                      # noqa: BLE001 - supplementary
        diag["error"] = f"{type(exc).__name__}: {exc}"
        return report, diag

    for group in payload.get("injuries") or []:
        for item in group.get("injuries") or []:
            if not isinstance(item, dict):
                continue
            diag["records"] += 1
            athlete = item.get("athlete") or {}
            player_id = _athlete_id_from_links(athlete)
            if not player_id:
                continue
            diag["with_player_id"] += 1
            details = item.get("details") or {}
            fantasy = details.get("fantasyStatus") or {}
            status = item.get("status")
            designation = fantasy.get("abbreviation") or status
            if status in ("Active", None) and not details.get("returnDate"):
                # Healthy and nothing scheduled: nothing worth carrying.
                continue
            return_date = details.get("returnDate")
            record = {
                "player_id": int(player_id) if player_id.lstrip("-").isdigit() else player_id,
                "name": athlete.get("displayName"),
                "nfl_status": status,
                "designation": designation,
                "on_nfl_ir": status in NFL_IR_STATUSES
                or (isinstance(designation, str) and designation.startswith("IR")),
                "expected_return": return_date,
                "expected_return_label": _return_date_label(return_date),
                "injury_type": details.get("type"),
                "injury_detail": details.get("detail"),
                "body_location": details.get("location"),
                "side": details.get("side"),
                "note": item.get("shortComment"),
                "reported_at": item.get("date"),
            }
            if record["on_nfl_ir"]:
                diag["designated"] += 1
            report[player_id] = {k: v for k, v in record.items() if v is not None}

    diag["ok"] = True
    diag["on_nfl_ir"] = diag.pop("designated")
    return report, diag


def is_out_for_season(badge: dict | None) -> bool:
    """True when ESPN expects the player back only next season."""
    return bool(badge) and badge.get("expected_return_label") == OUT_FOR_SEASON_LABEL


def injury_badge(player_id: Any, injury_report: dict[str, dict]) -> dict | None:
    """The compact block attached to a player wherever that player appears.

    Deliberately small: a designation, the IR flag and the expected return.
    The full record, including the beat note and injury type, lives once in
    data/injuries.json keyed by the same id.
    """
    record = (injury_report or {}).get(str(player_id))
    if not record:
        return None
    badge = {
        "designation": record.get("designation"),
        "nfl_status": record.get("nfl_status"),
        "on_nfl_ir": record.get("on_nfl_ir", False),
        "expected_return": record.get("expected_return"),
        "expected_return_label": record.get("expected_return_label"),
        "injury_type": record.get("injury_type"),
    }
    return {k: v for k, v in badge.items() if v is not None}


# --------------------------------------------------------------------------
# Recent form rankings
# --------------------------------------------------------------------------


def scoring_items_by_stat_id(scoring_settings: dict | None) -> dict[int, dict]:
    """Index league scoringItems by statId for the appliedTotal cross-check.

    Returns {} when the settings are missing, which disables the cross-check
    rather than failing the step.
    """
    items: dict[int, dict] = {}
    for item in (scoring_settings or {}).get("scoringItems") or []:
        if not isinstance(item, dict):
            continue
        try:
            items[int(item["statId"])] = item
        except (KeyError, TypeError, ValueError):
            continue
    return items


def recompute_applied_total(
    raw_stats: dict | None,
    scoring_items: dict[int, dict],
    position_id: Any,
) -> float:
    """Recompute a weekly score from league scoringItems. Diagnostic only.

    ESPN's inner per-week ``stats`` dict comes straight from JSON, so its keys
    are strings while ``scoring_items`` is keyed by int. Without the int()
    coercion every lookup misses and the cross-check reports a false delta the
    size of the largest weekly score. ``pointsOverrides`` is equally
    load-bearing: this league keys overrides by defaultPositionId as a string,
    so ignoring them reports a large false delta on every D/ST.
    """
    total = 0.0
    for raw_key, raw_value in (raw_stats or {}).items():
        try:
            stat_id = int(raw_key)      # ESPN ships these as JSON string keys
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        item = scoring_items.get(stat_id)
        if item is None:
            continue
        points = (item.get("pointsOverrides") or {}).get(
            str(position_id), item.get("points")
        )
        try:
            total += value * float(points)
        except (TypeError, ValueError):
            continue
    return total


def played_in_game(raw_stats: dict | None) -> bool:
    """Did the player actually appear in this game?

    True only when ESPN's games-played marker is present with a value of at
    least 1. The presence of a weekly scoring row is NOT sufficient: ESPN emits
    0.0 rows for players who were on a roster but did not play, and counting
    those as completed games materially misstates recent form.
    """
    value = (raw_stats or {}).get(GAMES_PLAYED_STAT_ID)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value >= 1


def extract_weekly_entry(
    player_obj: dict, season: int, week: int
) -> tuple[float, dict] | None:
    """(appliedTotal, raw stats dict) for the week's actual, or None.

    Selects on statSourceId == 0 (actual, not projection), scoringPeriodId ==
    week (not the season aggregate, which uses 0) and seasonId == season (prior
    seasons share the same array). All three are required to identify the row.
    """
    for entry in (player_obj or {}).get("stats") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("statSourceId") != 0:
            continue
        if entry.get("scoringPeriodId") != week:
            continue
        if entry.get("seasonId") != season:
            continue
        applied = entry.get("appliedTotal")
        if not isinstance(applied, (int, float)) or isinstance(applied, bool):
            return None
        if not math.isfinite(applied):
            return None
        return float(applied), (entry.get("stats") or {})
    return None


def fetch_weekly_actuals(
    client: EspnClient,
    season: int,
    week: int,
    scoring_items: dict[int, dict],
    page_size: int = RECENT_FORM_PAGE_SIZE,
    max_pages: int = RECENT_FORM_MAX_PAGES,
) -> tuple[dict[int, dict], dict[int, dict], dict[str, int]]:
    """One scoring period's actual points for the whole league-scoped pool.

    No ``filterStatus``: omitting it returns ONTEAM + WAIVERS + FREEAGENT in one
    response, including D/ST units with negative ids. An ``X-Fantasy-Filter`` is
    still required for paging, because with no filter header at all the endpoint
    returns only 50 players and no D/ST. ``filterStatsForTopScoringPeriodIds``
    must never be sent: it is an immediate HTTP 400 on this endpoint.

    Returns (actuals, meta, diag) where
      actuals = {player_id: {"points", "applied_raw", "recomputed"}}
      meta    = {player_id: {"name", "position", "pro_team", "position_id"}}
      diag    = {"requests", "invalid_values", "not_played_excluded",
                 "zero_point_games_kept"}

    Only games the player actually appeared in are recorded; see
    ``played_in_game``. A weekly row for a player who did not play is counted in
    ``diag["not_played_excluded"]`` and otherwise discarded.
    """
    actuals: dict[int, dict] = {}
    meta: dict[int, dict] = {}
    diag = {
        "requests": 0,
        "invalid_values": 0,
        "not_played_excluded": 0,
        "zero_point_games_kept": 0,
    }
    offset = 0

    for _ in range(max_pages):
        fantasy_filter = {
            "players": {
                "limit": page_size,
                "offset": offset,
                "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
            }
        }
        payload = client.get(
            views="kona_player_info",
            params={"scoringPeriodId": week},
            fantasy_filter=fantasy_filter,
            label=f"recent_form@w{week}+{offset}",
        )
        diag["requests"] += 1
        page = payload.get("players") or []

        for wrapper in page:
            if not isinstance(wrapper, dict):
                continue
            player_obj = wrapper.get("player") or {}
            pid = wrapper.get("id")
            if not isinstance(pid, int):
                continue
            position_id = player_obj.get("defaultPositionId")
            meta[pid] = {
                "name": player_obj.get("fullName"),
                "position": POSITIONS.get(position_id, "UNKNOWN"),
                "pro_team": PRO_TEAMS.get(player_obj.get("proTeamId"), "UNKNOWN"),
                "position_id": position_id,
            }
            found = extract_weekly_entry(player_obj, season, week)
            if found is None:
                # Either no row for this week (bye / did not play) or a row whose
                # appliedTotal was unusable. Only the latter is a data problem.
                if any(
                    isinstance(e, dict)
                    and e.get("statSourceId") == 0
                    and e.get("scoringPeriodId") == week
                    and e.get("seasonId") == season
                    for e in player_obj.get("stats") or []
                ):
                    diag["invalid_values"] += 1
                continue
            applied_total, raw_stats = found
            if not played_in_game(raw_stats):
                # ESPN reported a 0.0 row for a player who did not appear. This
                # is NOT a completed game; counting it would understate form.
                diag["not_played_excluded"] += 1
                continue
            if applied_total == 0.0:
                # A real appearance that produced nothing. Kept, and counted so
                # the magnitude stays visible.
                diag["zero_point_games_kept"] += 1
            actuals[pid] = {
                "points": _round(applied_total),
                "applied_raw": applied_total,
                "recomputed": (
                    recompute_applied_total(raw_stats, scoring_items, position_id)
                    if scoring_items
                    else None
                ),
            }

        if len(page) < page_size:
            break
        offset += page_size

    return actuals, meta, diag


def resolve_ownership(
    player_id: Any,
    players_by_id: dict[str, dict],
    jason_x_team_id: Any = JASON_X_TEAM_ID,
    jason_x_team_name: str = JASON_X_TEAM_NAME,
) -> dict:
    """Owner label and Jason X flag for one ESPN player id.

    Pure: the only inputs are the id and the index mapping, so a synthetic index
    produces synthetic ownership with no code change. This is the single place
    that decides ``is_jason_x``, and it decides it on the fantasy team **id**,
    never on the team name, because teams here get renamed mid-season.
    """
    unknown = {
        "owner_label": OWNER_UNKNOWN,
        "is_jason_x": False,
        "fantasy_status": None,
        "owner_team_id": None,
        "owner_name_is_jason_x": False,
    }

    record = (players_by_id or {}).get(str(player_id))
    if not isinstance(record, dict):
        return dict(unknown)

    status = record.get("fantasy_status")
    if status == "ROSTERED":
        team_name = record.get("fantasy_team")
        team_id = record.get("fantasy_team_id")
        has_name = isinstance(team_name, str) and team_name.strip() != ""
        return {
            # The team's *current* name, so a rename surfaces in the OWNER column.
            "owner_label": team_name if has_name else OWNER_UNKNOWN,
            # The id is the authority: a missing display name must not switch
            # highlighting off.
            "is_jason_x": team_id == jason_x_team_id,
            "fantasy_status": status,
            "owner_team_id": team_id,
            "owner_name_is_jason_x": has_name and team_name == jason_x_team_name,
        }

    label = AVAILABILITY_LABELS.get(record.get("availability_status"), OWNER_UNKNOWN)
    return {
        "owner_label": label,
        "is_jason_x": False,
        "fantasy_status": status,
        "owner_team_id": None,
        "owner_name_is_jason_x": False,
    }


def recent_form_weeks(status: dict, scoring_period_id: int | None) -> list[int]:
    """Candidate scoring periods: 1..latest, capped at the season's final week."""
    last = None
    for candidate in ((status or {}).get("latestScoringPeriod"), scoring_period_id):
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
            last = candidate
            break
    if last is None:
        return []

    final = (status or {}).get("finalScoringPeriod")
    if not (isinstance(final, int) and not isinstance(final, bool) and 1 <= final <= 30):
        final = 18
    return list(range(1, min(last, final) + 1))


def build_recent_form(
    weekly: dict[int, dict[int, dict]],
    meta: dict[int, dict],
    players_by_id: dict[str, dict],
    weeks_considered: list[int],
    limits: dict[str, int] | None = None,
    games_required: int = RECENT_FORM_GAMES,
    jason_x_team_id: Any = JASON_X_TEAM_ID,
    jason_x_team_name: str = JASON_X_TEAM_NAME,
    injury_report: dict[str, dict] | None = None,
) -> dict:
    """Assemble the ranked payload. Pure: no I/O, no network, no clock."""
    limits = RECENT_FORM_LIMITS if limits is None else limits
    injury_report = injury_report or {}

    rankings: dict[str, list[dict]] = {}
    counts: dict[str, dict] = {}
    crosscheck_rows: list[tuple[int, dict, int, dict]] = []
    excluded_out_for_season: list[dict] = []
    qualifying_ids: dict[str, list[int]] = {}

    for position, limit in limits.items():
        candidates: list[dict] = []
        for pid, info in meta.items():
            if info.get("position") != position:
                continue
            badge = injury_badge(pid, injury_report)
            if is_out_for_season(badge):
                # Excluded before sorting so the limit backfills with someone
                # who can actually play. Recorded so the omission is auditable.
                excluded_out_for_season.append(
                    {
                        "player_id": pid,
                        "name": info.get("name"),
                        "position": position,
                        "designation": badge.get("designation"),
                        "expected_return": badge.get("expected_return"),
                    }
                )
                continue
            weeks_for_player = weekly.get(pid) or {}
            # The three highest week numbers, not the three best scores.
            chosen = sorted(weeks_for_player, reverse=True)[:games_required]
            if len(chosen) < games_required:
                continue
            chosen.sort()                       # ascending: oldest game first
            games = [
                {"scoring_period_id": wk, "points": weeks_for_player[wk]["points"]}
                for wk in chosen
            ]
            avg_exact = sum(g["points"] for g in games) / games_required
            name = info.get("name")
            if not name:
                record = (players_by_id or {}).get(str(pid))
                name = (record or {}).get("name") if isinstance(record, dict) else None
            candidates.append(
                {
                    "player_id": pid,
                    "name": name or f"Player {pid}",
                    "position": position,
                    "pro_team": info.get("pro_team", "UNKNOWN"),
                    "games": games,
                    "avg_3_game_exact": avg_exact,
                    "avg_3_game": round(avg_exact, 6),
                    "avg_3_game_display": f"{avg_exact:.2f}",
                }
            )

        # Descending unrounded average, ties broken by ascending player id so the
        # ordering is total and byte-stable between runs.
        candidates.sort(key=lambda e: (-e["avg_3_game_exact"], e["player_id"]))
        qualifying = len(candidates)
        # Every player with three confirmed appearances, ranked or not. Lets a
        # consumer tell "outside the top N" from "fewer than three games".
        qualifying_ids[position] = [e["player_id"] for e in candidates]
        ranked = candidates[:limit]

        for index, entry in enumerate(ranked, 1):
            entry["rank"] = index
            own = resolve_ownership(
                entry["player_id"], players_by_id, jason_x_team_id, jason_x_team_name
            )
            entry["owner_label"] = own["owner_label"]
            entry["owner_team_id"] = own["owner_team_id"]
            entry["fantasy_status"] = own["fantasy_status"]
            entry["is_jason_x"] = own["is_jason_x"]
            badge = injury_badge(entry["player_id"], injury_report)
            if badge:
                entry["injury"] = badge
                entry["on_nfl_ir"] = badge.get("on_nfl_ir", False)
            index_record = (players_by_id or {}).get(str(entry["player_id"])) or {}
            if index_record.get("in_fantasy_ir_slot"):
                entry["in_fantasy_ir_slot"] = True
            for game in entry["games"]:
                row = (weekly.get(entry["player_id"]) or {})[game["scoring_period_id"]]
                if row.get("recomputed") is not None:
                    crosscheck_rows.append(
                        (entry["player_id"], entry, game["scoring_period_id"], row)
                    )

        rankings[position] = ranked
        counts[position] = {
            "limit": limit,
            "qualifying": qualifying,
            "ranked": len(ranked),
            "sufficient": len(ranked) == limit,
            "weeks_used": sorted(
                {g["scoring_period_id"] for e in ranked for g in e["games"]}
            ),
        }

    ranked_total = sum(len(rows) for rows in rankings.values())

    # Cross-check over exactly the weekly values that landed in a ranked entry.
    worst: dict | None = None
    max_abs_delta: float | None = None
    for pid, entry, week, row in crosscheck_rows:
        delta = abs(row["applied_raw"] - row["recomputed"])
        if max_abs_delta is None or delta > max_abs_delta:
            max_abs_delta = delta
            worst = {
                "player_id": pid,
                "name": entry["name"],
                "position": entry["position"],
                "scoring_period_id": week,
                "applied_total": row["applied_raw"],
                "points_stored": row["points"],
                "recomputed": row["recomputed"],
                "abs_delta": delta,
            }
    if crosscheck_rows:
        crosscheck = {
            "values_checked": len(crosscheck_rows),
            "tolerance": SCORING_CROSSCHECK_TOLERANCE,
            "max_abs_delta": _round(max_abs_delta, 6),
            "ok": max_abs_delta <= SCORING_CROSSCHECK_TOLERANCE,
            "worst": worst,
        }
    else:
        crosscheck = {
            "values_checked": 0,
            "tolerance": SCORING_CROSSCHECK_TOLERANCE,
            "max_abs_delta": None,
            "ok": None,
            "worst": None,
            "note": "no league scoring table was available, so the cross-check was skipped",
        }
    crosscheck["note"] = crosscheck.get("note") or (
        "ESPN appliedTotal is authoritative and is the value ranked. This block "
        "recomputes sum(stats[statId] * scoringItems[statId].points), honouring "
        "pointsOverrides by defaultPositionId, purely so silent scoring drift is "
        "detectable. It never changes a ranking."
    )

    # Ownership block, including the id-versus-name cross-check. Index-wide, so a
    # rename is detected even in a week when no Jason X player is inside a limit.
    ids_by_id = {
        pid for pid, rec in (players_by_id or {}).items()
        if isinstance(rec, dict) and rec.get("fantasy_status") == "ROSTERED"
        and rec.get("fantasy_team_id") == jason_x_team_id
    }
    ids_by_name = {
        pid for pid, rec in (players_by_id or {}).items()
        if isinstance(rec, dict) and rec.get("fantasy_status") == "ROSTERED"
        and rec.get("fantasy_team") == jason_x_team_name
    }
    observed = sorted({
        rec.get("fantasy_team") for rec in (players_by_id or {}).values()
        if isinstance(rec, dict) and rec.get("fantasy_team_id") == jason_x_team_id
        and rec.get("fantasy_team")
    })
    all_entries = [e for rows in rankings.values() for e in rows]
    unknown_count = sum(1 for e in all_entries if e["owner_label"] == OWNER_UNKNOWN)

    return {
        "usage": RECENT_FORM_USAGE,
        "weeks": {
            "considered": list(weeks_considered),
            "games_required": games_required,
        },
        "limits": dict(limits),
        "expected_total": sum(limits.values()),
        "ranked_total": ranked_total,
        "all_positions_sufficient": all(b["sufficient"] for b in counts.values()),
        "counts": counts,
        "scoring_crosscheck": crosscheck,
        "ownership": {
            "source": "player_index.json players_by_id from this same snapshot run",
            "match": "ESPN player id only; no name matching",
            "jason_x_team_id": jason_x_team_id,
            "jason_x_team_name": jason_x_team_name,
            "jason_x_team_name_observed": (
                observed[0] if len(observed) == 1 else (" | ".join(observed) or None)
            ),
            "jason_x_name_id_agree": ids_by_id == ids_by_name,
            "jason_x_by_id_count": len(ids_by_id),
            "jason_x_by_name_count": len(ids_by_name),
            "entries": ranked_total,
            "resolved": ranked_total - unknown_count,
            "unknown": unknown_count,
            "is_jason_x_count": sum(1 for e in all_entries if e["is_jason_x"]),
        },
        "diagnostics": {
            "players_seen": len(meta),
            "weekly_values_collected": sum(len(w) for w in weekly.values()),
            "excluded_out_for_season": len(excluded_out_for_season),
        },
        "excluded_out_for_season": sorted(
            excluded_out_for_season, key=lambda e: (e["position"], e["name"] or "")
        ),
        "qualifying_player_ids": qualifying_ids,
        "rankings": rankings,
    }


# --------------------------------------------------------------------------
# Derived report inputs
# --------------------------------------------------------------------------


def build_jason_x_block(
    rosters: list[dict],
    lineup_slot_counts: dict[str, int],
    pro_schedule: dict[int, dict],
    weeks_ahead: list[int],
    jason_x_team_id: Any = JASON_X_TEAM_ID,
) -> dict | None:
    """Everything about Jason X's own team, precomputed.

    The report prompt previously had to rebuild starters, bench, IR and open
    spots on every run. That is a derivation with a wrong answer available, so
    the pipeline publishes it instead.
    """
    team = next(
        (t for t in rosters if t.get("team_id") == jason_x_team_id), None
    )
    if team is None:
        return None

    byes = bye_week_map(pro_schedule)

    def decorate(player: dict) -> dict:
        out = {
            "player_id": player["player_id"],
            "name": player["name"],
            "position": player["position"],
            "pro_team": player["pro_team"],
            "lineup_slot": player["lineup_slot"],
            "bye_week": byes.get(player.get("pro_team_id")),
            "percent_owned": player.get("percent_owned"),
            "injury": player.get("injury"),
            "on_nfl_ir": player.get("on_nfl_ir"),
            "in_fantasy_ir_slot": player.get("in_fantasy_ir_slot"),
            "upcoming": upcoming_games(
                pro_schedule, player.get("pro_team_id"), weeks_ahead
            ),
        }
        return {k: v for k, v in out.items() if v not in (None, [], {})}

    # ESPN returns starters in its own order; the report presents them in a
    # fixed slot order, so sort here rather than asking the consumer to.
    remaining = [decorate(p) for p in team["starters"]]
    starters: list[dict] = []
    for slot in STARTING_SLOT_ORDER:
        match = next((p for p in remaining if p.get("lineup_slot") == slot), None)
        if match is not None:
            starters.append(match)
            remaining.remove(match)
    starters.extend(remaining)      # any slot not in the canonical order

    bench = [decorate(p) for p in team["bench"]]
    ir = [decorate(p) for p in team["injured_reserve"]]

    # Which starting slots are on bye in each upcoming week.
    bye_pressure = []
    for week in weeks_ahead:
        affected = [
            {"name": p["name"], "position": p["position"],
             "lineup_slot": p["lineup_slot"]}
            for p in starters
            if p.get("bye_week") == week
        ]
        if affected:
            bye_pressure.append({"scoring_period_id": week, "starters_on_bye": affected})

    designated = [
        {
            "name": p["name"],
            "lineup_slot": p["lineup_slot"],
            "designation": (p.get("injury") or {}).get("designation"),
            "expected_return": (p.get("injury") or {}).get("expected_return_label"),
            "on_nfl_ir": p.get("on_nfl_ir", False),
        }
        for p in starters + bench + ir
        if p.get("injury")
    ]

    return {
        "team_id": team["team_id"],
        "team_name": team["team_name"],
        "owners": team["owners"],
        "record": team["record"],
        "faab": team.get("faab"),
        "waiver_rank": team.get("waiver_rank"),
        "counts": team["counts"],
        "capacity": team["capacity"],
        "open_starting_slots": team["open_starting_slots"],
        "roster_slot_counts": {
            LINEUP_SLOTS.get(int(slot), slot): count
            for slot, count in (lineup_slot_counts or {}).items()
            if count
        },
        "weeks_ahead": weeks_ahead,
        "starting_slot_order": list(STARTING_SLOT_ORDER),
        "starters": starters,
        "bench": bench,
        "injured_reserve": ir,
        "injury_designations": designated,
        "bye_pressure": bye_pressure,
        "has_open_bench_spot": team["capacity"]["bench_open"] > 0,
        "has_open_ir_spot": team["capacity"]["ir_open"] > 0,
    }


def recent_form_lookup(recent_form: dict | None) -> dict[int, dict]:
    """{player_id: {rank, avg, position}} plus a note for the merely qualifying.

    Section 4 and Section 6 of the report show a player's last-three-game rank,
    so it is attached inline rather than left as a cross-reference the consumer
    has to perform. "Not ranked" is ambiguous on its own, so the two reasons are
    distinguished: outside the position's limit, or fewer than three
    appearances.
    """
    out: dict[int, dict] = {}
    if not recent_form:
        return out

    limits = recent_form.get("limits") or {}

    # Season-ending exclusions come first: they may well have three appearances,
    # so reporting them as "fewer than three games" would be wrong.
    for entry in recent_form.get("excluded_out_for_season") or []:
        out[entry["player_id"]] = {
            "recent_form_position": entry.get("position"),
            "recent_form_note": f"excluded from the rankings: {OUT_FOR_SEASON_LABEL}",
        }

    for position, rows in (recent_form.get("rankings") or {}).items():
        for entry in rows:
            out[entry["player_id"]] = {
                "recent_form_rank": entry["rank"],
                "recent_form_position": position,
                "recent_form_avg": entry.get("avg_3_game_display"),
            }
    for position, ids in (recent_form.get("qualifying_player_ids") or {}).items():
        limit = limits.get(position)
        for pid in ids:
            if pid in out:
                continue        # already ranked, or excluded above
            out[pid] = {
                "recent_form_position": position,
                "recent_form_note": (
                    f"outside the ranked top {limit}" if limit
                    else "outside the ranked list"
                ),
            }
    return out


def build_top_available(
    available_players: list[dict],
    pro_schedule: dict[int, dict],
    injury_report: dict[str, dict],
    weeks_ahead: list[int],
    per_position: int = TOP_AVAILABLE_PER_POSITION,
    form_lookup: dict[int, dict] | None = None,
) -> dict:
    """The slice of the free-agent pool a report can actually use.

    available_players.json is ~850 players and ~800 KB, of which only the top of
    each position is ever a candidate. This is the same data, ranked by rostered
    percentage and trimmed, so a consumer does not have to read and sort the
    whole pool to answer "who is worth adding at RB".
    """
    byes = bye_week_map(pro_schedule)
    form_lookup = form_lookup or {}
    by_position: dict[str, list[dict]] = {}

    for player in available_players:
        position = player.get("position")
        if position not in RECENT_FORM_LIMITS:
            continue
        by_position.setdefault(position, []).append(player)

    out: dict[str, list[dict]] = {}
    for position in RECENT_FORM_LIMITS:
        ranked = sorted(
            by_position.get(position, []),
            key=lambda p: (-(p.get("percent_owned") or 0), p.get("name") or ""),
        )[:per_position]
        rows = []
        for index, player in enumerate(ranked, 1):
            badge = injury_badge(player["player_id"], injury_report)
            row = {
                "rank": index,
                "player_id": player["player_id"],
                "name": player["name"],
                "position": position,
                "pro_team": player["pro_team"],
                "availability_status": player.get("availability_status"),
                "acquisition": (
                    "FREE AGENT"
                    if player.get("availability_status") == "FREEAGENT"
                    else "WAIVERS"
                ),
                "waiver_process_date": player.get("waiver_process_date"),
                "waiver_process_ct": iso_to_central_label(
                    player.get("waiver_process_date")
                ),
                "percent_owned": player.get("percent_owned"),
                "percent_change": player.get("percent_change"),
                "bye_week": byes.get(player.get("pro_team_id")),
                "injury": badge,
                "upcoming": upcoming_games(
                    pro_schedule, player.get("pro_team_id"), weeks_ahead
                ),
            }
            form = form_lookup.get(player["player_id"])
            if form:
                row.update(form)
            else:
                row["recent_form_note"] = (
                    f"fewer than {RECENT_FORM_GAMES} games played"
                )
            rows.append({k: v for k, v in row.items() if v not in (None, [], {})})
        out[position] = rows
    return out


def build_gates(metadata: dict, extra: dict[str, Any]) -> dict:
    """Machine-readable pass/fail for every snapshot-level check.

    The report prompt asks for a DATA STATUS line, which previously meant
    re-deriving the verdict from several files. This makes it a lookup.
    """
    gates = [
        ("league_settings_retrieved", metadata.get("league_fetch_success"),
         "league settings, teams and status"),
        ("all_rosters_retrieved", metadata.get("roster_fetch_success"),
         f"{metadata.get('team_count')} of {metadata.get('expected_team_count')} teams"),
        ("team_count_complete", metadata.get("team_count_complete"), None),
        ("transactions_retrieved", metadata.get("transactions_fetch_success"), None),
        ("player_pool_retrieved", metadata.get("player_pool_fetch_success"), None),
        ("matchups_retrieved", metadata.get("matchup_fetch_success"), None),
        ("ownership_reconciled", metadata.get("ownership_reconciled"), None),
        ("player_index_complete", metadata.get("player_index_built"), None),
        ("recent_form_built", metadata.get("recent_form_success"),
         f"{metadata.get('recent_form_ranked_total')} ranked"),
        ("injury_report_retrieved", metadata.get("injury_report_success"),
         "supplementary; non-fatal"),
        ("nfl_schedule_retrieved", extra.get("schedule_ok"),
         f"{extra.get('schedule_teams')} NFL teams"),
        ("faab_arithmetic", extra.get("faab_ok"), extra.get("faab_detail")),
    ]
    blocks = [
        {
            "gate": name,
            "passed": bool(value),
            "detail": detail,
            "critical": name in CRITICAL_GATES,
        }
        for name, value, detail in gates
    ]
    failed_critical = [b["gate"] for b in blocks if b["critical"] and not b["passed"]]
    failed_other = [
        b["gate"] for b in blocks if not b["critical"] and not b["passed"]
    ]
    return {
        "usage": (
            "Pass/fail for every snapshot-level check, so DATA STATUS is a lookup "
            "rather than a derivation. data_status is PASS only when every "
            "critical gate passed. Non-critical gates failing degrade the report "
            "but do not fail it: say what is missing instead of omitting it "
            "silently. This covers snapshot integrity only; the report still owns "
            "its own ownership, acquisition-cost and PDF gates."
        ),
        "data_status": "PASS" if not failed_critical else "FAIL",
        "failed_critical": failed_critical,
        "failed_non_critical": failed_other,
        "snapshot_fetched_at_utc": metadata.get("fetched_at"),
        "snapshot_fetched_at_ct": metadata.get("fetched_at_ct"),
        "snapshot_fetched_at_ct_label": metadata.get("fetched_at_ct_label"),
        "scoring_period_id": metadata.get("scoringPeriodId"),
        "errors": metadata.get("errors") or [],
        "gates": blocks,
    }


# Gates that must pass for the snapshot to be usable at all. The injury feed and
# the NFL schedule are supplementary, and the recent-form step is explicitly
# non-critical, so none of them can fail the snapshot.
CRITICAL_GATES = {
    "league_settings_retrieved",
    "all_rosters_retrieved",
    "team_count_complete",
    "player_pool_retrieved",
    "ownership_reconciled",
    "player_index_complete",
    "faab_arithmetic",
}


# --------------------------------------------------------------------------
# Fetch steps
# --------------------------------------------------------------------------


def fetch_transactions(
    client: EspnClient, current_period: int
) -> tuple[list[dict], dict[str, int]]:
    """Collect the full transaction history by walking every scoring period.

    ``mTransactions2`` is scoped to a scoring period, and calling it without a
    ``scoringPeriodId`` returns only the *current* period. That is a quiet trap:
    in week 1 the call happened to include the draft and every early move, so it
    looked like a complete history, then the same call in week 2 returned four
    lineup tweaks. Walking the periods and merging by transaction id restores the
    whole log, including every waiver bid.

    Periods overlap in what they return, so dedupe by id is required.
    """
    merged: dict[str, dict] = {}
    per_period: dict[str, int] = {}

    # Period 0 mirrors the current period; 1..current covers everything so far.
    for period in [0] + list(range(1, max(current_period, 1) + 1)):
        try:
            payload = client.get(
                views="mTransactions2",
                params={"scoringPeriodId": period},
                label=f"transactions@{period}",
            )
        except FetchError as exc:
            print(f"  WARNING: transactions period {period}: {exc}", file=sys.stderr)
            continue
        rows = payload.get("transactions") or []
        per_period[str(period)] = len(rows)
        for row in rows:
            key = row.get("id") or (
                f"{row.get('type')}:{row.get('teamId')}:{row.get('proposedDate')}"
                f":{row.get('scoringPeriodId')}"
            )
            merged[key] = row

    return list(merged.values()), per_period


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
                    "waiver_process_ct": iso_to_central_label(
                        _ms_to_iso(wrapper.get("waiverProcessDate"))
                    ),
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

    def write(name: str, payload: Any, compact: bool = False) -> None:
        """Write a JSON file. ``compact`` drops indentation to keep large
        lookup tables small enough to read in one pass."""
        path = os.path.join(out_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            if compact:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=False)
            else:
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
    print("\n[1/10] league settings, teams, status")
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
            "faab": {
                "enabled": bool(
                    (settings.get("acquisitionSettings") or {}).get(
                        "isUsingAcquisitionBudget"
                    )
                ),
                "budget_per_team": faab_budget(settings.get("acquisitionSettings")),
                "minimum_bid": (settings.get("acquisitionSettings") or {}).get(
                    "minimumBid"
                ),
                "acquisition_type": (settings.get("acquisitionSettings") or {}).get(
                    "acquisitionType"
                ),
                "waiver_process_days": (settings.get("acquisitionSettings") or {}).get(
                    "waiverProcessDays"
                ),
                "waiver_process_hour": (settings.get("acquisitionSettings") or {}).get(
                    "waiverProcessHour"
                ),
                "note": (
                    "Remaining budget per team is in rosters.json under each team's "
                    "faab block, and in ownership.json under team_needs. Those "
                    "figures come from ESPN's own per-team ledger "
                    "(transactionCounter.acquisitionBudgetSpent), which is what the "
                    "ESPN UI displays and is the authoritative number to quote. "
                    "transactions.json also lists every waiver bid under "
                    "waiver_bids with a succeeded flag; do not sum those to derive "
                    "remaining budget. Failed claims (status FAILED_*) are real bids "
                    "that were never charged, and ESPN's ledger can legitimately "
                    "disagree with a naive sum of winning bids."
                ),
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

    # ---- 2. NFL schedule and bye weeks ----------------------------------
    print("\n[2/10] NFL schedule and bye weeks")
    pro_schedule, schedule_diag = fetch_pro_schedule(client, season)
    byes = bye_week_map(pro_schedule)
    steps["nfl_schedule_success"] = bool(schedule_diag.get("ok"))
    if schedule_diag.get("ok"):
        remaining = sorted(
            {w for w in byes.values() if isinstance(w, int) and w >= (scoring_period_id or 1)}
        )
        print(
            f"  {schedule_diag['teams']} NFL teams | {len(byes)} bye weeks mapped"
            f" | byes still to come: {remaining}"
        )
    else:
        message = f"NFL schedule unavailable: {schedule_diag.get('error')}"
        print(f"  WARNING: {message}", file=sys.stderr)
        errors.append(message)

    # The two scoring periods the report's lineup pressure test covers.
    weeks_ahead = [
        w for w in ((scoring_period_id or 1), (scoring_period_id or 1) + 1)
        if w <= (status.get("finalScoringPeriod") or 18)
    ]

    # ---- 3. NFL injury report (second host, supplementary) ---------------
    # Fetched before the rosters so every downstream structure can carry the
    # badge. Never fatal: the fantasy API already gives us injuryStatus, and
    # this only adds the expected return date and the beat note.
    print("\n[3/10] NFL injury report (expected return dates)")
    injury_report, injury_diag = fetch_injury_report()
    steps["injury_report_success"] = bool(injury_diag.get("ok") and injury_report)
    if injury_diag.get("ok"):
        print(
            f"  {len(injury_report)} players with a designation"
            f" | {injury_diag.get('on_nfl_ir', 0)} on NFL injured reserve"
            f" | {injury_diag['with_player_id']}/{injury_diag['records']} ids recovered"
        )
    else:
        message = f"NFL injury feed unavailable: {injury_diag.get('error')}"
        print(f"  WARNING: {message}", file=sys.stderr)
        errors.append(message)

    # ---- 2. Rosters -------------------------------------------------------
    print("\n[4/10] rosters for every team")
    rosters = build_rosters(
        teams,
        members_by_id,
        lineup_slot_counts,
        settings.get("acquisitionSettings"),
        injury_report=injury_report,
        bye_weeks=byes,
    )
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
    print("\n[5/10] transactions")
    teams_by_id = {r["team_id"]: r["team_name"] for r in rosters}
    transactions_payload: dict[str, Any]
    try:
        # NOTE: mTransactions2 returns 400 if an X-Fantasy-Filter is supplied, so
        # these requests intentionally send none. It is also period-scoped, hence
        # the walk across scoring periods.
        raw_transactions, per_period = fetch_transactions(client, scoring_period_id)
        write_raw("transactions_raw.json", {"transactions": raw_transactions})
        transactions_payload = normalize_transactions(raw_transactions, teams_by_id)
        transactions_payload["rows_per_scoring_period"] = per_period
        steps["transactions_fetch_success"] = len(raw_transactions) > 0
        counts = transactions_payload["counts"]
        print(
            f"  {counts['total']} total across {len(per_period)} periods | "
            f"{counts['executed_ownership_changes']} executed ownership changes | "
            f"{counts['waiver_bids']} waiver bids | {counts['draft_picks']} draft picks"
        )
        if not raw_transactions:
            errors.append("transaction history came back empty")
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
    print("\n[6/10] free agent + waiver pool (X-Fantasy-Filter)")
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
    print("\n[7/10] matchups / boxscore for current period")
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
    print("\n[8/10] ownership reconciliation")
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

    # ---- 7. Flat player index -------------------------------------------
    print("\n[9/10] player lookup index")
    player_index = build_player_index(
        rosters, available, injury_report=injury_report, bye_weeks=byes
    )
    player_index.update(
        {
            "league_id": args.league_id,
            "season": season,
            "scoring_period_id": scoring_period_id,
            "fetched_at": started_at.isoformat(),
        }
    )
    write("player_index.json", player_index, compact=True)
    index_counts = player_index["counts"]
    steps["player_index_built"] = index_counts["total"] == (
        ownership["counts"]["rostered"] + ownership["counts"]["available"]
    )
    print(
        f"  {index_counts['total']} players | {index_counts['unique_name_keys']} name keys "
        f"| {index_counts['collisions']} collision(s)"
    )
    if index_counts["collisions"]:
        for name, ids in player_index["collisions"].items():
            print(f"    collision: {name} -> {ids}")
    if not steps["player_index_built"]:
        message = (
            f"player index has {index_counts['total']} rows but ownership counted "
            f"{ownership['counts']['rostered'] + ownership['counts']['available']}"
        )
        print(f"  WARNING: {message}", file=sys.stderr)
        errors.append(message)

    # ---- 8. Recent form rankings (last 3 completed games) ----------------
    # No write_raw() here on purpose: 9-17 payloads at ~9 MB each would add
    # 80-160 MB to the workflow artifact for data this artifact already captures.
    print("\n[10/10] recent form rankings (last 3 completed games)")
    recent_form_ranked_total = 0
    recent_form_payload: dict | None = None
    try:
        weeks_considered = recent_form_weeks(status, scoring_period_id)
        if not weeks_considered:
            raise ValueError(
                "cannot determine the week range: latestScoringPeriod="
                f"{status.get('latestScoringPeriod')!r}, scoring_period_id="
                f"{scoring_period_id!r}"
            )
        scoring_items = scoring_items_by_stat_id(settings.get("scoringSettings"))
        if not scoring_items:
            message = "league scoringItems unavailable; appliedTotal cross-check skipped"
            print(f"  WARNING: {message}", file=sys.stderr)
            errors.append(message)

        weekly: dict[int, dict[int, dict]] = {}
        meta: dict[int, dict] = {}
        weeks_with_data: list[int] = []
        weeks_failed: list[int] = []
        totals = {
            "requests": 0,
            "invalid_values": 0,
            "not_played_excluded": 0,
            "zero_point_games_kept": 0,
        }

        for week in weeks_considered:
            try:
                actuals, week_meta, diag = fetch_weekly_actuals(
                    client, season, week, scoring_items
                )
            except FetchError as exc:
                # A week that fails mid-pagination is discarded whole: ranking a
                # week on half a pool would silently bias every average using it.
                print(f"  WARNING: recent form week {week}: {exc}", file=sys.stderr)
                errors.append(f"recent form week {week}: {exc}")
                weeks_failed.append(week)
                continue
            for key in totals:
                totals[key] += diag.get(key, 0)
            meta.update(week_meta)           # ascending weeks: latest wins
            for pid, row in actuals.items():
                weekly.setdefault(pid, {})[week] = row
            if actuals:
                weeks_with_data.append(week)

        payload = build_recent_form(
            weekly, meta, player_index["players_by_id"], weeks_considered,
            injury_report=injury_report,
        )
        payload["weeks"]["with_data"] = weeks_with_data
        payload["weeks"]["failed"] = weeks_failed
        payload["weeks"]["latest_scoring_period"] = status.get("latestScoringPeriod")
        payload["weeks"]["final_scoring_period"] = status.get("finalScoringPeriod")
        payload["diagnostics"]["espn_requests"] = totals["requests"]
        payload["diagnostics"]["page_size"] = RECENT_FORM_PAGE_SIZE
        payload["diagnostics"]["invalid_values"] = totals["invalid_values"]
        payload["diagnostics"]["not_played_excluded"] = totals["not_played_excluded"]
        payload["diagnostics"]["zero_point_games_kept"] = totals["zero_point_games_kept"]
        payload.update(
            {
                "league_id": args.league_id,
                "season": season,
                "scoring_period_id": scoring_period_id,
                # started_at, not now(): every other artifact in this run stamps
                # the run's start, and the tests use it to tell whether
                # metadata.json and this file came from the same run.
                "fetched_at": started_at.isoformat(),
            }
        )

        compact = len(json.dumps(payload, separators=(",", ":"))) > 300_000
        write("recent_form_rankings.json", payload, compact=compact)

        recent_form_payload = payload
        recent_form_ranked_total = payload["ranked_total"]
        rf_counts = payload["counts"]
        print("  " + " | ".join(
            f"{pos} {rf_counts[pos]['ranked']}/{rf_counts[pos]['limit']}"
            for pos in RECENT_FORM_LIMITS
        ))
        check = payload["scoring_crosscheck"]
        print(
            f"  {recent_form_ranked_total} ranked of {payload['expected_total']} expected"
            f" | weeks {weeks_considered[0]}-{weeks_considered[-1]}"
            f" | cross-check max delta {check['max_abs_delta']}"
            f" over {check['values_checked']} values"
        )
        own = payload["ownership"]
        print(
            f"  {own['is_jason_x_count']} Jason X player(s) flagged for highlighting"
            f" | {own['unknown']} owner(s) unresolved"
        )
        for pos, block in rf_counts.items():
            if not block["sufficient"]:
                message = (
                    f"recent form {pos}: only {block['ranked']} of {block['limit']} "
                    f"ranked; {block['qualifying']} player(s) have "
                    f"{RECENT_FORM_GAMES} completed games"
                )
                print(f"  NOTE: {message}")
                errors.append(message)
        if check["ok"] is False:
            message = (
                "appliedTotal cross-check exceeded tolerance: max delta "
                f"{check['max_abs_delta']} > {SCORING_CROSSCHECK_TOLERANCE} "
                f"({check['worst']})"
            )
            print(f"  WARNING: {message}", file=sys.stderr)
            errors.append(message)
        if not own["jason_x_name_id_agree"]:
            message = (
                f"Jason X roster by team id {JASON_X_TEAM_ID} has "
                f"{own['jason_x_by_id_count']} player(s) but by team name "
                f"{JASON_X_TEAM_NAME!r} has {own['jason_x_by_name_count']}; "
                f"team id {JASON_X_TEAM_ID} is now named "
                f"{own['jason_x_team_name_observed']!r}. Highlighting follows the id."
            )
            print(f"  WARNING: {message}", file=sys.stderr)
            errors.append(message)
        if recent_form_ranked_total > 0 and own["unknown"] == recent_form_ranked_total:
            message = (
                f"recent form ownership unresolved for all {recent_form_ranked_total} "
                "ranked players; no Jason X highlights will render"
            )
            print(f"  WARNING: {message}", file=sys.stderr)
            errors.append(message)

        steps["recent_form_success"] = bool(
            recent_form_ranked_total > 0 and not weeks_failed
        )
    except Exception as exc:  # non-critical step: never take the pipeline down
        print(f"  WARNING: recent form rankings failed: {exc!r}", file=sys.stderr)
        errors.append(f"recent form rankings failed: {exc}")
        steps["recent_form_success"] = False

    # ---- NFL schedule file -----------------------------------------------
    if pro_schedule:
        write(
            "nfl_schedule.json",
            {
                "usage": (
                    "Every NFL team's bye week and per-scoring-period opponent, "
                    "keyed by ESPN pro team id. Use this instead of web research "
                    "for byes and opponents; it is the same snapshot as the "
                    "rosters. A scoring period absent from a team's "
                    "games_by_scoring_period is that team's bye, which is why "
                    "bye_week and the gap always agree. Every player record in "
                    "rosters.json, player_index.json and recent_form_rankings.json "
                    "already carries bye_week inline, so only the opponent lookup "
                    "needs this file. kickoff_ct is the kickoff in US Central."
                ),
                "league_id": args.league_id,
                "season": season,
                "scoring_period_id": scoring_period_id,
                "fetched_at": started_at.isoformat(),
                "source": schedule_diag,
                "weeks_ahead": weeks_ahead,
                "bye_weeks_by_abbrev": {
                    rec["abbrev"]: rec["bye_week"]
                    for rec in pro_schedule.values()
                    if rec.get("abbrev") and isinstance(rec.get("bye_week"), int)
                    and rec["bye_week"] > 0
                },
                "byes_by_scoring_period": {
                    str(week): sorted(
                        rec["abbrev"] for rec in pro_schedule.values()
                        if rec.get("bye_week") == week and rec.get("abbrev")
                    )
                    for week in sorted(
                        {
                            rec["bye_week"] for rec in pro_schedule.values()
                            if isinstance(rec.get("bye_week"), int)
                            and rec["bye_week"] > 0
                        }
                    )
                },
                "teams": {str(k): v for k, v in sorted(pro_schedule.items())},
            },
        )

    # ---- injury report file ----------------------------------------------
    # Written after the rosters so ownership can be attached. The badge is
    # already inline on every player everywhere; this file is the one place the
    # full detail lives, plus the two IR cross-checks worth acting on.
    index_by_id = player_index["players_by_id"]
    league_injuries = {}
    for pid, record in injury_report.items():
        owner = index_by_id.get(pid)
        if not owner:
            continue            # not in this league's player pool
        enriched = dict(record)
        enriched["fantasy_status"] = owner.get("fantasy_status")
        enriched["fantasy_team"] = owner.get("fantasy_team")
        enriched["fantasy_team_id"] = owner.get("fantasy_team_id")
        enriched["lineup_slot"] = owner.get("lineup_slot")
        enriched["in_fantasy_ir_slot"] = bool(owner.get("in_fantasy_ir_slot"))
        enriched["is_jason_x"] = owner.get("fantasy_team_id") == JASON_X_TEAM_ID
        league_injuries[pid] = enriched

    on_ir = {k: v for k, v in league_injuries.items() if v.get("on_nfl_ir")}
    ir_not_in_slot = sorted(
        (v["name"], v.get("fantasy_team"), v.get("expected_return"))
        for v in on_ir.values()
        if v.get("fantasy_status") == "ROSTERED" and not v["in_fantasy_ir_slot"]
    )
    slot_not_on_ir = sorted(
        (p["name"], team["team_name"])
        for team in rosters for p in team["injured_reserve"]
        if not p.get("on_nfl_ir")
    )

    write(
        "injuries.json",
        {
            "usage": (
                "NFL injury designations and expected return dates for every "
                "player in this league's pool, keyed by ESPN player id. Joined "
                "by player id, never by name. A compact subset of each record "
                "is already attached inline as the player's `injury` block in "
                "rosters.json, player_index.json and recent_form_rankings.json, "
                "so a player's injury travels with them everywhere they appear; "
                "this file carries the full detail including the beat note. "
                "on_nfl_ir means the NFL team placed the player on injured "
                "reserve. in_fantasy_ir_slot means this league's manager parked "
                "them in the fantasy IR slot. The two are different and "
                "routinely disagree: ir_on_nfl_but_active_slot lists players "
                "burning an active roster spot while on NFL IR, and "
                "ir_slot_but_not_on_nfl_ir lists fantasy IR slots holding "
                "someone the NFL has not designated. expected_return is ESPN's "
                "estimate; a date in February means out for the season, and "
                "expected_return_label says so in words. A designation of IR-R "
                "means designated to return."
            ),
            "league_id": args.league_id,
            "season": season,
            "scoring_period_id": scoring_period_id,
            "fetched_at": started_at.isoformat(),
            "source": injury_diag,
            "counts": {
                "in_league_pool": len(league_injuries),
                "on_nfl_ir": len(on_ir),
                "rostered_on_nfl_ir": sum(
                    1 for v in on_ir.values() if v.get("fantasy_status") == "ROSTERED"
                ),
                "jason_x_affected": sum(
                    1 for v in league_injuries.values() if v.get("is_jason_x")
                ),
            },
            "ir_on_nfl_but_active_slot": [
                {"name": n, "fantasy_team": t, "expected_return": r}
                for n, t, r in ir_not_in_slot
            ],
            "ir_slot_but_not_on_nfl_ir": [
                {"name": n, "fantasy_team": t} for n, t in slot_not_on_ir
            ],
            "players": league_injuries,
        },
    )
    print("\n[injuries] league-pool injury detail")
    print(
        f"  {len(league_injuries)} designated | {len(on_ir)} on NFL IR | "
        f"{len(ir_not_in_slot)} on NFL IR holding an active slot | "
        f"{len(slot_not_on_ir)} IR slot(s) without an NFL IR designation"
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
        player_index_count=index_counts["total"],
        recent_form_ranked_total=recent_form_ranked_total,
    )
    write("metadata.json", metadata)

    # ---- derived report inputs -------------------------------------------
    # Written last because they consolidate everything above. Each one removes a
    # derivation the report prompt previously had to perform itself.
    print("\n[derived] report inputs")

    jason_block = build_jason_x_block(
        rosters, lineup_slot_counts, pro_schedule, weeks_ahead
    )
    if jason_block:
        jason_block.update(
            {
                "usage": (
                    "Jason X's team, precomputed from this snapshot so the report "
                    "does not have to rebuild it. starters, bench and "
                    "injured_reserve are the current lineup; open_starting_slots "
                    "and capacity give the roster-spot answer directly, and "
                    "has_open_bench_spot is the flag that decides whether an add "
                    "needs a drop. bye_pressure lists which starters are on bye "
                    "in each of weeks_ahead. injury_designations is every "
                    "designated player on the roster with the expected return. "
                    "Every player carries bye_week and their upcoming opponents."
                ),
                "league_id": args.league_id,
                "season": season,
                "scoring_period_id": scoring_period_id,
                "fetched_at": started_at.isoformat(),
                "fetched_at_ct_label": to_central_label(started_at),
            }
        )
        write("jason_x.json", jason_block)
        print(
            f"  jason_x.json: {jason_block['counts']['total']} players | "
            f"bench open {jason_block['capacity']['bench_open']} | "
            f"IR open {jason_block['capacity']['ir_open']} | "
            f"FAAB ${(jason_block.get('faab') or {}).get('remaining')} | "
            f"{len(jason_block['injury_designations'])} designated"
        )

    form_lookup = recent_form_lookup(recent_form_payload)
    top_available = build_top_available(
        available, pro_schedule, injury_report, weeks_ahead,
        form_lookup=form_lookup,
    )
    write(
        "top_available.json",
        {
            "usage": (
                f"The top {TOP_AVAILABLE_PER_POSITION} available players per "
                "position by rostered percentage, with acquisition cost, bye "
                "week, injury badge and upcoming opponents already attached. "
                "This is a trimmed view of available_players.json for when a "
                "report needs candidates rather than the whole ~850-player pool. "
                "Availability still comes from the same snapshot, and "
                "player_index.json remains the authority on ownership. acquisition "
                "is FREE AGENT ($0, instant) or WAIVERS (needs a FAAB bid, "
                "processing at waiver_process_ct). recent_form_rank and "
                "recent_form_avg are this player's standing in "
                "recent_form_rankings.json, attached here so a report does not "
                "have to cross-reference. When those are absent, "
                "recent_form_note says why: either outside the ranked top N for "
                "the position, or fewer than three confirmed appearances. "
                "Players ESPN expects back only next season are excluded from "
                "the rankings entirely, so they carry the fewer-than-three note."
            ),
            "league_id": args.league_id,
            "season": season,
            "scoring_period_id": scoring_period_id,
            "fetched_at": started_at.isoformat(),
            "per_position": TOP_AVAILABLE_PER_POSITION,
            "counts": {pos: len(rows) for pos, rows in top_available.items()},
            "free_agents_in_pool": sum(
                1 for p in available if p.get("availability_status") == "FREEAGENT"
            ),
            "waivers_in_pool": sum(
                1 for p in available if p.get("availability_status") == "WAIVERS"
            ),
            "players_by_position": top_available,
        },
    )
    print(
        "  top_available.json: "
        + " ".join(f"{pos} {len(rows)}" for pos, rows in top_available.items())
    )

    faab_ok = True
    faab_detail = "faab disabled"
    league_budget = faab_budget(settings.get("acquisitionSettings"))
    if league_budget is not None:
        mismatched = [
            t["team_name"] for t in rosters
            if (t.get("faab") or {}).get("remaining")
            != league_budget - ((t.get("faab") or {}).get("spent") or 0)
        ]
        faab_ok = not mismatched
        faab_detail = (
            f"all {len(rosters)} teams reconcile against a ${league_budget} budget"
            if faab_ok else f"mismatched: {mismatched}"
        )

    gates = build_gates(
        metadata,
        {
            "schedule_ok": schedule_diag.get("ok"),
            "schedule_teams": schedule_diag.get("teams"),
            "faab_ok": faab_ok,
            "faab_detail": faab_detail,
        },
    )
    write("gates.json", gates)
    print(
        f"  gates.json: DATA STATUS {gates['data_status']}"
        + (f" | failed: {gates['failed_critical']}" if gates["failed_critical"] else "")
        + (
            f" | degraded: {gates['failed_non_critical']}"
            if gates["failed_non_critical"] else ""
        )
    )

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
    player_index_count: int = 0,
    recent_form_ranked_total: int = 0,
) -> dict:
    finished_at = datetime.now(timezone.utc)
    all_ok = all(steps.get(k, False) for k in ("league_fetch_success", "roster_fetch_success"))
    return {
        "fetched_at": finished_at.isoformat(),
        "fetched_at_ct": to_central_iso(finished_at),
        "fetched_at_ct_label": to_central_label(finished_at),
        "timezone_source": central_timezone_source(),
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
        "player_index_count": player_index_count,
        "player_index_built": steps.get("player_index_built", False),
        "recent_form_success": steps.get("recent_form_success", False),
        "recent_form_ranked_total": recent_form_ranked_total,
        "injury_report_success": steps.get("injury_report_success", False),
        "nfl_schedule_success": steps.get("nfl_schedule_success", False),
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
