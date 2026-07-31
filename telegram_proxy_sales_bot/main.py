import asyncio
import base64
import json
import logging
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes


load_dotenv()

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("telegram_proxy_sales_bot")

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state.json"
PROXY_POOL_FILE = BASE_DIR / "proxy_pool.json"


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable is required: {name}")
    return value


BOT_TOKEN = env_required("TELEGRAM_BOT_TOKEN")
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "9026303200"))
PAYMENT_PHONE = "9026303200"
PAYMENT_NOTE = os.getenv("PAYMENT_NOTE", "Оплата прокси").strip() or "Оплата прокси"

PLANS: Dict[str, Dict[str, Any]] = {
    "plan_30": {"name": "30 ДНЕЙ", "price": 1000, "days": 30},
    "plan_year": {"name": "ГОД", "price": 5000, "days": 365},
    "plan_5years": {"name": "5 ЛЕТ", "price": 20000, "days": 365 * 5},
}


@dataclass
class ProxyCreds:
    host: str
    port: int
    username: str
    password: str
    protocol: str


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def default_state() -> Dict[str, Any]:
    return {
        "config": {"owner_id": OWNER_TELEGRAM_ID},
        "payment_requests": {},
        "subscriptions": {},
        "payments": [],
        "trial_used": {},
    }


def load_state() -> Dict[str, Any]:
    state = load_json(STATE_FILE, default_state())
    if "config" not in state or not isinstance(state["config"], dict):
        state["config"] = {"owner_id": OWNER_TELEGRAM_ID}
    if "owner_id" not in state["config"]:
        state["config"]["owner_id"] = OWNER_TELEGRAM_ID
    if "payment_requests" not in state:
        state["payment_requests"] = {}
    if "subscriptions" not in state:
        state["subscriptions"] = {}
    if "payments" not in state:
        state["payments"] = []
    if "trial_used" not in state:
        state["trial_used"] = {}
    return state


def get_owner_id(state: Dict[str, Any]) -> int:
    try:
        return int(state.get("config", {}).get("owner_id", OWNER_TELEGRAM_ID))
    except Exception:
        return OWNER_TELEGRAM_ID


def load_proxy_pool() -> List[Dict[str, Any]]:
    data = load_json(PROXY_POOL_FILE, [])
    if not isinstance(data, list):
        raise RuntimeError("proxy_pool.json must be an array")
    return data


def tariff_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("30 ДНЕЙ — 1000 ₽", callback_data="plan_30")],
            [InlineKeyboardButton("ГОД — 5000 ₽", callback_data="plan_year")],
            [InlineKeyboardButton("5 ЛЕТ — 20000 ₽", callback_data="plan_5years")],
        ]
    )


def paid_keyboard(req_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Я оплатил", callback_data=f"paid_{req_id}")]]
    )


def owner_request_keyboard(req_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"owner_approve_{req_id}")],
            [InlineKeyboardButton("❌ Отклонить", callback_data=f"owner_reject_{req_id}")],
        ]
    )


def ts_to_text(ts: int) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


def find_free_proxy(pool: List[Dict[str, Any]], state: Dict[str, Any], now_ts: int) -> Optional[ProxyCreds]:
    used_keys = set()
    for sub in state["subscriptions"].values():
        if int(sub.get("expires_at", 0)) > now_ts:
            key = f"{sub.get('host')}:{sub.get('port')}|{sub.get('username')}"
            used_keys.add(key)

    for item in pool:
        key = f"{item.get('host')}:{item.get('port')}|{item.get('username')}"
        if key in used_keys:
            continue
        try:
            return ProxyCreds(
                host=str(item["host"]),
                port=int(item["port"]),
                username=str(item["username"]),
                password=str(item["password"]),
                protocol=str(item.get("protocol", "socks5")),
            )
        except Exception:
            continue
    return None


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise RuntimeError("Соединение закрыто прокси")
        data += chunk
    return data


def _check_socks5_proxy(creds: ProxyCreds, timeout_sec: float = 6.0) -> tuple[bool, str]:
    with socket.create_connection((creds.host, creds.port), timeout=timeout_sec) as sock:
        sock.settimeout(timeout_sec)

        methods = [0x00, 0x02] if creds.username and creds.password else [0x00]
        sock.sendall(bytes([0x05, len(methods), *methods]))
        resp = _recv_exact(sock, 2)
        if resp[0] != 0x05:
            return False, "не SOCKS5"
        method = resp[1]
        if method == 0xFF:
            return False, "метод авторизации не принят"

        if method == 0x02:
            u = creds.username.encode("utf-8")
            p = creds.password.encode("utf-8")
            if len(u) > 255 or len(p) > 255:
                return False, "логин/пароль слишком длинные для SOCKS5"
            sock.sendall(bytes([0x01, len(u)]) + u + bytes([len(p)]) + p)
            auth_resp = _recv_exact(sock, 2)
            if auth_resp[1] != 0x00:
                return False, "ошибка логина/пароля"

        # CONNECT 1.1.1.1:80
        req = bytes([0x05, 0x01, 0x00, 0x01, 1, 1, 1, 1, 0, 80])
        sock.sendall(req)
        head = _recv_exact(sock, 4)
        if head[1] != 0x00:
            return False, f"CONNECT отклонен, код={head[1]}"

        atyp = head[3]
        if atyp == 0x01:
            _recv_exact(sock, 4)
        elif atyp == 0x03:
            ln = _recv_exact(sock, 1)[0]
            _recv_exact(sock, ln)
        elif atyp == 0x04:
            _recv_exact(sock, 16)
        _recv_exact(sock, 2)

    return True, "ok"


def _check_http_proxy(creds: ProxyCreds, timeout_sec: float = 6.0) -> tuple[bool, str]:
    with socket.create_connection((creds.host, creds.port), timeout=timeout_sec) as sock:
        sock.settimeout(timeout_sec)

        auth_line = ""
        if creds.username or creds.password:
            token = base64.b64encode(f"{creds.username}:{creds.password}".encode("utf-8")).decode("ascii")
            auth_line = f"Proxy-Authorization: Basic {token}\r\n"

        req = (
            "CONNECT 1.1.1.1:80 HTTP/1.1\r\n"
            "Host: 1.1.1.1:80\r\n"
            f"{auth_line}"
            "Connection: close\r\n\r\n"
        )
        sock.sendall(req.encode("utf-8"))
        data = sock.recv(1024).decode("latin-1", errors="ignore")
        first_line = data.splitlines()[0] if data else ""
        if " 200 " in first_line:
            return True, "ok"
        if " 407 " in first_line:
            return False, "ошибка авторизации (407)"
        return False, f"CONNECT неуспешен: {first_line or 'нет ответа'}"


def check_proxy_alive(creds: ProxyCreds, timeout_sec: float = 6.0) -> tuple[bool, str]:
    proto = creds.protocol.lower().strip()
    try:
        if proto in {"socks", "socks5", "socks5h"}:
            return _check_socks5_proxy(creds, timeout_sec=timeout_sec)
        if proto in {"http", "https"}:
            return _check_http_proxy(creds, timeout_sec=timeout_sec)
        return False, f"неподдерживаемый протокол: {creds.protocol}"
    except Exception as e:
        return False, str(e)


def make_proxy_message(creds: ProxyCreds, expires_at: int) -> str:
    return (
        "✅ Оплата подтверждена. Прокси подключен.\n\n"
        f"Протокол: {creds.protocol}\n"
        f"Host: {creds.host}\n"
        f"Port: {creds.port}\n"
        f"Login: {creds.username}\n"
        f"Password: {creds.password}\n"
        f"Действует до: {ts_to_text(expires_at)}\n\n"
        "После окончания срока доступ отключается."
    )


def extract_req_id(text: str) -> Optional[str]:
    m = re.search(r"([a-f0-9]{10})", text.lower())
    return m.group(1) if m else None


def find_matching_payment(state: Dict[str, Any], req_id: str, amount: int) -> Optional[Dict[str, Any]]:
    for payment in state.get("payments", []):
        if payment.get("status") != "new":
            continue
        if int(payment.get("amount", 0)) != amount:
            continue
        if extract_req_id(str(payment.get("comment", ""))) != req_id:
            continue
        return payment
    return None


def issue_subscription_for_request(state: Dict[str, Any], req: Dict[str, Any], plan: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    now_ts = int(time.time())
    expires_at = now_ts + int(plan["days"]) * 86400

    pool = load_proxy_pool()
    checked = 0
    creds: Optional[ProxyCreds] = None
    while True:
        candidate = find_free_proxy(pool, state, now_ts)
        if candidate is None:
            break
        checked += 1
        ok, _reason = check_proxy_alive(candidate)
        if ok:
            creds = candidate
            break
        # исключаем нерабочий кандидат из текущего прохода
        pool = [
            p for p in pool
            if not (
                str(p.get("host")) == candidate.host
                and int(p.get("port", 0)) == candidate.port
                and str(p.get("username")) == candidate.username
            )
        ]

    if creds is None:
        return None, "Нет свободного рабочего прокси в proxy_pool.json"

    state["subscriptions"][str(req["user_id"])] = {
        "plan_id": req["plan_id"],
        "host": creds.host,
        "port": creds.port,
        "username": creds.username,
        "password": creds.password,
        "protocol": creds.protocol,
        "expires_at": expires_at,
        "updated_at": now_ts,
    }
    return {
        "text": make_proxy_message(creds, expires_at),
        "expires_at": expires_at,
    }, None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "30 ДНЕЙ-1000РУБ\n"
        "ГОД-5000РУБ\n"
        "5ЛЕТ-20000РУБ\n\n"
        "Выберите тариф ниже:"
    )
    await update.effective_message.reply_text(text, reply_markup=tariff_keyboard())


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    state = load_state()
    sub = state["subscriptions"].get(user_id)
    now_ts = int(time.time())
    if not sub or int(sub.get("expires_at", 0)) <= now_ts:
        await update.effective_message.reply_text("У вас нет активного прокси. Нажмите /start")
        return

    await update.effective_message.reply_text(
        "Ваш прокси активен:\n"
        f"{sub.get('protocol')}://{sub.get('username')}:{sub.get('password')}@{sub.get('host')}:{sub.get('port')}\n"
        f"Действует до: {ts_to_text(int(sub['expires_at']))}"
    )


async def plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    plan = PLANS.get(query.data)
    if not plan:
        await query.message.reply_text("Неизвестный тариф. Нажмите /start")
        return

    req_id = uuid.uuid4().hex[:10]
    state = load_state()
    state["payment_requests"][req_id] = {
        "user_id": query.from_user.id,
        "username": query.from_user.username or "",
        "plan_id": query.data,
        "created_at": int(time.time()),
        "status": "waiting_payment",
    }
    save_json(STATE_FILE, state)

    text = (
        f"Вы выбрали тариф: {plan['name']}\n"
        f"Сумма к оплате: {plan['price']} ₽\n\n"
        f"Оплатите на номер: {PAYMENT_PHONE}\n"
        f"Комментарий к переводу: {PAYMENT_NOTE} #{req_id}\n\n"
        "После оплаты нажмите кнопку «Я оплатил»."
    )
    await query.message.reply_text(text, reply_markup=paid_keyboard(req_id))


async def paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer("Проверяю оплату...")

    req_id = query.data.replace("paid_", "", 1)
    state = load_state()
    req = state["payment_requests"].get(req_id)
    if not req:
        await query.message.reply_text("Заявка не найдена. Нажмите /start")
        return

    if req.get("user_id") != query.from_user.id:
        await query.message.reply_text("Это не ваша заявка.")
        return

    plan = PLANS[req["plan_id"]]
    payment = find_matching_payment(state, req_id=req_id, amount=int(plan["price"]))
    if payment is None:
        req["status"] = "payment_not_found"
        save_json(STATE_FILE, state)
        await query.message.reply_text(
            "❌ Вы не сделали оплату или платеж ещё не поступил.\n"
            "Оплатите и нажмите кнопку позже. Прокси не выдано."
        )
        return

    issue_result, error = issue_subscription_for_request(state, req, plan)
    if error:
        await query.message.reply_text(f"❌ {error}")
        return

    payment["status"] = "used"
    payment["used_for_req"] = req_id
    payment["used_at"] = int(time.time())
    req["status"] = "approved_auto"
    req["approved_at"] = int(time.time())
    save_json(STATE_FILE, state)

    await query.message.reply_text("✅ Оплата найдена. Прокси выдано.")
    await context.bot.send_message(chat_id=req["user_id"], text=issue_result["text"])


async def owner_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    state = load_state()
    if query.from_user.id != get_owner_id(state):
        await query.answer("Только владелец может это делать", show_alert=True)
        return

    await query.answer()
    parts = query.data.split("_", 2)
    if len(parts) != 3:
        return
    action = parts[1]
    req_id = parts[2]

    req = state["payment_requests"].get(req_id)
    if not req:
        await query.message.reply_text("Заявка не найдена")
        return

    if action == "reject":
        req["status"] = "rejected"
        save_json(STATE_FILE, state)
        await context.bot.send_message(chat_id=req["user_id"], text="Оплата не подтверждена. Напишите владельцу.")
        await query.message.reply_text(f"Заявка {req_id} отклонена")
        return

    plan = PLANS[req["plan_id"]]
    payment = find_matching_payment(state, req_id=req_id, amount=int(plan["price"]))
    if payment is None:
        await query.message.reply_text("Платеж не найден. Выдать прокси нельзя.")
        return

    issue_result, error = issue_subscription_for_request(state, req, plan)
    if error:
        await query.message.reply_text(error)
        return

    payment["status"] = "used"
    payment["used_for_req"] = req_id
    payment["used_at"] = int(time.time())
    req["status"] = "approved"
    req["approved_at"] = int(time.time())
    save_json(STATE_FILE, state)

    await context.bot.send_message(chat_id=req["user_id"], text=issue_result["text"])
    await query.message.reply_text(f"Заявка {req_id} подтверждена. Доступ выдан.")


async def income_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    if update.effective_user.id != get_owner_id(state):
        await update.effective_message.reply_text("Команда только для владельца")
        return

    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text(
            "Использование: /income <сумма> <комментарий> [телефон_отправителя]\n"
            "Пример: /income 1000 Оплата_прокси_#a1b2c3d4e5 79000000000"
        )
        return

    try:
        amount = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Сумма должна быть числом")
        return

    comment = context.args[1]
    payer_phone = context.args[2] if len(context.args) > 2 else ""
    req_id = extract_req_id(comment)

    payment_id = uuid.uuid4().hex[:10]
    payment = {
        "payment_id": payment_id,
        "amount": amount,
        "comment": comment,
        "payer_phone": payer_phone,
        "req_id": req_id or "",
        "status": "new",
        "created_at": int(time.time()),
    }
    state["payments"].append(payment)

    auto_issued = False
    if req_id and req_id in state["payment_requests"]:
        req = state["payment_requests"][req_id]
        plan = PLANS.get(req.get("plan_id", ""))
        if plan and int(plan["price"]) == amount and req.get("status") not in {"approved", "approved_auto"}:
            issue_result, error = issue_subscription_for_request(state, req, plan)
            if not error:
                payment["status"] = "used"
                payment["used_for_req"] = req_id
                payment["used_at"] = int(time.time())
                req["status"] = "approved_auto"
                req["approved_at"] = int(time.time())
                auto_issued = True
                await context.bot.send_message(chat_id=req["user_id"], text="✅ Оплата найдена. Прокси выдано.")
                await context.bot.send_message(chat_id=req["user_id"], text=issue_result["text"])

    save_json(STATE_FILE, state)

    if auto_issued:
        await update.effective_message.reply_text(f"Платеж добавлен и заявка {req_id} закрыта автоматически")
    else:
        await update.effective_message.reply_text(
            f"Платеж добавлен: {amount} ₽, id={payment_id}.\n"
            "Если пользователь уже нажал «Я оплатил», пусть нажмёт ещё раз для автопроверки."
        )


async def grantfree_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    if update.effective_user.id != get_owner_id(state):
        await update.effective_message.reply_text("Команда только для владельца")
        return

    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text(
            "Использование: /grantfree <user_id> <plan_id>\n"
            "Пример: /grantfree 9026303200 plan_30\n"
            "Доступные plan_id: plan_30, plan_year, plan_5years"
        )
        return

    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("user_id должен быть числом")
        return

    plan_id = context.args[1].strip()
    plan = PLANS.get(plan_id)
    if not plan:
        await update.effective_message.reply_text("Неверный plan_id. Используйте: plan_30, plan_year, plan_5years")
        return

    req = {
        "user_id": target_user_id,
        "username": "",
        "plan_id": plan_id,
        "created_at": int(time.time()),
        "status": "approved_free",
    }

    issue_result, error = issue_subscription_for_request(state, req, plan)
    if error:
        await update.effective_message.reply_text(f"❌ {error}")
        return

    save_json(STATE_FILE, state)

    await context.bot.send_message(chat_id=target_user_id, text="🎁 Вам выдан бесплатный доступ к прокси.")
    await context.bot.send_message(chat_id=target_user_id, text=issue_result["text"])
    await update.effective_message.reply_text(f"✅ Бесплатный прокси выдан пользователю {target_user_id} ({plan_id})")


async def iamowner_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    current_owner = get_owner_id(state)
    user_id = update.effective_user.id

    if user_id == current_owner:
        await update.effective_message.reply_text(f"Вы уже владелец. OWNER_ID={current_owner}")
        return

    # Разрешаем перепривязку владельца только если текущий owner выглядит как номер телефона,
    # либо если владелец ещё не был реально подтверждён.
    owner_str = str(current_owner)
    looks_like_phone = owner_str.startswith("9") and len(owner_str) == 10
    if not looks_like_phone:
        await update.effective_message.reply_text(
            "Владелец уже зафиксирован. Если это ваш бот, смените OWNER_TELEGRAM_ID в .env и перезапустите."
        )
        return

    state["config"]["owner_id"] = user_id
    save_json(STATE_FILE, state)
    await update.effective_message.reply_text(f"✅ Вы назначены владельцем. OWNER_ID={user_id}")


async def addproxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    if update.effective_user.id != get_owner_id(state):
        await update.effective_message.reply_text("Команда только для владельца")
        return

    if len(context.args) < 4:
        await update.effective_message.reply_text(
            "Использование: /addproxy <host> <port> <login> <password> [protocol]\n"
            "Пример: /addproxy 1.2.3.4 1080 user pass socks5"
        )
        return

    host = context.args[0].strip()
    try:
        port = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("port должен быть числом")
        return
    username = context.args[2].strip()
    password = context.args[3].strip()
    protocol = context.args[4].strip() if len(context.args) > 4 else "socks5"

    creds = ProxyCreds(host=host, port=port, username=username, password=password, protocol=protocol)
    ok, reason = check_proxy_alive(creds)
    if not ok:
        await update.effective_message.reply_text(f"❌ Прокси не прошел проверку: {reason}")
        return

    pool = load_proxy_pool()
    pool.append(
        {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "protocol": protocol,
        }
    )
    save_json(PROXY_POOL_FILE, pool)
    await update.effective_message.reply_text("✅ Прокси добавлен в пул и прошёл проверку")


async def checkpool_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    if update.effective_user.id != get_owner_id(state):
        await update.effective_message.reply_text("Команда только для владельца")
        return

    pool = load_proxy_pool()
    if not pool:
        await update.effective_message.reply_text("Пул пуст")
        return

    lines: List[str] = ["Проверка пула:"]
    ok_count = 0
    for i, item in enumerate(pool, start=1):
        creds = ProxyCreds(
            host=str(item.get("host", "")),
            port=int(item.get("port", 0)),
            username=str(item.get("username", "")),
            password=str(item.get("password", "")),
            protocol=str(item.get("protocol", "socks5")),
        )
        ok, reason = check_proxy_alive(creds)
        if ok:
            ok_count += 1
            lines.append(f"{i}) ✅ {creds.protocol}://{creds.host}:{creds.port}")
        else:
            lines.append(f"{i}) ❌ {creds.protocol}://{creds.host}:{creds.port} — {reason}")

    lines.append(f"\nИтого: {ok_count}/{len(pool)} рабочих")
    await update.effective_message.reply_text("\n".join(lines)[:3900])


async def testproxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    state = load_state()

    if state.get("trial_used", {}).get(str(user_id)):
        await update.effective_message.reply_text("Пробный прокси уже был выдан. Используйте /start для обычной покупки.")
        return

    plan = {"days": 1}
    req = {
        "user_id": user_id,
        "username": update.effective_user.username or "",
        "plan_id": "trial_1day",
        "created_at": int(time.time()),
        "status": "approved_trial",
    }
    issue_result, error = issue_subscription_for_request(state, req, plan)
    if error:
        await update.effective_message.reply_text(
            "❌ Нет свободного прокси для теста. Добавьте прокси через /addproxy или файл proxy_pool.json"
        )
        return

    state["trial_used"][str(user_id)] = int(time.time())
    save_json(STATE_FILE, state)
    await update.effective_message.reply_text("✅ Тестовый прокси выдан на 1 день")
    await context.bot.send_message(chat_id=user_id, text=issue_result["text"])


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(f"Ваш Telegram ID: {update.effective_user.id}")


def ensure_files() -> None:
    if not STATE_FILE.exists():
        save_json(STATE_FILE, default_state())
    if not PROXY_POOL_FILE.exists():
        save_json(PROXY_POOL_FILE, [])


def main() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    ensure_files()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("iamowner", iamowner_command))
    app.add_handler(CommandHandler("income", income_command))
    app.add_handler(CommandHandler("grantfree", grantfree_command))
    app.add_handler(CommandHandler("addproxy", addproxy_command))
    app.add_handler(CommandHandler("checkpool", checkpool_command))
    app.add_handler(CommandHandler("testproxy", testproxy_command))
    app.add_handler(CallbackQueryHandler(plan_callback, pattern=r"^plan_"))
    app.add_handler(CallbackQueryHandler(paid_callback, pattern=r"^paid_"))
    app.add_handler(CallbackQueryHandler(owner_action_callback, pattern=r"^owner_(approve|reject)_"))

    logger.info("Proxy sales bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
