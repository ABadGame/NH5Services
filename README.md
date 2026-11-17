# NH5 services

The online menu content for NASCAR Heat 5 with the SteamNet loader: **leaderboards, World Challenges, eSports, news and
Online Now**, plus the player-count heartbeat for a Discord bot. Lobbies, races and voice run over Steam and never touch this
server. One small Python app (aiohttp + SQLite), made to run on Railway.

## Run it locally
```
pip install -r requirements.txt
python app.py                      # http://127.0.0.1:8000
python tests/smoke_test.py         # in a second window: plays the game's requests, should print "15 checks passed"
```

## Deploy on Railway
1. Push this folder to a GitHub repo, then in Railway: New Project → Deploy from GitHub repo. It starts `python app.py`
   (Procfile) and Railway sets `PORT`.
2. **Add a volume** (service → Settings → Volumes), mount path `/data`, and set the variable `DATA_DIR=/data`. Without it
   every redeploy wipes the leaderboards.
3. Settings → Networking → Generate Domain. Open `https://<name>.up.railway.app/`: it should say "NH5 services up".

## Point the game at it
In `H5TrackLoader/steamnet/steamnet_settings.txt`:
```
services_url      = https://<name>.up.railway.app/n2020/
services_url_2022 = https://<name>.up.railway.app/n2022/
services_routes   = challenge,tournament,leaderboard,newsfeed,stats,analytics
presence_url      =
```
`presence_url` empty sends the heartbeats here (`/n2020/steamnet/heartbeat`). The Discord bot can then read
`https://<name>.up.railway.app/stats` (`active_players`, `active_lobbies`, `races_in_last_24hours`, `total_races`) instead of
the old server. Needs SteamNet loader 6e4c1f59 or newer (it sends the Steam ID and name with the login).

## What it serves
| Game route | Here |
|---|---|
| `POST user?sn_id=&sn_name=` | login, gives the token the other routes use (a 403 makes the game log in again) |
| `GET leaderboard/{board}/{daily\|weekly\|alltime}` | lap boards (`._LAPTIME_.`): best lap per player, fastest first; race counts (`._XP_.`): summed |
| `POST leaderboard` | the game posts these after every finished race |
| `GET challenge/list`, `challenge/leaderboard/{NORMAL\|HARD\|EXPERT}`, `POST challenge/completed/{id}` | World Challenges from `content/challenges.json`, scores stored here |
| `GET tournament/event_info/...`, `tournament/history/...` | eSports from `content/tournaments.json` (none = the tab shows inactive) |
| `GET newsfeed/list`, `newsfeed/{id}` | Home tab news from `content/news.json` |
| `GET stats` | Online Now: players and lobbies heard in the last 90 s |
| anything else | 404 "Endpoint not implemented" |

Every reply is HTTP 200 with the real status in the `ACTUAL-STATUS-CODE` header: Heat 5 counts any other HTTP status as a
network failure. 2020 and 2022 (`/n2020/`, `/n2022/`) keep separate boards.

## Content files (edit, push, redeploy; they're re-read on every request)
- `content/news.json`: list of posts `{Id, Version, Order, Title, Brief, Text, Category, ImageData}`. Bump `Version` when you
  change a post so games fetch it again. `ImageData` = a base64 image, or null.
- `content/tournaments.json`: list of events `{EventId, StartTime, EndTime, adv, RaceLength, WearFactor, DraftEffect, LapCount,
  TrackName, League, PopupMessage}`; `adv` is the series key the game asks for (e.g. `release-CUP`), times are ISO UTC
  (`2026-12-01T00:00:00Z`). Optional `"year": "n2022"` limits an entry to one season (same for challenges).
- `content/challenges.json`: list of `{ID, FriendlyName, StartTime, EndTime, AssistsLevel, ScenarioData}`. `ScenarioData` is the
  game's own serialized scenario (`MGIScenario.SerializedData`: track, laps, cars, condition), so challenges are easiest made by
  copying a scenario the game already has. Empty for now.

## Limits (v1)
- **Trust:** the Steam login ticket isn't checked, so a modified client could post any name or lap. Laps under 5 s and race
  counts over 10 per post are dropped. Checking the ticket would need a Steam Web API publisher key for the game, which we don't have.
- **eSports results:** an event shows up and is enterable, but SteamNet doesn't send tournament race times here yet, so its
  leaderboard stays empty.
- **Challenges:** need scenario data (see above).
- Heartbeats are kept in memory only (they're about the last 90 s anyway).
