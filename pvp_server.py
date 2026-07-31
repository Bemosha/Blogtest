import json
import math
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


TICK_RATE = 60.0
ARENA_W = 900
ARENA_H = 600
TANK_SPEED = 2.6
TURN_SPEED = 0.06
BULLET_SPEED = 7.0
FIRE_COOLDOWN = 20  # ticks
MAX_HP = 5


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class Room:
    def __init__(self):
        self.id = "room-1"
        self.players = {}
        self.order = []  # combatants currently active (humans + bots)
        self.human_ids = []
        # Classic online 1v1: max two humans in room.
        self.max_humans = 2
        self.max_combatants = 2
        self.inputs = {}
        self.bullets = []
        self.winner = None
        self.lock = threading.Lock()
        self.last_seen = {}
        self.inactive_timeout = 20.0

    def _spawn_random(self):
        return {
            "x": 60.0 + (ARENA_W - 120.0) * (0.15 + 0.7 * (time.time_ns() % 1000000) / 1000000.0),
            "y": 60.0 + (ARENA_H - 120.0) * (0.15 + 0.7 * ((time.time_ns() // 1000) % 1000000) / 1000000.0),
            "a": ((time.time_ns() // 1000000) % 6283) / 1000.0,
            "hp": MAX_HP,
            "cd": 0,
        }

    def _spawn_for_side(self, side):
        # Backward compatibility helper: now all combatants spawn around arena randomly.
        return self._spawn_random()

    def _respawn_player(self, pid):
        prev = self.players.get(pid, {})
        self.players[pid] = self._spawn_random()
        self.players[pid]["name"] = prev.get("name", "Игрок")
        self.players[pid]["isBot"] = bool(prev.get("isBot", False))

    def _nearest_enemy_id(self, pid):
        me = self.players.get(pid)
        if not me or me.get("hp", 0) <= 0:
            return None
        best_id = None
        best_d2 = 1e18
        for other_id, p in self.players.items():
            if other_id == pid:
                continue
            if p.get("hp", 0) <= 0:
                continue
            dx = p["x"] - me["x"]
            dy = p["y"] - me["y"]
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best_id = other_id
        return best_id

    def restart(self):
        self.bullets = []
        self.winner = None
        for pid in list(self.players.keys()):
            self._respawn_player(pid)

    def _ensure_combatants(self):
        # Keep classic 1v1 behavior:
        # - 1 human -> add 1 bot opponent
        # - 2 humans -> no bots
        # - 0 humans -> no bots
        self.human_ids = [pid for pid in self.human_ids if pid in self.players]
        bot_ids = [pid for pid, p in self.players.items() if bool(p.get("isBot", False))]
        if len(self.human_ids) == 1:
            need_bots = 1
        else:
            need_bots = 0

        # Remove excess bots
        if len(bot_ids) > need_bots:
            for bid in bot_ids[need_bots:]:
                self.players.pop(bid, None)

        # Add missing bots
        bot_ids = [pid for pid, p in self.players.items() if bool(p.get("isBot", False))]
        while len(bot_ids) < need_bots:
            bid = f"bot-{uuid.uuid4().hex[:8]}"
            self.players[bid] = self._spawn_random()
            self.players[bid]["name"] = f"Бот {len(bot_ids) + 1}"
            self.players[bid]["isBot"] = True
            bot_ids.append(bid)

        # Humans first, then bots
        self.order = list(self.human_ids) + bot_ids

    def _prune_inactive(self):
        now = time.time()
        alive_humans = []
        for pid in self.human_ids:
            seen = self.last_seen.get(pid, 0)
            if now - seen <= self.inactive_timeout:
                alive_humans.append(pid)
            else:
                self.players.pop(pid, None)
                self.inputs.pop(pid, None)
                self.last_seen.pop(pid, None)
        self.human_ids = alive_humans
        self._ensure_combatants()

    def join(self, nickname):
        with self.lock:
            self._prune_inactive()
            if len(self.human_ids) >= self.max_humans:
                # return spectator-ish info if full
                pid = str(uuid.uuid4())
                return {
                    "playerId": pid,
                    "roomId": self.id,
                    "side": "spectator",
                    "ready": len(self.players) >= 2,
                    "full": True,
                }

            pid = str(uuid.uuid4())
            side = "arena"
            self.human_ids.append(pid)
            self.players[pid] = self._spawn_random()
            self.players[pid]["name"] = (nickname or "Игрок").strip()[:18] or "Игрок"
            self.players[pid]["isBot"] = False
            self.inputs[pid] = {"w": False, "a": False, "s": False, "d": False, "fire": False}
            self.last_seen[pid] = time.time()
            self._ensure_combatants()
            return {
                "playerId": pid,
                "roomId": self.id,
                "side": side,
                "ready": len(self.players) >= 2,
                "full": False,
            }

    def set_input(self, pid, keys):
        with self.lock:
            if pid in self.inputs:
                self.last_seen[pid] = time.time()
                self.inputs[pid] = {
                    "w": bool(keys.get("w", False)),
                    "a": bool(keys.get("a", False)),
                    "s": bool(keys.get("s", False)),
                    "d": bool(keys.get("d", False)),
                    "fire": bool(keys.get("fire", False)),
                }

    def get_state(self, pid):
        with self.lock:
            self._prune_inactive()
            if pid in self.last_seen:
                self.last_seen[pid] = time.time()
            return {
                "roomId": self.id,
                "ready": len(self.players) >= 2,
                "winner": self.winner,
                "players": {
                    k: {
                        "x": v["x"],
                        "y": v["y"],
                        "a": v["a"],
                        "hp": v["hp"],
                        "name": v.get("name", "Игрок"),
                        "isBot": bool(v.get("isBot", False)),
                    }
                    for k, v in self.players.items()
                },
                "bullets": [{"x": b["x"], "y": b["y"]} for b in self.bullets],
                "you": pid,
            }

    def tick(self):
        with self.lock:
            self._prune_inactive()
            self._ensure_combatants()
            if len(self.players) < 2:
                return
            self.winner = None

            # Bot AI for all bots in arena
            for pid in list(self.players.keys()):
                p = self.players.get(pid)
                if not p or not p.get("isBot") or p["hp"] <= 0:
                    continue
                enemy_id = self._nearest_enemy_id(pid)
                enemy = self.players.get(enemy_id)
                if not enemy or enemy["hp"] <= 0:
                    continue
                dx = enemy["x"] - p["x"]
                dy = enemy["y"] - p["y"]
                target_a = math.atan2(dy, dx)
                da = (target_a - p["a"] + math.pi) % (2 * math.pi) - math.pi
                if da > TURN_SPEED:
                    p["a"] += TURN_SPEED
                elif da < -TURN_SPEED:
                    p["a"] -= TURN_SPEED
                else:
                    p["a"] = target_a

                dist2 = dx * dx + dy * dy
                if dist2 > 170 * 170:
                    p["x"] += math.cos(p["a"]) * (TANK_SPEED * 0.75)
                    p["y"] += math.sin(p["a"]) * (TANK_SPEED * 0.75)
                    p["x"] = clamp(p["x"], 20, ARENA_W - 20)
                    p["y"] = clamp(p["y"], 20, ARENA_H - 20)
                if p["cd"] > 0:
                    p["cd"] -= 1
                if abs(da) < 0.24 and p["cd"] <= 0:
                    self.bullets.append(
                        {
                            "x": p["x"] + math.cos(p["a"]) * 26,
                            "y": p["y"] + math.sin(p["a"]) * 26,
                            "vx": math.cos(p["a"]) * BULLET_SPEED,
                            "vy": math.sin(p["a"]) * BULLET_SPEED,
                            "owner": pid,
                            "life": 150,
                        }
                    )
                    p["cd"] = FIRE_COOLDOWN + 6

            # Move all human players
            for pid in list(self.human_ids):
                p = self.players.get(pid)
                if not p or p["hp"] <= 0 or p.get("isBot"):
                    continue
                inp = self.inputs.get(pid, {})

                if inp.get("a"):
                    p["a"] -= TURN_SPEED
                if inp.get("d"):
                    p["a"] += TURN_SPEED

                speed = 0.0
                if inp.get("w"):
                    speed += TANK_SPEED
                if inp.get("s"):
                    speed -= TANK_SPEED * 0.6

                p["x"] += math.cos(p["a"]) * speed
                p["y"] += math.sin(p["a"]) * speed

                p["x"] = clamp(p["x"], 20, ARENA_W - 20)
                p["y"] = clamp(p["y"], 20, ARENA_H - 20)

                if p["cd"] > 0:
                    p["cd"] -= 1

                if inp.get("fire") and p["cd"] <= 0:
                    self.bullets.append(
                        {
                            "x": p["x"] + math.cos(p["a"]) * 26,
                            "y": p["y"] + math.sin(p["a"]) * 26,
                            "vx": math.cos(p["a"]) * BULLET_SPEED,
                            "vy": math.sin(p["a"]) * BULLET_SPEED,
                            "owner": pid,
                            "life": 150,
                        }
                    )
                    p["cd"] = FIRE_COOLDOWN

            # Update bullets / collisions
            alive = []
            for b in self.bullets:
                b["x"] += b["vx"]
                b["y"] += b["vy"]
                b["life"] -= 1

                if b["x"] < 0 or b["x"] > ARENA_W or b["y"] < 0 or b["y"] > ARENA_H or b["life"] <= 0:
                    continue

                hit = False
                for pid, p in list(self.players.items()):
                    if pid == b["owner"]:
                        continue
                    if not p or p["hp"] <= 0:
                        continue
                    dx = p["x"] - b["x"]
                    dy = p["y"] - b["y"]
                    if dx * dx + dy * dy <= 24 * 24:
                        p["hp"] -= 1
                        hit = True
                        if p["hp"] <= 0:
                            self._respawn_player(pid)
                        break

                if not hit:
                    alive.append(b)

            self.bullets = alive


ROOM = Room()

# ===== Lightweight online presence relay for index.html =====
ONLINE_PLAYERS = {}
ONLINE_LOCK = threading.Lock()
ONLINE_TIMEOUT = 12.0
ONLINE_BANS = {}  # normalized_nick -> {"until": ts, "by": str, "created": ts}


def _norm_nick(nickname):
    return str(nickname or "").strip().lower()[:64]


def _prune_bans(now=None):
    if now is None:
        now = time.time()
    stale = [k for k, v in ONLINE_BANS.items() if float(v.get("until", 0)) <= now]
    for k in stale:
        ONLINE_BANS.pop(k, None)


def _ban_info_by_nick(nickname, now=None):
    if now is None:
        now = time.time()
    _prune_bans(now)
    return ONLINE_BANS.get(_norm_nick(nickname))


def online_join(nickname):
    pid = str(uuid.uuid4())
    name = (nickname or "Игрок").strip()[:18] or "Игрок"
    now = time.time()
    with ONLINE_LOCK:
        ban = _ban_info_by_nick(name, now)
        if ban:
            return {
                "ok": False,
                "error": "banned",
                "banned": True,
                "untilTs": float(ban.get("until", now)),
            }
        ONLINE_PLAYERS[pid] = {
            "id": pid,
            "name": name,
            "x": 0.0,
            "z": 0.0,
            "angle": 0.0,
            "turretAngle": 0.0,
            "hp": MAX_HP,
            "maxHp": MAX_HP,
            "kills": 0,
            "ts": now,
            "banMarkUntil": 0.0,
            "kickAt": 0.0,
        }
    return {"ok": True, "playerId": pid, "name": name}


def online_presence(pid, state):
    if not pid:
        return {"ok": False, "error": "missing playerId"}
    now = time.time()
    with ONLINE_LOCK:
        _prune_bans(now)
        # prune stale
        stale = [k for k, v in ONLINE_PLAYERS.items() if now - float(v.get("ts", 0)) > ONLINE_TIMEOUT]
        for k in stale:
            ONLINE_PLAYERS.pop(k, None)

        # kick players whose 5s BAN label has passed
        to_kick = [
            k
            for k, v in ONLINE_PLAYERS.items()
            if float(v.get("kickAt", 0.0)) > 0 and now >= float(v.get("kickAt", 0.0))
        ]
        for k in to_kick:
            ONLINE_PLAYERS.pop(k, None)

        cur = ONLINE_PLAYERS.get(pid)
        if not cur:
            return {"ok": False, "error": "unknown player"}

        ban = _ban_info_by_nick(cur.get("name", ""), now)
        if ban:
            return {
                "ok": False,
                "error": "banned",
                "banned": True,
                "untilTs": float(ban.get("until", now)),
            }

        cur["x"] = float(state.get("x", cur.get("x", 0.0)))
        cur["z"] = float(state.get("z", cur.get("z", 0.0)))
        cur["angle"] = float(state.get("angle", cur.get("angle", 0.0)))
        cur["turretAngle"] = float(state.get("turretAngle", cur.get("turretAngle", 0.0)))
        cur["hp"] = float(state.get("hp", cur.get("hp", MAX_HP)))
        cur["maxHp"] = float(state.get("maxHp", cur.get("maxHp", MAX_HP)))
        cur["kills"] = int(state.get("kills", cur.get("kills", 0)))
        cur["ts"] = now
    return {"ok": True}


def online_state(pid):
    now = time.time()
    with ONLINE_LOCK:
        _prune_bans(now)
        stale = [k for k, v in ONLINE_PLAYERS.items() if now - float(v.get("ts", 0)) > ONLINE_TIMEOUT]
        for k in stale:
            ONLINE_PLAYERS.pop(k, None)

        # kick players whose BAN label time has ended
        to_kick = [
            k
            for k, v in ONLINE_PLAYERS.items()
            if float(v.get("kickAt", 0.0)) > 0 and now >= float(v.get("kickAt", 0.0))
        ]
        for k in to_kick:
            ONLINE_PLAYERS.pop(k, None)

        me = ONLINE_PLAYERS.get(pid)
        if me:
            ban = _ban_info_by_nick(me.get("name", ""), now)
            if ban:
                return {
                    "ok": False,
                    "error": "banned",
                    "banned": True,
                    "untilTs": float(ban.get("until", now)),
                }

        others = [
            {
                "id": v.get("id"),
                "name": v.get("name", "Игрок"),
                "x": float(v.get("x", 0.0)),
                "z": float(v.get("z", 0.0)),
                "angle": float(v.get("angle", 0.0)),
                "turretAngle": float(v.get("turretAngle", 0.0)),
                "hp": float(v.get("hp", MAX_HP)),
                "maxHp": float(v.get("maxHp", MAX_HP)),
                "kills": int(v.get("kills", 0)),
                "banMarkUntilMs": int(float(v.get("banMarkUntil", 0.0)) * 1000),
            }
            for k, v in ONLINE_PLAYERS.items()
            if k != pid
        ]
    return {"ok": True, "players": others}


def online_ban(admin_pid, admin_flag, target_nickname, duration_minutes):
    if not admin_flag:
        return {"ok": False, "error": "admin required"}

    nick = (target_nickname or "").strip()[:18]
    if not nick:
        return {"ok": False, "error": "missing nickname"}

    try:
        mins = float(duration_minutes)
    except Exception:
        mins = 0.0
    mins = max(0.1, min(60.0 * 24.0 * 30.0, mins))

    now = time.time()
    until = now + mins * 60.0
    norm = _norm_nick(nick)
    with ONLINE_LOCK:
        _prune_bans(now)
        ONLINE_BANS[norm] = {
            "until": until,
            "by": str(admin_pid or "admin"),
            "created": now,
        }

        # Show BAN label for 5 seconds on current online target(s), then kick.
        affected = 0
        for _, v in ONLINE_PLAYERS.items():
            if _norm_nick(v.get("name", "")) == norm:
                v["banMarkUntil"] = now + 5.0
                v["kickAt"] = now + 5.0
                affected += 1

    return {
        "ok": True,
        "nickname": nick,
        "untilTs": until,
        "durationMinutes": mins,
        "affectedOnline": affected,
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}

        if self.path == "/join":
            self._send(200, ROOM.join(data.get("nickname", "Игрок")))
            return

        if self.path == "/input":
            pid = data.get("playerId")
            keys = data.get("keys", {})
            ROOM.set_input(pid, keys)
            self._send(200, {"ok": True})
            return

        if self.path == "/state":
            pid = data.get("playerId")
            self._send(200, ROOM.get_state(pid))
            return

        if self.path == "/restart":
            ROOM.restart()
            self._send(200, {"ok": True})
            return

        # Lightweight relay API for main index.html online mode
        if self.path == "/join_online":
            self._send(200, online_join(data.get("nickname", "Игрок")))
            return

        if self.path == "/presence":
            self._send(200, online_presence(data.get("playerId"), data.get("state", {})))
            return

        if self.path == "/state_online":
            self._send(200, online_state(data.get("playerId")))
            return

        if self.path == "/ban_online":
            self._send(
                200,
                online_ban(
                    data.get("playerId"),
                    bool(data.get("admin", False)),
                    data.get("targetNickname", ""),
                    data.get("durationMinutes", 0),
                ),
            )
            return

        self._send(404, {"error": "not found"})


def game_loop():
    dt = 1.0 / TICK_RATE
    while True:
        t0 = time.perf_counter()
        ROOM.tick()
        spent = time.perf_counter() - t0
        time.sleep(max(0.0, dt - spent))


if __name__ == "__main__":
    threading.Thread(target=game_loop, daemon=True).start()
    host = str(os.getenv("SERVER_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    port = int(os.getenv("SERVER_PORT", "8765") or 8765)
    print(f"PvP server running on http://{host}:{port}")
    print("Open online_pvp.html in two browser windows.")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
