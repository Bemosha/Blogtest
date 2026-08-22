#!/usr/bin/env python3
import json
import os
import re
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


HOST = "127.0.0.1"
PORT = 8090
DB_FILE = "lynk_users.json"


def normalize_phone(value: str) -> str:
    value = (value or "").strip()
    digits = re.sub(r"\D", "", value)
    if not digits:
        return ""
    # РФ: 8XXXXXXXXXX -> 7XXXXXXXXXX
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return "+" + digits


def valid_email(value: str) -> bool:
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", (value or "").strip()))


def load_users() -> dict:
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_users(users: dict) -> None:
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


class LynkHandler(SimpleHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/find_friend":
            q = parse_qs(parsed.query)
            phone = normalize_phone((q.get("phone") or [""])[0])
            if not phone or len(re.sub(r"\D", "", phone)) < 10:
                return self._json(400, {"ok": False, "error": "Некорректный номер"})

            users = load_users()
            user = users.get(phone)
            if not user:
                return self._json(200, {"ok": True, "found": False})

            return self._json(
                200,
                {
                    "ok": True,
                    "found": True,
                    "user": {
                        "phone": phone,
                        "nickname": user.get("nickname", "Пользователь Lynk"),
                        "avatar": user.get("avatar", ""),
                        "updated_at": user.get("updated_at", ""),
                    },
                },
            )

        if parsed.path == "/api/health":
            return self._json(200, {"ok": True, "service": "lynk"})

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/register":
            return self._json(404, {"ok": False, "error": "Not found"})

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._json(400, {"ok": False, "error": "Невалидный JSON"})

        email = (payload.get("email") or "").strip()
        phone = normalize_phone(payload.get("phone") or "")
        nickname = (payload.get("nickname") or "").strip()
        avatar = (payload.get("avatar") or "").strip()

        if not valid_email(email):
            return self._json(400, {"ok": False, "error": "Некорректная почта"})
        if not phone or len(re.sub(r"\D", "", phone)) < 10:
            return self._json(400, {"ok": False, "error": "Некорректный номер"})
        if len(nickname) < 2:
            return self._json(400, {"ok": False, "error": "Короткий никнейм"})

        users = load_users()
        users[phone] = {
            "email": email,
            "phone": phone,
            "nickname": nickname,
            "avatar": avatar,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        save_users(users)

        return self._json(200, {"ok": True, "phone": phone})


def main():
    server = ThreadingHTTPServer((HOST, PORT), LynkHandler)
    print(f"Lynk server started: http://{HOST}:{PORT}/lynk.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
