# bot.py
# -*- coding: utf-8 -*-
# Бот системы кланов для Telegram (aiogram 3 + SQLite)
# Запуск: python bot.py
# Переменные окружения: BOT_TOKEN (обязательно), DB_PATH (опционально, по умолчанию clans.db)

import asyncio
import logging
import os
import re
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

# ============================================================
# КОНФИГ
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", "clans.db")

# ID пользователей, которые считаются "владельцем" бота (полный доступ к ; командам)
BOT_OWNER_IDS = {
    # 123456789,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("clanbot")

router = Router()

# ============================================================
# БАЗА ДАННЫХ
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS clans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    leader_id INTEGER NOT NULL,
    type TEXT NOT NULL DEFAULT 'open',
    status TEXT NOT NULL DEFAULT 'active',
    max_members INTEGER NOT NULL DEFAULT 20,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    clan_id INTEGER,
    clan_role TEXT,
    FOREIGN KEY (clan_id) REFERENCES clans(id)
);

CREATE TABLE IF NOT EXISTS clan_bans (
    user_id INTEGER PRIMARY KEY,
    banned_by INTEGER NOT NULL,
    reason TEXT DEFAULT '',
    banned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clan_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
"""

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()

async def db():
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn

# ---- Хелперы ----
async def get_user(conn, user_id: int):
    cur = await conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return await cur.fetchone()

async def get_clan(conn, clan_id: int):
    cur = await conn.execute("SELECT * FROM clans WHERE id=?", (clan_id,))
    return await cur.fetchone()

async def get_clan_by_name(conn, name: str):
    cur = await conn.execute("SELECT * FROM clans WHERE name=?", (name,))
    return await cur.fetchone()

async def count_members(conn, clan_id: int) -> int:
    cur = await conn.execute("SELECT COUNT(*) AS c FROM users WHERE clan_id=?", (clan_id,))
    row = await cur.fetchone()
    return row["c"]

async def is_banned(conn, user_id: int) -> bool:
    cur = await conn.execute("SELECT 1 FROM clan_bans WHERE user_id=?", (user_id,))
    return await cur.fetchone() is not None

def role_ru(role: str) -> str:
    return {"leader": "Лидер", "officer": "Заместитель", "member": "Участник"}.get(role, role or "—")

async def is_group_admin(message: Message, user_id: int) -> bool:
    if message.chat.type == ChatType.PRIVATE:
        return False
    try:
        member = await message.chat.get_member(user_id)
        return member.status in ("administrator", "creator")
    except TelegramBadRequest:
        return False

async def can_moderate(message: Message, conn) -> bool:
    if message.from_user.id in BOT_OWNER_IDS:
        return True
    return await is_group_admin(message, message.from_user.id)

async def can_manage_clan(message: Message, conn):
    if await can_moderate(message, conn):
        return True
    u = await get_user(conn, message.from_user.id)
    if u and u["clan_role"] in ("leader", "officer"):
        return True
    return False

# ============================================================
# ТЕКСТЫ
# ============================================================
HELP_INFO = (
    "📖 <b>Информация о боте кланов</b>\n\n"
    "<b>Пользовательские команды (префикс !):</b>\n"
    "• <code>!создать клан Название</code> — создать клан (описание со 2-й строки)\n"
    "• <code>!клан изменить НовоеИмя</code> — переименовать (описание со 2-й строки)\n"
    "• <code>!мой клан</code> — карточка моего клана\n"
    "• <code>!кланы [страница]</code> — топ кланов\n"
    "• <code>!клан состав [номер|название]</code> — участники клана\n"
    "• <code>!кланинфо</code> — статистика моего клана и роли\n"
    "• <code>!клан + [номер]</code> — заявка на вступление\n"
    "• <code>!клан пригласить @user</code> — пригласить (лидер/зам)\n"
    "• <code>!клан исключить @user</code> — исключить (лидер/зам)\n"
    "• <code>!клан кик @user</code> — исключить из клана (лидер/зам/админ чата)\n"
    "• <code>!клан бан @user</code> — бан в клановой системе (лидер/зам/админ чата)\n"
    "• <code>!клан разбан @user</code> — снять бан\n"
    "• <code>!выйти из клана</code> — покинуть клан\n"
    "• <code>!клан тип открытый|закрытый</code> — режим вступления\n"
    "• <code>!клан лимит &lt;число&gt;</code> — максимум участников (лидер)\n"
    "• <code>!клан созвать</code> — упомянуть всех участников (лидер)\n"
    "• <code>!удалить клан</code> → <code>!удалить клан подтвердить</code>\n\n"
    "<b>Модераторские команды (префикс ;):</b>\n"
    "• <code>;удалить клан [номер|название]</code>\n"
    "• <code>;клан переименовать [номер] НовоеИмя</code>\n"
    "• <code>;клан заблокировать [номер]</code>\n"
    "• <code>;клан разблокировать [номер]</code>\n"
    "• <code>;клан исключить [номер] @user</code>\n"
    "• <code>;клан бан @user</code>\n"
    "• <code>;клан разбан @user</code>"
)

# ============================================================
# ПАРСИНГ
# ============================================================
def parse_arg(text: str):
    parts = text.split("\n", 1)
    first = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    return first, rest

def extract_target(message: Message) -> int | None:
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    if message.entities:
        for e in message.entities:
            if e.type == "text_mention" and e.user:
                return e.user.id
    m = re.search(r"\b(\d{5,})\b", message.text or "")
    if m:
        return int(m.group(1))
    return None

# ============================================================
# !info
# ============================================================
@router.message(F.text.regexp(r"^!info\b"))
async def cmd_info(message: Message):
    await message.reply(HELP_INFO, parse_mode=ParseMode.HTML)

# ============================================================
# !создать клан
# ============================================================
@router.message(F.text.regexp(r"^!создать клан\b"))
async def cmd_create_clan(message: Message):
    user_id = message.from_user.id
    first, desc = parse_arg(message.text)
    name = first[len("!создать клан"):].strip()
    if not name:
        return await message.reply("❌ Укажи название: <code>!создать клан Название</code>", parse_mode=ParseMode.HTML)

    async for conn in db():
        if await is_banned(conn, user_id):
            return await message.reply("🚫 Ты забанен в клановой системе.")
        u = await get_user(conn, user_id)
        if u and u["clan_id"]:
            return await message.reply("❌ Ты уже состоишь в клане.")
        if await get_clan_by_name(conn, name):
            return await message.reply("❌ Клан с таким названием уже существует.")

        now = datetime.utcnow().isoformat()
        cur = await conn.execute(
            "INSERT INTO clans (name, description, leader_id, type, status, max_members, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (name, desc, user_id, "open", "active", 20, now),
        )
        clan_id = cur.lastrowid
        await conn.execute(
            "INSERT INTO users (user_id, clan_id, clan_role) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET clan_id=excluded.clan_id, clan_role=excluded.clan_role",
            (user_id, clan_id, "leader"),
        )
        await conn.commit()
    await message.reply(f"✅ Клан <b>{name}</b> создан! Ты — Лидер.", parse_mode=ParseMode.HTML)

# ============================================================
# !клан изменить
# ============================================================
@router.message(F.text.regexp(r"^!клан изменить\b"))
async def cmd_rename_clan(message: Message):
    first, desc = parse_arg(message.text)
    new_name = first[len("!клан изменить"):].strip()
    if not new_name:
        return await message.reply("❌ Укажи новое название.")
    async for conn in db():
        u = await get_user(conn, message.from_user.id)
        if not u or not u["clan_id"]:
            return await message.reply("❌ Ты не в клане.")
        if u["clan_role"] != "leader":
            return await message.reply("❌ Только Лидер может переименовать клан.")
        if await get_clan_by_name(conn, new_name):
            return await message.reply("❌ Такое название уже занято.")
        if desc:
            await conn.execute("UPDATE clans SET name=?, description=? WHERE id=?", (new_name, desc, u["clan_id"]))
        else:
            await conn.execute("UPDATE clans SET name=? WHERE id=?", (new_name, u["clan_id"]))
        await conn.commit()
    await message.reply(f"✅ Клан переименован в <b>{new_name}</b>.", parse_mode=ParseMode.HTML)

# ============================================================
# !мой клан
# ============================================================
@router.message(F.text.regexp(r"^!мой клан$"))
async def cmd_my_clan(message: Message):
    async for conn in db():
        u = await get_user(conn, message.from_user.id)
        if not u or not u["clan_id"]:
            return await message.reply("❌ Ты не в клане. Создай: <code>!создать клан Имя</code>", parse_mode=ParseMode.HTML)
        c = await get_clan(conn, u["clan_id"])
        members = await count_members(conn, c["id"])

    status = "🟢 активен" if c["status"] == "active" else "🔴 заблокирован"
    typ = "открытый" if c["type"] == "open" else "закрытый"
    await message.reply(
        f"🏰 <b>{c['name']}</b>\n"
        f"📝 {c['description'] or '—'}\n"
        f"👥 Участники: {members}/{c['max_members']}\n"
        f"🔓 Тип: {typ}\n"
        f"📅 Создан: {c['created_at'][:10]}\n"
        f"📊 Статус: {status}",
        parse_mode=ParseMode.HTML,
    )

# ============================================================
# !кланинфо
# ============================================================
@router.message(F.text.regexp(r"^!кланинфо$"))
async def cmd_clan_info(message: Message):
    async for conn in db():
        u = await get_user(conn, message.from_user.id)
        if not u or not u["clan_id"]:
            return await message.reply("❌ Ты не в клане.")
        c = await get_clan(conn, u["clan_id"])
        cur = await conn.execute(
            "SELECT user_id, clan_role FROM users WHERE clan_id=? ORDER BY "
            "CASE clan_role WHEN 'leader' THEN 0 WHEN 'officer' THEN 1 ELSE 2 END, user_id",
            (c["id"],),
        )
        members = await cur.fetchall()

    roles = {"leader": [], "officer": [], "member": []}
    for m in members:
        roles[m["clan_role"]].append(m["user_id"])

    def fmt(ids):
        if not ids:
            return "—"
        return ", ".join(f'<a href="tg://user?id={i}">ID{i}</a>' for i in ids)

    text = (
        f"🏰 <b>{c['name']}</b> — статистика\n"
        f"📝 {c['description'] or '—'}\n"
        f"👥 Участники: {len(members)}/{c['max_members']}\n"
        f"🔓 Тип: {'открытый' if c['type']=='open' else 'закрытый'}\n"
        f"📊 Статус: {'активен' if c['status']=='active' else 'заблокирован'}\n\n"
        f"👑 <b>Лидер:</b> {fmt(roles['leader'])}\n"
        f"🛡 <b>Заместители:</b> {fmt(roles['officer'])}\n"
        f"👤 <b>Участники:</b> {fmt(roles['member'])}"
    )
    await message.reply(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ============================================================
# !кланы [страница]
# ============================================================
@router.message(F.text.regexp(r"^!кланы(\s+\d+)?$"))
async def cmd_top_clans(message: Message):
    parts = message.text.split()
    page = int(parts[1]) if len(parts) > 1 else 1
    page = max(1, page)
    per_page = 10
    offset = (page - 1) * per_page

    async for conn in db():
        cur = await conn.execute("SELECT COUNT(*) AS c FROM clans WHERE status='active'")
        total = (await cur.fetchone())["c"]
        cur = await conn.execute(
            "SELECT id, name, leader_id FROM clans WHERE status='active' ORDER BY id ASC LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        rows = await cur.fetchall()

    if not rows:
        return await message.reply("📭 Кланов на этой странице нет.")
    lines = [f"🏆 <b>Топ кланов</b> (стр. {page})\n"]
    for i, r in enumerate(rows, start=offset + 1):
        lines.append(f"{i}. <b>{r['name']}</b> (ID {r['id']}) — лидер {r['leader_id']}")
    lines.append(f"\nВсего активных: {total}")
    await message.reply("\n".join(lines), parse_mode=ParseMode.HTML)

# ============================================================
# !клан состав [номер|название]
# ============================================================
@router.message(F.text.regexp(r"^!клан состав\b"))
async def cmd_clan_roster(message: Message):
    first, _ = parse_arg(message.text)
    arg = first[len("!клан состав"):].strip()

    async for conn in db():
        if arg:
            if arg.isdigit():
                c = await get_clan(conn, int(arg))
            else:
                c = await get_clan_by_name(conn, arg)
            if not c:
                return await message.reply("❌ Клан не найден.")
        else:
            u = await get_user(conn, message.from_user.id)
            if not u or not u["clan_id"]:
                return await message.reply("❌ Ты не в клане. Укажи: <code>!клан состав Номер</code>", parse_mode=ParseMode.HTML)
            c = await get_clan(conn, u["clan_id"])

        cur = await conn.execute(
            "SELECT user_id, clan_role FROM users WHERE clan_id=? ORDER BY "
            "CASE clan_role WHEN 'leader' THEN 0 WHEN 'officer' THEN 1 ELSE 2 END, user_id",
            (c["id"],),
        )
        members = await cur.fetchall()

    if not members:
        return await message.reply("👥 В клане нет участников.")
    lines = [f"👥 <b>Состав клана {c['name']}</b>\n"]
    for m in members:
        lines.append(f"• <a href=\"tg://user?id={m['user_id']}\">ID{m['user_id']}</a> — {role_ru(m['clan_role'])}")
    await message.reply("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ============================================================
# !клан + [номер]
# ============================================================
@router.message(F.text.regexp(r"^!клан \+"))
async def cmd_join_clan(message: Message):
    first, _ = parse_arg(message.text)
    arg = first.replace("!клан +", "").strip()
    if not arg.isdigit():
        return await message.reply("❌ Укажи номер клана: <code>!клан + 3</code>", parse_mode=ParseMode.HTML)
    clan_id = int(arg)

    async for conn in db():
        if await is_banned(conn, message.from_user.id):
            return await message.reply("🚫 Ты забанен в клановой системе.")
        u = await get_user(conn, message.from_user.id)
        if u and u["clan_id"]:
            return await message.reply("❌ Ты уже в клане.")
        c = await get_clan(conn, clan_id)
        if not c or c["status"] != "active":
            return await message.reply("❌ Клан не найден или заблокирован.")
        cnt = await count_members(conn, clan_id)
        if cnt >= c["max_members"]:
            return await message.reply("❌ В клане нет свободных мест.")

        if c["type"] == "open":
            await conn.execute(
                "INSERT INTO users (user_id, clan_id, clan_role) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET clan_id=excluded.clan_id, clan_role=excluded.clan_role",
                (message.from_user.id, clan_id, "member"),
            )
            await conn.commit()
            return await message.reply(f"✅ Ты вступил в клан <b>{c['name']}</b>!", parse_mode=ParseMode.HTML)

        await conn.execute(
            "INSERT INTO applications (clan_id, user_id, chat_id, created_at) VALUES (?,?,?,?)",
            (clan_id, message.from_user.id, message.chat.id, datetime.utcnow().isoformat()),
        )
        await conn.commit()

    await message.reply(
        f"📩 Заявка в клан <b>{c['name']}</b> отправлена.\n"
        f"Лидер: <a href=\"tg://user?id={c['leader_id']}\">ID{c['leader_id']}</a> — рассмотри заявку от "
        f"<a href=\"tg://user?id={message.from_user.id}\">ID{message.from_user.id}</a>.",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )

# ============================================================
# !клан пригласить
# ============================================================
@router.message(F.text.regexp(r"^!клан пригласить\b"))
async def cmd_invite(message: Message):
    target = extract_target(message)
    if not target:
        return await message.reply("❌ Укажи @user / reply / ID.")
    async for conn in db():
        u = await get_user(conn, message.from_user.id)
        if not u or not u["clan_id"] or u["clan_role"] not in ("leader", "officer"):
            return await message.reply("❌ Только Лидер или Заместитель.")
        if await is_banned(conn, target):
            return await message.reply("🚫 Пользователь забанен в клановой системе.")
        tu = await get_user(conn, target)
        if tu and tu["clan_id"]:
            return await message.reply("❌ Пользователь уже в клане.")
        c = await get_clan(conn, u["clan_id"])
        cnt = await count_members(conn, c["id"])
        if cnt >= c["max_members"]:
            return await message.reply("❌ Клан заполнен.")
        await conn.execute(
            "INSERT INTO users (user_id, clan_id, clan_role) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET clan_id=excluded.clan_id, clan_role=excluded.clan_role",
            (target, c["id"], "member"),
        )
        await conn.commit()
    await message.reply(
        f"✅ <a href=\"tg://user?id={target}\">ID{target}</a> приглашён в клан <b>{c['name']}</b>.",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )

# ============================================================
# !клан исключить / !клан кик
# ============================================================
async def _kick_logic(message: Message, target: int):
    async for conn in db():
        u = await get_user(conn, message.from_user.id)
        if not u or not u["clan_id"]:
            return await message.reply("❌ Ты не в клане.")
        c = await get_clan(conn, u["clan_id"])
        actor_role = u["clan_role"]
        moderate = await can_moderate(message, conn)

        tu = await get_user(conn, target)
        if not tu or tu["clan_id"] != c["id"]:
            return await message.reply("❌ Пользователь не в твоём клане.")

        if not moderate:
            if actor_role not in ("leader", "officer"):
                return await message.reply("❌ Недостаточно прав.")
            if tu["clan_role"] == "leader":
                return await message.reply("❌ Нельзя исключить Лидера.")
            if actor_role == "officer" and tu["clan_role"] == "officer":
                return await message.reply("❌ Заместитель не может исключить другого Заместителя.")

        await conn.execute("UPDATE users SET clan_id=NULL, clan_role=NULL WHERE user_id=?", (target,))
        await conn.commit()
    await message.reply(f"✅ <a href=\"tg://user?id={target}\">ID{target}</a> исключён из клана.", parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@router.message(F.text.regexp(r"^!клан исключить\b"))
async def cmd_kick_clan(message: Message):
    t = extract_target(message)
    if not t:
        return await message.reply("❌ Укажи @user / reply / ID.")
    await _kick_logic(message, t)

@router.message(F.text.regexp(r"^!клан кик\b"))
async def cmd_kick2(message: Message):
    t = extract_target(message)
    if not t:
        return await message.reply("❌ Укажи @user / reply / ID.")
    await _kick_logic(message, t)

# ============================================================
# !клан бан / разбан
# ============================================================
@router.message(F.text.regexp(r"^!клан бан\b"))
async def cmd_ban_clan(message: Message):
    t = extract_target(message)
    if not t:
        return await message.reply("❌ Укажи @user / reply / ID.")
    async for conn in db():
        if not await can_manage_clan(message, conn):
            return await message.reply("❌ Только Лидер/Зам или админ чата.")
        if await is_banned(conn, t):
            return await message.reply("ℹ️ Уже забанен.")
        await conn.execute(
            "INSERT INTO clan_bans (user_id, banned_by, banned_at) VALUES (?,?,?)",
            (t, message.from_user.id, datetime.utcnow().isoformat()),
        )
        await conn.execute("UPDATE users SET clan_id=NULL, clan_role=NULL WHERE user_id=?", (t,))
        await conn.commit()
    await message.reply(f"🚫 <a href=\"tg://user?id={t}\">ID{t}</a> забанен в клановой системе.", parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@router.message(F.text.regexp(r"^!клан разбан\b"))
async def cmd_unban_clan(message: Message):
    t = extract_target(message)
    if not t:
        return await message.reply("❌ Укажи @user / reply / ID.")
    async for conn in db():
        if not await can_manage_clan(message, conn):
            return await message.reply("❌ Только Лидер/Зам или админ чата.")
        await conn.execute("DELETE FROM clan_bans WHERE user_id=?", (t,))
        await conn.commit()
    await message.reply(f"✅ С <a href=\"tg://user?id={t}\">ID{t}</a> снят бан.", parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ============================================================
# !выйти из клана
# ============================================================
@router.message(F.text.regexp(r"^!выйти из клана$"))
async def cmd_leave(message: Message):
    async for conn in db():
        u = await get_user(conn, message.from_user.id)
        if not u or not u["clan_id"]:
            return await message.reply("❌ Ты не в клане.")
        c = await get_clan(conn, u["clan_id"])
        if u["clan_role"] == "leader":
            cnt = await count_members(conn, c["id"])
            if cnt > 1:
                return await message.reply("⚠️ Ты Лидер. Передай права или удали клан: <code>!удалить клан</code>", parse_mode=ParseMode.HTML)
            await conn.execute("DELETE FROM clans WHERE id=?", (c["id"],))
            await conn.execute("UPDATE users SET clan_id=NULL, clan_role=NULL WHERE user_id=?", (message.from_user.id,))
            await conn.commit()
            return await message.reply("🗑 Клан был удалён (ты был единственным участником).")
        await conn.execute("UPDATE users SET clan_id=NULL, clan_role=NULL WHERE user_id=?", (message.from_user.id,))
        await conn.commit()
    await message.reply("✅ Ты покинул клан.")

# ============================================================
# !клан тип
# ============================================================
@router.message(F.text.regexp(r"^!клан тип\b"))
async def cmd_type(message: Message):
    first, _ = parse_arg(message.text)
    arg = first[len("!клан тип"):].strip().lower()
    if arg not in ("открытый", "закрытый"):
        return await message.reply("❌ Используй: <code>!клан тип открытый</code> или <code>!клан тип закрытый</code>", parse_mode=ParseMode.HTML)
    new_type = "open" if arg == "открытый" else "closed"
    async for conn in db():
        u = await get_user(conn, message.from_user.id)
        if not u or not u["clan_id"] or u["clan_role"] != "leader":
            return await message.reply("❌ Только Лидер.")
        await conn.execute("UPDATE clans SET type=? WHERE id=?", (new_type, u["clan_id"]))
        await conn.commit()
    await message.reply(f"✅ Режим вступления: <b>{arg}</b>.", parse_mode=ParseMode.HTML)

# ============================================================
# !клан лимит <число>
# ============================================================
@router.message(F.text.regexp(r"^!клан лимит\b"))
async def cmd_limit(message: Message):
    first, _ = parse_arg(message.text)
    arg = first[len("!клан лимит"):].strip()
    if not arg.isdigit() or int(arg) < 1 or int(arg) > 1000:
        return await message.reply("❌ Укажи число 1–1000: <code>!клан лимит 30</code>", parse_mode=ParseMode.HTML)
    new_max = int(arg)
    async for conn in db():
        u = await get_user(conn, message.from_user.id)
        if not u or not u["clan_id"] or u["clan_role"] != "leader":
            return await message.reply("❌ Только Лидер.")
        cnt = await count_members(conn, u["clan_id"])
        if new_max < cnt:
            return await message.reply(f"❌ Сейчас в клане {cnt} участников — нельзя поставить лимит меньше.")
        await conn.execute("UPDATE clans SET max_members=? WHERE id=?", (new_max, u["clan_id"]))
        await conn.commit()
    await message.reply(f"✅ Лимит участников: <b>{new_max}</b>.", parse_mode=ParseMode.HTML)

# ============================================================
# !клан созвать
# ============================================================
@router.message(F.text.regexp(r"^!клан созвать$"))
async def cmd_call(message: Message):
    async for conn in db():
        u = await get_user(conn, message.from_user.id)
        if not u or not u["clan_id"] or u["clan_role"] != "leader":
            return await message.reply("❌ Только Лидер.")
        c = await get_clan(conn, u["clan_id"])
        cur = await conn.execute("SELECT user_id FROM users WHERE clan_id=?", (c["id"],))
        ids = [r["user_id"] for r in await cur.fetchall()]
    mentions = " ".join(f'<a href="tg://user?id={i}">​</a>' for i in ids)
    await message.reply(f"📢 <b>Сбор клана {c['name']}!</b>\n{mentions}", parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ============================================================
# !удалить клан
# ============================================================
@router.message(F.text.regexp(r"^!удалить клан$"))
async def cmd_delete_clan(message: Message):
    await message.reply("⚠️ Подтверди удаление клана: <code>!удалить клан подтвердить</code>", parse_mode=ParseMode.HTML)

@router.message(F.text.regexp(r"^!удалить клан подтвердить$"))
async def cmd_delete_clan_confirm(message: Message):
    async for conn in db():
        u = await get_user(conn, message.from_user.id)
        if not u or not u["clan_id"] or u["clan_role"] != "leader":
            return await message.reply("❌ Только Лидер может удалить клан.")
        cid = u["clan_id"]
        await conn.execute("UPDATE users SET clan_id=NULL, clan_role=NULL WHERE clan_id=?", (cid,))
        await conn.execute("DELETE FROM clans WHERE id=?", (cid,))
        await conn.execute("DELETE FROM applications WHERE clan_id=?", (cid,))
        await conn.commit()
    await message.reply("🗑 Клан удалён. Все участники свободны.")

# ============================================================
# МОДЕРАТОРСКИЕ ; КОМАНДЫ
# ============================================================
async def _resolve_clan(conn, arg: str):
    if not arg:
        return None
    if arg.isdigit():
        return await get_clan(conn, int(arg))
    return await get_clan_by_name(conn, arg)

@router.message(F.text.regexp(r"^;удалить клан\b"))
async def adm_delete_clan(message: Message):
    async for conn in db():
        if not await can_moderate(message, conn):
            return await message.reply("❌ Только администратор чата или владелец бота.")
        first, _ = parse_arg(message.text)
        arg = first[len(";удалить клан"):].strip()
        c = await _resolve_clan(conn, arg)
        if not c:
            return await message.reply("❌ Клан не найден.")
        await conn.execute("UPDATE users SET clan_id=NULL, clan_role=NULL WHERE clan_id=?", (c["id"],))
        await conn.execute("DELETE FROM clans WHERE id=?", (c["id"],))
        await conn.execute("DELETE FROM applications WHERE clan_id=?", (c["id"],))
        await conn.commit()
    await message.reply(f"🗑 Клан <b>{c['name']}</b> принудительно удалён.", parse_mode=ParseMode.HTML)

@router.message(F.text.regexp(r"^;клан переименовать\b"))
async def adm_rename(message: Message):
    first, _ = parse_arg(message.text)
    rest = first[len(";клан переименовать"):].strip().split()
    if len(rest) < 2 or not rest[0].isdigit():
        return await message.reply("❌ Формат: <code>;клан переименовать 3 НовоеИмя</code>", parse_mode=ParseMode.HTML)
    cid, new_name = int(rest[0]), " ".join(rest[1:])
    async for conn in db():
        if not await can_moderate(message, conn):
            return await message.reply("❌ Недостаточно прав.")
        if not await get_clan(conn, cid):
            return await message.reply("❌ Клан не найден.")
        if await get_clan_by_name(conn, new_name):
            return await message.reply("❌ Имя занято.")
        await conn.execute("UPDATE clans SET name=? WHERE id=?", (new_name, cid))
        await conn.commit()
    await message.reply(f"✅ Клан #{cid} переименован в <b>{new_name}</b>.", parse_mode=ParseMode.HTML)

@router.message(F.text.regexp(r"^;клан заблокировать\b"))
async def adm_block(message: Message):
    first, _ = parse_arg(message.text)
    arg = first[len(";клан заблокировать"):].strip()
    async for conn in db():
        if not await can_moderate(message, conn):
            return await message.reply("❌ Недостаточно прав.")
        c = await _resolve_clan(conn, arg)
        if not c:
            return await message.reply("❌ Клан не найден.")
        await conn.execute("UPDATE clans SET status='banned' WHERE id=?", (c["id"],))
        await conn.commit()
    await message.reply(f"🔒 Клан <b>{c['name']}</b> заблокирован.", parse_mode=ParseMode.HTML)

@router.message(F.text.regexp(r"^;клан разблокировать\b"))
async def adm_unblock(message: Message):
    first, _ = parse_arg(message.text)
    arg = first[len(";клан разблокировать"):].strip()
    async for conn in db():
        if not await can_moderate(message, conn):
            return await message.reply("❌ Недостаточно прав.")
        c = await _resolve_clan(conn, arg)
        if not c:
            return await message.reply("❌ Клан не найден.")
        await conn.execute("UPDATE clans SET status='active' WHERE id=?", (c["id"],))
        await conn.commit()
    await message.reply(f"🔓 Клан <b>{c['name']}</b> разблокирован.", parse_mode=ParseMode.HTML)

@router.message(F.text.regexp(r"^;клан исключить\b"))
async def adm_kick(message: Message):
    t = extract_target(message)
    first, _ = parse_arg(message.text)
    rest = first[len(";клан исключить"):].strip().split()
    if not t or not rest or not rest[0].isdigit():
        return await message.reply("❌ Формат: <code>;клан исключить 3 @user</code>", parse_mode=ParseMode.HTML)
    cid = int(rest[0])
    async for conn in db():
        if not await can_moderate(message, conn):
            return await message.reply("❌ Недостаточно прав.")
        await conn.execute("UPDATE users SET clan_id=NULL, clan_role=NULL WHERE user_id=? AND clan_id=?", (t, cid))
        await conn.commit()
    await message.reply(f"✅ <a href=\"tg://user?id={t}\">ID{t}</a> исключён модератором.", parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@router.message(F.text.regexp(r"^;клан бан\b"))
async def adm_ban(message: Message):
    t = extract_target(message)
    if not t:
        return await message.reply("❌ Укажи @user / reply / ID.")
    async for conn in db():
        if not await can_moderate(message, conn):
            return await message.reply("❌ Недостаточно прав.")
        await conn.execute(
            "INSERT OR IGNORE INTO clan_bans (user_id, banned_by, banned_at) VALUES (?,?,?)",
            (t, message.from_user.id, datetime.utcnow().isoformat()),
        )
        await conn.execute("UPDATE users SET clan_id=NULL, clan_role=NULL WHERE user_id=?", (t,))
        await conn.commit()
    await message.reply(f"🚫 <a href=\"tg://user?id={t}\">ID{t}</a> забанен в клановой системе.", parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@router.message(F.text.regexp(r"^;клан разбан\b"))
async def adm_unban(message: Message):
    t = extract_target(message)
    if not t:
        return await message.reply("❌ Укажи @user / reply / ID.")
    async for conn in db():
        if not await can_moderate(message, conn):
            return await message.reply("❌ Недостаточно прав.")
        await conn.execute("DELETE FROM clan_bans WHERE user_id=?", (t,))
        await conn.commit()
    await message.reply(f"✅ С <a href=\"tg://user?id={t}\">ID{t}</a> снят бан.", parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Нет BOT_TOKEN в переменных окружения")
    await db_init()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    log.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())