"""Plays the game's requests against a running service: python tests/smoke_test.py [base_url]"""
import json, sys, urllib.request
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")

def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode())
    if token: req.add_header("MGI-Bearer-Token", token)
    with urllib.request.urlopen(req) as r:
        assert r.status == 200, (path, r.status)
        return int(r.headers["ACTUAL-STATUS-CODE"]), json.loads(r.read() or b"{}")

ok = 0
def check(cond, what):
    global ok
    assert cond, what
    ok += 1

y = "/n2020/"
c, r = call("POST", y + "user", {"platform": "stm"}); check(c == 400, "login without sn_id refused")
c, a = call("POST", y + "user?sn_id=76561190000000001&sn_name=Alpha", {"platform": "stm"}); check(c == 200 and a["mgi_token"], "login A")
c, b = call("POST", y + "user?sn_id=76561190000000002&sn_name=Bravo", {"platform": "stm"}); check(c == 200, "login B")
ta, tb = a["mgi_token"], b["mgi_token"]
lap = "best_laptime._LAPTIME_.VGFsbGFkZWdh.CUP.aids-none"
xp = "races_completed._XP_.none.none.none"
c, _ = call("POST", y + "leaderboard", {"posts": [{"name": lap, "val": 49.5}]}); check(c == 403, "post without token -> 403")
call("POST", y + "leaderboard", {"posts": [{"name": lap, "val": 49.5}, {"name": xp, "val": 1}]}, ta)
call("POST", y + "leaderboard", {"posts": [{"name": lap, "val": 48.9}, {"name": xp, "val": 1}]}, ta)
call("POST", y + "leaderboard", {"posts": [{"name": lap, "val": 49.1}, {"name": xp, "val": 1}, {"name": lap, "val": 0.1}]}, tb)
c, r = call("GET", y + "leaderboard/" + lap + "/alltime?start_at=0&count=10")
check([(e["display_name"], e["val"]) for e in r["entries"]] == [("Alpha", 48.9), ("Bravo", 49.1)], "laptime board ascending, best per player, junk dropped")
c, r = call("GET", y + "leaderboard/" + xp + "/daily?start_at=0&count=10")
check([(e["display_name"], e["val"]) for e in r["entries"]] == [("Alpha", 2), ("Bravo", 1)], "races board summed")
c, r = call("GET", "/n2022/leaderboard/" + lap + "/weekly?start_at=0&count=10"); check(r["entries"] == [], "2022 boards separate")
c, r = call("GET", y + "stats?category=x", token=ta); check(r["total_races"] == 3, "stats race count")
call("POST", y + "steamnet/heartbeat", {"steam_id": "76561190000000001", "state": "racing", "game_id": "g1"}, ta)
call("POST", "/heartbeat", {"steam_id": "76561190000000002", "state": "lobby", "game_id": "g1"})
c, r = call("GET", "/stats"); check(r["active_players"] == 2 and r["active_lobbies"] == 1, "presence counted")
c, r = call("GET", y + "challenge/list?limit=6&published=yes&full=yes", token=ta); check(c == 200 and "challenges" in r, "challenge list")
c, r = call("GET", y + "challenge/leaderboard/NORMAL", token=ta); check(c == 200 and r["Leaderboard"] == [], "challenge leaderboard")
c, r = call("GET", y + "tournament/event_info/release-CUP/unified", token=ta); check(r == {"EventInfo": None}, "no tournament")
c, r = call("GET", y + "newsfeed/list", token=ta); check(r["version"][0]["Id"] == 1, "news list")
c, r = call("GET", y + "newsfeed/1", token=ta); check(r["post"]["Title"], "news item")
c, r = call("GET", y + "game/whatever", token=ta); check(c == 404, "unknown route 404 in header")
print(f"{ok} checks passed")
