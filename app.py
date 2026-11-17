"""NH5 services: the online menu content for NASCAR Heat 5 SteamNet games (leaderboards, World Challenges, eSports, news,
Online Now), plus the presence heartbeat. Lobbies, races and voice run over Steam (SteamNet) and never come here.

Game setting (H5TrackLoader/steamnet/steamnet_settings.txt):
  services_url      = https://<your-app>.up.railway.app/n2020/
  services_url_2022 = https://<your-app>.up.railway.app/n2022/
  services_routes   = challenge,tournament,leaderboard,newsfeed,stats,analytics
  presence_url      =            (empty: heartbeats go to services_url + steamnet/heartbeat)

Every reply is HTTP 200 with the real status in the ACTUAL-STATUS-CODE header (Heat 5 treats any other HTTP status as a
network failure). JSON shapes follow WebAPI.Responses.* in the game's Assembly-CSharp; enums are ints.

Env: PORT (Railway sets it), DATA_DIR (put a Railway volume here; default ./data), CONTENT_DIR (default ./content).
Run:  pip install -r requirements.txt && python app.py
"""
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from aiohttp import web

PORT = int(os.getenv("PORT") or "8000")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR") or os.path.join(HERE, "data")
CONTENT_DIR = os.getenv("CONTENT_DIR") or os.path.join(HERE, "content")
YEARS = ("n2020", "n2022")
ACTIVE_SECONDS = 90
MIN_LAP_SECONDS = 5.0  # sanity floor for posted lap times

os.makedirs(DATA_DIR, exist_ok=True)
db = sqlite3.connect(os.path.join(DATA_DIR, "nh5.db"))
db.executescript("""
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, steam_id TEXT, name TEXT, t REAL);
CREATE TABLE IF NOT EXISTS posts (year TEXT, board TEXT, steam_id TEXT, name TEXT, val REAL, t REAL);
CREATE INDEX IF NOT EXISTS posts_board ON posts (year, board, t);
CREATE TABLE IF NOT EXISTS challenge_scores (year TEXT, challenge_id INTEGER, steam_id TEXT, name TEXT, score REAL, t REAL);
CREATE INDEX IF NOT EXISTS cs_id ON challenge_scores (year, challenge_id);
""")
db.commit()

# steam_id -> last heartbeat {"t", "state", "game_id", ...} (in memory; it's only about the last 90 s)
presence = {}


# ---------------------------------------------------------------- helpers
def reply(data=None, code=200):
    body = json.dumps({} if data is None else data, separators=(",", ":"))
    return web.Response(text=body, content_type="application/json", headers={"ACTUAL-STATUS-CODE": str(code)})


def error(code, msg):
    return reply({"error": msg}, code)


def content(name, default):
    """content/<name>.json, re-read on every request so edits apply without a restart."""
    try:
        with open(os.path.join(CONTENT_DIR, name + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def now():
    return time.time()


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def window_start(kind):
    t = datetime.now(timezone.utc)
    if kind == "daily":
        return t.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if kind == "weekly":
        d = t.replace(hour=0, minute=0, second=0, microsecond=0)
        return (d - timedelta(days=d.weekday())).timestamp()
    return 0.0  # alltime


def user_of(request):
    tok = request.headers.get("MGI-Bearer-Token", "")
    row = db.execute("SELECT steam_id, name FROM sessions WHERE token = ?", (tok,)).fetchone() if tok else None
    return row  # None -> 403 makes the game log in again


async def body_json(request):
    try:
        txt = await request.text()
        return json.loads(txt) if txt.strip() else {}
    except Exception:
        return None


def entry(steam_id, name, val):
    return {"platform_user_id": steam_id, "display_name": name, "val": val, "is_verified_user": False}


# ---------------------------------------------------------------- login
async def login(request):
    """POST user. SteamNet adds ?sn_id=<steam id>&sn_name=<persona name> (the Steam ticket isn't checked here)."""
    sid = request.query.get("sn_id", "")
    name = request.query.get("sn_name", "")[:32] or "Player"
    if not sid.isdigit() or len(sid) > 20:
        return error(400, "sn_id missing: update the SteamNet loader")
    tok = secrets.token_urlsafe(24)
    db.execute("DELETE FROM sessions WHERE steam_id = ?", (sid,))
    db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (tok, sid, name, now()))
    db.commit()
    return reply({"session_id": tok[:12], "mgi_token": tok})


# ---------------------------------------------------------------- leaderboards
def board_order(board):
    """(aggregate, descending) for a leaderboard name, as the game names them."""
    if "._LAPTIME_." in board:
        return "MIN", False
    if "._XP_." in board:
        return "SUM", True
    return "MAX", True


def board_entries(year, board, kind, start_at=0, count=10):
    agg, desc = board_order(board)
    rows = db.execute(
        f"SELECT steam_id, MAX(name), {agg}(val) AS v FROM posts WHERE year = ? AND board = ? AND t >= ? "
        f"GROUP BY steam_id ORDER BY v {'DESC' if desc else 'ASC'}, MIN(t) LIMIT ? OFFSET ?",
        (year, board, window_start(kind), max(1, min(count, 100)), max(0, start_at))).fetchall()
    return [entry(s, n, v) for s, n, v in rows]


async def leaderboard_get(request):
    y = request.match_info["year"]
    try:
        start_at = int(request.query.get("start_at", "0"))
        count = int(request.query.get("count", "10"))
    except ValueError:
        return error(400, "bad paging")
    kind = request.match_info["kind"]
    return reply({"entries": board_entries(y, request.match_info["board"], kind, start_at, count)})


async def leaderboard_post(request):
    u = user_of(request)
    if u is None:
        return error(403, "log in again")
    data = await body_json(request)
    if not isinstance(data, dict):
        return error(400, "bad json")
    y = request.match_info["year"]
    t = now()
    for p in data.get("posts") or []:
        try:
            board = str(p["name"])[:200]
            val = float(p["val"])
        except (KeyError, TypeError, ValueError):
            continue
        if "._LAPTIME_." in board and val < MIN_LAP_SECONDS:
            continue
        if "._XP_." in board and not (0 < val <= 10):
            continue
        db.execute("INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?)", (y, board, u[0], u[1], val, t))
    db.commit()
    return reply({"entries": []})


async def leaderboard_advance(request):
    return reply({})  # windows are computed from timestamps; nothing to roll over


# ---------------------------------------------------------------- World Challenges (content/challenges.json)
def challenges(year):
    """Live challenges for this year: content entries with "year" (default both) between StartTime and EndTime."""
    out = []
    t = datetime.now(timezone.utc)
    for c in content("challenges", []):
        if c.get("year", year) != year or not c.get("IsPublished", True):
            continue
        try:
            start, end = parse_time(c["StartTime"]), parse_time(c["EndTime"])
        except (KeyError, ValueError):
            continue
        if start <= t < end:
            cc = {k: c[k] for k in ("ID", "FriendlyName", "StartTime", "EndTime", "AssistsLevel") if k in c}
            sd = c.get("ScenarioData", "")
            cc["ScenarioData"] = sd if isinstance(sd, str) else json.dumps(sd)
            cc["IsPublished"] = True
            cc["MinutesRemaining"] = int((end - t).total_seconds() // 60)
            out.append(cc)
    return out


async def challenge_list(request):
    return reply({"challenges": challenges(request.match_info["year"])[:6]})


def challenge_board(year, cid, me):
    rows = db.execute(
        "SELECT steam_id, MAX(name), MAX(score) AS s FROM challenge_scores WHERE year = ? AND challenge_id = ? "
        "GROUP BY steam_id ORDER BY s DESC, MIN(t)", (year, cid)).fetchall()
    board = [entry(s, n, v) for s, n, v in rows]
    rank, score = 0, 0.0
    for i, (s, n, v) in enumerate(rows):
        if s == me:
            rank, score = i + 1, v
    return board[:10], rank, score


async def challenge_leaderboard(request):
    y = request.match_info["year"]
    assists = request.match_info["assists"]
    u = user_of(request)
    me = u[0] if u else ""
    live = [c for c in challenges(y) if c.get("AssistsLevel") == assists]
    past = [c for c in content("challenges", []) if c.get("AssistsLevel") == assists and c.get("year", y) == y
            and "EndTime" in c and parse_time(c["EndTime"]) <= datetime.now(timezone.utc)]
    past.sort(key=lambda c: c["EndTime"])
    r = {"Leaderboard": [], "PlayerRank": 0, "PlayerScore": 0.0, "ChallengeName": "",
         "PastLeaderboard": [], "PastPlayerRank": 0, "PastPlayerScore": 0.0, "PastChallengeName": "",
         "CompletedEventsPerDifficulty": [0, 0, 0]}
    if live:
        r["Leaderboard"], r["PlayerRank"], r["PlayerScore"] = challenge_board(y, live[0]["ID"], me)
        r["ChallengeName"] = live[0].get("FriendlyName", "")
    if past:
        r["PastLeaderboard"], r["PastPlayerRank"], r["PastPlayerScore"] = challenge_board(y, past[-1]["ID"], me)
        r["PastChallengeName"] = past[-1].get("FriendlyName", "")
    if me:
        levels = ["NORMAL", "HARD", "EXPERT"]  # VSDrivingAids.Preset names, as in challenge/leaderboard/{assists}
        for lvl_i, lvl in enumerate(levels):
            ids = [c["ID"] for c in content("challenges", []) if c.get("AssistsLevel") == lvl and "ID" in c]
            if ids:
                q = "SELECT COUNT(DISTINCT challenge_id) FROM challenge_scores WHERE year = ? AND steam_id = ? AND challenge_id IN (%s)" % ",".join("?" * len(ids))
                r["CompletedEventsPerDifficulty"][lvl_i] = db.execute(q, (y, me, *ids)).fetchone()[0]
    return reply(r)


async def challenge_completed(request):
    u = user_of(request)
    if u is None:
        return error(403, "log in again")
    data = await body_json(request)
    y = request.match_info["year"]
    try:
        cid = int(request.match_info["cid"])
        score = float((data or {}).get("score"))
    except (TypeError, ValueError):
        return error(400, "bad score")
    if not any(c.get("ID") == cid for c in challenges(y)):
        return error(404, "challenge not live")
    db.execute("INSERT INTO challenge_scores VALUES (?, ?, ?, ?, ?, ?)", (y, cid, u[0], u[1], score, now()))
    db.commit()
    return reply({})


# ---------------------------------------------------------------- eSports (content/tournaments.json)
# TRNEventState: 0 Init, 1 RegularSeason, 2 PostRegularSeason, 3 WaitingForFinalSeason, 4 FinalSeason, 5 PostFinalSeason, 6 Complete
# TRNRaceStartPermission: 0 None, 1 Regular.  TournamentClass: 0..3 Final_A..D, 4 RegularSeason
def tournament(year, adv):
    t = datetime.now(timezone.utc)
    for e in content("tournaments", []):
        if e.get("year", year) != year or e.get("adv", adv) != adv:
            continue
        try:
            start, end = parse_time(e["StartTime"]), parse_time(e["EndTime"])
        except (KeyError, ValueError):
            continue
        if start <= t < end:
            return e, int((end - t).total_seconds() // 60)
    return None, 0


async def tournament_event_info(request):
    y, adv = request.match_info["year"], request.match_info["adv"]
    e, mins = tournament(y, adv)
    if e is None:
        return reply({"EventInfo": None})  # "no event": the eSports tab shows it as inactive
    cfg = {k: str(e.get(k, "")) for k in ("EventId", "RaceLength", "WearFactor", "DraftEffect", "LapCount", "TrackName", "League")}
    board = "trn." + cfg["EventId"] + "._LAPTIME_.race"
    return reply({
        "EventInfo": {"RaceStartPermission": 1, "State": 1, "MinutesRemainingInState": mins, "Config": cfg,
                      "ClassBrackets": [{"PlayerCount": 0}]},
        "RegularSeasonRank": None, "FinalRank": None, "LeaderboardClass": 4,
        "Leaderboard": board_entries(y, board, "alltime", 0, 10),
        "CompletedEventsPerClass": [0, 0, 0, 0, 0], "WorstResultInLeaderboard": None,
        "PopupMessage": e.get("PopupMessage", ""),
    })


async def tournament_history(request):
    return reply({"PastEvents": []})


# ---------------------------------------------------------------- news (content/news.json)
async def newsfeed_list(request):
    items = content("news", [])
    return reply({"is_store_enabled": False, "is_ps4_store_enabled": False,
                  "version": [{"Id": n["Id"], "Version": n.get("Version", 1)} for n in items if "Id" in n]})


async def newsfeed_item(request):
    try:
        nid = int(request.match_info["nid"])
    except ValueError:
        return error(404, "no such post")
    for n in content("news", []):
        if n.get("Id") == nid:
            post = {"Id": nid, "Version": n.get("Version", 1), "Order": n.get("Order", nid), "Title": n.get("Title", ""),
                    "Brief": n.get("Brief", ""), "Text": n.get("Text", ""), "TextAttribution": n.get("TextAttribution", ""),
                    "TextSource": n.get("TextSource", ""), "ImageData": n.get("ImageData"),  # base64 PNG/JPG or null
                    "ImageAttribution": n.get("ImageAttribution", ""), "Category": n.get("Category", "")}
            return reply({"post": post})
    return error(404, "no such post")


# ---------------------------------------------------------------- presence + Online Now
def active():
    t = now()
    players, lobbies = 0, set()
    for sid, p in list(presence.items()):
        if t - p["t"] > ACTIVE_SECONDS * 4:
            del presence[sid]
            continue
        if t - p["t"] > ACTIVE_SECONDS or p["state"] == "offline":
            continue
        players += 1
        if p.get("game_id"):
            lobbies.add(p["game_id"])
    return players, len(lobbies)


async def heartbeat(request):
    data = await body_json(request)
    sid = str((data or {}).get("steam_id", ""))
    if not sid.isdigit() or len(sid) > 20:
        return error(400, "bad steam_id")
    presence[sid] = {"t": now(), "state": str(data.get("state", "menu"))[:20], "game_id": str(data.get("game_id", ""))[:40],
                     "name": str(data.get("name", ""))[:32], "track": str(data.get("track", ""))[:60],
                     "private": bool(data.get("private", False))}
    return reply({})


async def stats(request):
    players, lobbies = active()
    day = now() - 86400
    races = "SELECT COUNT(*) FROM posts WHERE board = 'races_completed._XP_.none.none.none'"
    return reply({"active_players": players, "active_lobbies": lobbies,
                  "races_in_last_24hours": db.execute(races + " AND t >= ?", (day,)).fetchone()[0],
                  "total_races": db.execute(races).fetchone()[0]})


async def analytics(request):
    return reply({})


async def not_implemented(request):
    return error(404, "Endpoint not implemented")


async def index(request):
    players, lobbies = active()
    return web.Response(text=f"NH5 services up. {players} players, {lobbies} lobbies.\n")


# ---------------------------------------------------------------- app
def make_app():
    app = web.Application(client_max_size=1 << 20)
    r = app.router
    r.add_get("/", index)
    r.add_get("/stats", stats)           # for a Discord bot (same shape as before, plus race counts)
    r.add_post("/heartbeat", heartbeat)  # presence_url can point here too
    y = "/{year:n2020|n2022}/"
    r.add_post(y + "user", login)
    r.add_post(y + "steamnet/heartbeat", heartbeat)
    r.add_get(y + "stats", stats)
    r.add_get(y + "leaderboard/{board}/{kind:daily|weekly|alltime}", leaderboard_get)
    r.add_post(y + "leaderboard", leaderboard_post)
    r.add_post(y + "leaderboard/advance_time", leaderboard_advance)
    r.add_get(y + "challenge/list", challenge_list)
    r.add_get(y + "challenge/leaderboard/{assists}", challenge_leaderboard)
    r.add_post(y + "challenge/completed/{cid}", challenge_completed)
    r.add_get(y + "tournament/event_info/{adv}/{subgroup}", tournament_event_info)
    r.add_get(y + "tournament/history/{adv}/{subgroup}", tournament_history)
    r.add_get(y + "newsfeed/list", newsfeed_list)
    r.add_get(y + "newsfeed/{nid}", newsfeed_item)
    r.add_post(y + "analytics/{what}", analytics)
    r.add_route("*", y + "{tail:.*}", not_implemented)
    return app


if __name__ == "__main__":
    print(f"NH5 services on port {PORT} (data {DATA_DIR}, content {CONTENT_DIR})")
    web.run_app(make_app(), host="0.0.0.0", port=PORT, print=None)
