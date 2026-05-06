import logging
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from config import Config
from db import Database

logger = logging.getLogger(__name__)

# ── Multi-step command state storage ────────────────────────────
_states: dict[int, dict] = {}


def _get(uid: int):
    return _states.get(uid)


def _set(uid: int, state: dict):
    _states[uid] = state


def _clear(uid: int):
    _states.pop(uid, None)


# ── Helpers ─────────────────────────────────────────────────────
def _admin_only(func):
    """Decorator: only admins may execute."""
    async def wrapper(event, *a, **kw):
        db: Database = kw.get("db")
        if db and not await db.is_admin(event.sender_id):
            return await event.reply("❌ You are not authorized.")
        return await func(event, *a, **kw)
    return wrapper


def _require_userbot(func):
    """Decorator: requires an active userbot client."""
    async def wrapper(event, *a, **kw):
        ub = kw.get("userbot")
        if not ub:
            return await event.reply("❌ Userbot is not connected.\nSet `SESSION_STRING` and restart.")
        return await func(event, *a, **kw)
    return wrapper


# ── Register all handlers ───────────────────────────────────────
def register_handlers(bot: TelegramClient, userbot, db: Database):
    # ---- /start ----
    @bot.on("/start")
    @_admin_only
    async def cmd_start(event, **_):
        await event.reply(
            "👋 **Welcome to AutoPost Bot**\n\n"
            "Use /help to see all commands.\n\n"
            "If you haven't set up a session yet, use /gensession first."
        )

    # ---- /help ----
    @bot.on("/help")
    @_admin_only
    async def cmd_help(event, **_):
        await event.reply(
            "📖 **Commands**\n\n"
            "🔧 **Setup**\n"
            "/gensession — Generate session string\n"
            "/setsource — Set source channel\n"
            "/addchannel — Add destination channel\n"
            "/removechannel — Remove destination channel\n"
            "/addadmin — Add admin (by user ID)\n"
            "/removeadmin — Remove admin\n\n"
            "⚙️ **Configuration**\n"
            "/setlimit — Set daily post limit\n"
            "/settime — Set posting time window\n"
            "/setfooter — Set caption footer\n"
            "/setmode — Set posting mode\n"
            "/setlink — Set link handling\n\n"
            "▶️ **Control**\n"
            "/pause — Pause posting\n"
            "/resume — Resume posting\n"
            "/status — Show status\n"
            "/cancel — Cancel current operation"
        )

    # ---- /gensession ----
    @bot.on("/gensession")
    @_admin_only
    async def cmd_gensession(event, **_):
        if Config.SESSION_STRING:
            return await event.reply("⚠️ Session string is already configured.")
        await event.reply(
            "🔐 **Session String Generator**\n\n"
            "Send your phone number with country code:\n"
            "Example: `+1234567890`"
        )
        _set(event.sender_id, {"cmd": "gensession", "step": "phone"})

    # ---- /setsource ----
    @bot.on("/setsource")
    @_admin_only
    @_require_userbot
    async def cmd_setsource(event, **_):
        await event.reply(
            "📌 **Set Source Channel**\n\n"
            "Send the channel username or ID:\n"
            "Example: `@mysource` or `-1001234567890`"
        )
        _set(event.sender_id, {"cmd": "setsource"})

    # ---- /addchannel ----
    @bot.on("/addchannel")
    @_admin_only
    @_require_userbot
    async def cmd_addchannel(event, **_):
        await event.reply(
            "➕ **Add Destination Channel**\n\n"
            "Send the channel username or ID:\n"
            "Example: `@mydest` or `-1001234567890`"
        )
        _set(event.sender_id, {"cmd": "addchannel"})

    # ---- /removechannel ----
    @bot.on("/removechannel")
    @_admin_only
    async def cmd_removechannel(event, **_):
        channels = await db.get_all_channels()
        if not channels:
            return await event.reply("❌ No channels added yet.")
        lines = []
        for i, ch in enumerate(channels, 1):
            lines.append(f"{i}. {ch.get('channel_name', ch['channel_id'])} (`{ch['channel_id']}`)")
        await event.reply(
            "➖ **Remove Channel**\n\nSend the channel ID to remove:\n\n" + "\n".join(lines)
        )
        _set(event.sender_id, {"cmd": "removechannel"})

    # ---- /addadmin ----
    @bot.on("/addadmin")
    @_admin_only
    async def cmd_addadmin(event, **_):
        await event.reply("👤 Send the **user ID** to add as admin:")
        _set(event.sender_id, {"cmd": "addadmin"})

    # ---- /removeadmin ----
    @bot.on("/removeadmin")
    @_admin_only
    async def cmd_removeadmin(event, **_):
        admins = await db.get_admins()
        lines = [f"• `{a['user_id']}` — {a.get('name', 'Admin')}" for a in admins]
        await event.reply(
            "🗑 **Remove Admin**\n\nSend the user ID to remove:\n\n" + "\n".join(lines)
        )
        _set(event.sender_id, {"cmd": "removeadmin"})

    # ---- /setlimit ----
    @bot.on("/setlimit")
    @_admin_only
    async def cmd_setlimit(event, **_):
        channels = await db.get_all_channels()
        if not channels:
            return await event.reply("❌ No channels added.")
        lines = []
        for ch in channels:
            lim = ch.get("daily_limit", 50)
            lines.append(f"• {ch.get('channel_name', ch['channel_id'])} (`{ch['channel_id']}`) — current: {lim}")
        await event.reply(
            "🔢 **Set Daily Limit**\n\nSend in format:\n`channel_id limit`\n\n" + "\n".join(lines)
        )
        _set(event.sender_id, {"cmd": "setlimit"})

    # ---- /settime ----
    @bot.on("/settime")
    @_admin_only
    async def cmd_settime(event, **_):
        await event.reply(
            "⏱ **Set Posting Time Window**\n\n"
            "Send in format: `start-end` (hours, 0-23)\n"
            "Example: `9-21` (posts from 9 AM to 9 PM)\n"
            "Send `off` to disable time restriction."
        )
        _set(event.sender_id, {"cmd": "settime"})

    # ---- /setfooter ----
    @bot.on("/setfooter")
    @_admin_only
    async def cmd_setfooter(event, **_):
        await event.reply(
            "✏️ **Set Caption Footer**\n\n"
            "Send the footer text to append to every post.\n"
            "Send `none` to remove footer."
        )
        _set(event.sender_id, {"cmd": "setfooter"})

    # ---- /setmode ----
    @bot.on("/setmode")
    @_admin_only
    async def cmd_setmode(event, **_):
        await event.reply(
            "📋 **Set Posting Mode**\n\n"
            "Send one of:\n"
            "`forward` — Forward messages as-is\n"
            "`copy` — Copy media with modified caption\n"
            "`text_only` — Send text only (no media)"
        )
        _set(event.sender_id, {"cmd": "setmode"})

    # ---- /setlink ----
    @bot.on("/setlink")
    @_admin_only
    async def cmd_setlink(event, **_):
        await event.reply(
            "🔗 **Set Link Handling**\n\n"
            "Send one of:\n"
            "`keep` — Keep all links\n"
            "`remove` — Remove t.me links\n"
            "`replace` — Replace t.me links (you'll be asked for URL)"
        )
        _set(event.sender_id, {"cmd": "setlink"})

    # ---- /pause ----
    @bot.on("/pause")
    @_admin_only
    async def cmd_pause(event, **_):
        await db.update_settings({"is_paused": True})
        await event.reply("⏸ Posting **paused**.")

    # ---- /resume ----
    @bot.on("/resume")
    @_admin_only
    async def cmd_resume(event, **_):
        await db.update_settings({"is_paused": False})
        await event.reply("▶️ Posting **resumed**.")

    # ---- /status ----
    @bot.on("/status")
    @_admin_only
    async def cmd_status(event, **_):
        s = await db.get_settings()
        channels = await db.get_all_channels()
        tracking = {}

        src_id = s.get("source_channel")
        if src_id:
            tracking = await db.get_post_tracking(src_id)

        mode = s.get("posting_mode", "copy")
        link_mode = s.get("link_mode", "keep")
        footer = s.get("footer", "")
        paused = s.get("is_paused", False)
        loop = s.get("loop_enabled", False)
        t_start = s.get("time_start")
        t_end = s.get("time_end")

        status_icon = "⏸ Paused" if paused else "▶️ Running"
        time_str = f"{t_start}:00 — {t_end}:00" if t_start is not None else "Always"
        src_name = s.get("source_name", "Not set")

        lines = [
            "📊 **Bot Status**\n",
            f"🔸 **Source**: {src_name}",
            f"🔸 **Destinations**: {len(channels)}",
            f"🔸 **Mode**: {mode}",
            f"🔸 **Links**: {link_mode}",
            f"🔸 **Footer**: {footer if footer else 'None'}",
            f"🔸 **Time Window**: {time_str}",
            f"🔸 **Loop**: {'ON' if loop else 'OFF'}",
            f"🔸 **Status**: {status_icon}",
        ]

        if tracking:
            lines.append(f"🔸 **Pointer**: {tracking.get('current_id', '?')}")
            lines.append(f"🔸 **Start ID**: {tracking.get('start_id', '?')}")

        if channels:
            lines.append("\n📋 **Channel Limits**:")
            for ch in channels:
                cnt = await db.get_daily_count(ch["channel_id"])
                lim = ch.get("daily_limit", 50)
                name = ch.get("channel_name", str(ch["channel_id"]))
                lines.append(f"├ {name}: {cnt}/{lim} today")

        await event.reply("\n".join(lines))

    # ---- /cancel ----
    @bot.on("/cancel")
    async def cmd_cancel(event, **_):
        _clear(event.sender_id)
        await event.reply("✅ Operation cancelled.")

    # ── Generic message handler (multi-step flows) ─────────────
    @bot.on(lambda e: e.is_private and e.text and not e.text.startswith("/"))
    async def on_private_message(event):
        uid = event.sender_id
        state = _get(uid)
        if not state:
            return

        text = (event.text or "").strip()
        cmd = state["cmd"]

        try:
            if cmd == "gensession":
                await _handle_gensession(event, state, text)
            elif cmd == "setsource":
                await _handle_setsource(event, userbot, db, text)
            elif cmd == "addchannel":
                await _handle_addchannel(event, userbot, db, text)
            elif cmd == "removechannel":
                await _handle_removechannel(event, db, text)
            elif cmd == "addadmin":
                await _handle_addadmin(event, db, text)
            elif cmd == "removeadmin":
                await _handle_removeadmin(event, db, text)
            elif cmd == "setlimit":
                await _handle_setlimit(event, db, text)
            elif cmd == "settime":
                await _handle_settime(event, db, text)
            elif cmd == "setfooter":
                await _handle_setfooter(event, db, text)
            elif cmd == "setmode":
                await _handle_setmode(event, db, text)
            elif cmd == "setlink":
                await _handle_setlink(event, db, state, text)
        except Exception as exc:
            logger.error(f"Handler error for {cmd}: {exc}", exc_info=True)
            _clear(uid)
            await event.reply(f"❌ Error: {exc}")


# ── Multi-step handler implementations ──────────────────────────

async def _handle_gensession(event, state: dict, text: str):
    uid = event.sender_id
    step = state["step"]

    if step == "phone":
        phone = text
        await event.reply("⏳ Connecting to Telegram...")
        try:
            from telethon.sessions import StringSession
            temp_session = StringSession()
            client = TelegramClient(temp_session, Config.API_ID, Config.API_HASH)
            await client.connect()
            result = await client.send_code_request(phone)
            _set(uid, {
                "cmd": "gensession", "step": "code",
                "phone": phone, "client": client,
                "phone_code_hash": result.phone_code_hash,
            })
            await event.reply("📱 Verification code sent!\n\nSend the code (digits only):")
        except Exception as e:
            _clear(uid)
            await event.reply(f"❌ Failed to send code: {e}")

    elif step == "code":
        code = text.replace(" ", "").replace("-", "")
        client = state["client"]
        try:
            await client.sign_in(
                phone=state["phone"], code=code,
                phone_code_hash=state["phone_code_hash"],
            )
            session_str = client.session.save()
            await client.disconnect()
            _clear(uid)
            await event.reply(
                "✅ **Session generated!**\n\n"
                f"`{session_str}`\n\n"
                "Copy this string, set it as `SESSION_STRING`, then redeploy."
            )
        except SessionPasswordNeededError:
            _set(uid, {
                "cmd": "gensession", "step": "2fa",
                "phone": state["phone"], "client": client,
                "phone_code_hash": state["phone_code_hash"], "code": code,
            })
            await event.reply("🔐 Two-factor auth enabled.\nSend your password:")
        except Exception as e:
            _clear(uid)
            try:
                await client.disconnect()
            except Exception:
                pass
            await event.reply(f"❌ Sign-in failed: {e}")

    elif step == "2fa":
        client = state["client"]
        try:
            await client.sign_in(password=text)
            session_str = client.session.save()
            await client.disconnect()
            _clear(uid)
            await event.reply(
                "✅ **Session generated!**\n\n"
                f"`{session_str}`\n\n"
                "Copy this string, set it as `SESSION_STRING`, then redeploy."
            )
        except Exception as e:
            _clear(uid)
            try:
                await client.disconnect()
            except Exception:
                pass
            await event.reply(f"❌ 2FA failed: {e}")


async def _handle_setsource(event, userbot, db: Database, text: str):
    uid = event.sender_id
    try:
        entity = await userbot.get_entity(text)
        cid = entity.id
        name = getattr(entity, "title", None) or text

        # Reset tracking for previous source
        old = await db.get_settings()
        old_src = old.get("source_channel")
        if old_src:
            await db.delete_post_tracking(old_src)

        await db.update_settings({"source_channel": cid, "source_name": name})
        _clear(uid)
        await event.reply(f"✅ Source set to **{name}** (`{cid}`)")
    except Exception as e:
        await event.reply(f"❌ Could not resolve channel: {e}")


async def _handle_addchannel(event, userbot, db: Database, text: str):
    uid = event.sender_id
    try:
        entity = await userbot.get_entity(text)
        cid = entity.id
        name = getattr(entity, "title", None) or text
        if await db.channel_exists(cid):
            _clear(uid)
            return await event.reply("⚠️ This channel is already added.")
        await db.add_channel(cid, name)
        _clear(uid)
        await event.reply(f"✅ Destination added: **{name}**\nDefault limit: 50 posts/day")
    except Exception as e:
        await event.reply(f"❌ Could not resolve channel: {e}")


async def _handle_removechannel(event, db: Database, text: str):
    uid = event.sender_id
    try:
        cid = int(text.strip())
        await db.remove_channel(cid)
        _clear(uid)
        await event.reply(f"✅ Channel `{cid}` removed.")
    except ValueError:
        await event.reply("❌ Invalid channel ID.")


async def _handle_addadmin(event, db: Database, text: str):
    uid = event.sender_id
    try:
        admin_id = int(text.strip())
        await db.add_admin(admin_id)
        _clear(uid)
        await event.reply(f"✅ Admin added: `{admin_id}`")
    except ValueError:
        await event.reply("❌ Invalid user ID.")


async def _handle_removeadmin(event, db: Database, text: str):
    uid = event.sender_id
    try:
        admin_id = int(text.strip())
        if admin_id == Config.OWNER_ID:
            _clear(uid)
            return await event.reply("❌ Cannot remove the owner.")
        await db.remove_admin(admin_id)
        _clear(uid)
        await event.reply(f"✅ Admin removed: `{admin_id}`")
    except ValueError:
        await event.reply("❌ Invalid user ID.")


async def _handle_setlimit(event, db: Database, text: str):
    uid = event.sender_id
    parts = text.split()
    if len(parts) != 2:
        return await event.reply("❌ Format: `channel_id limit`")
    try:
        cid = int(parts[0])
        limit = int(parts[1])
        if limit < 1:
            return await event.reply("❌ Limit must be at least 1.")
        await db.set_channel_limit(cid, limit)
        _clear(uid)
        await event.reply(f"✅ Daily limit for `{cid}` set to **{limit}**")
    except ValueError:
        await event.reply("❌ Invalid numbers.")


async def _handle_settime(event, db: Database, text: str):
    uid = event.sender_id
    if text.lower() == "off":
        await db.update_settings({"time_start": None, "time_end": None})
        _clear(uid)
        return await event.reply("✅ Time restriction disabled. Posting 24/7.")

    try:
        parts = text.split("-")
        if len(parts) != 2:
            raise ValueError
        start_h = int(parts[0])
        end_h = int(parts[1])
        if not (0 <= start_h <= 23 and 0 <= end_h <= 23):
            raise ValueError
        await db.update_settings({"time_start": start_h, "time_end": end_h})
        _clear(uid)
        await event.reply(f"✅ Time window set: **{start_h}:00 — {end_h}:00**")
    except (ValueError, IndexError):
        await event.reply("❌ Invalid format. Use `start-end` (e.g. `9-21`) or `off`")


async def _handle_setfooter(event, db: Database, text: str):
    uid = event.sender_id
    if text.lower() == "none":
        await db.update_settings({"footer": ""})
        _clear(uid)
        return await event.reply("✅ Footer removed.")
    await db.update_settings({"footer": text})
    _clear(uid)
    await event.reply(f"✅ Footer set to:\n{text}")


async def _handle_setmode(event, db: Database, text: str):
    uid = event.sender_id
    mode = text.lower().strip()
    valid = {"forward", "copy", "text_only"}
    if mode not in valid:
        return await event.reply(f"❌ Invalid mode. Choose from: {', '.join(valid)}")
    await db.update_settings({"posting_mode": mode})
    _clear(uid)
    await event.reply(f"✅ Posting mode set to **{mode}**")


async def _handle_setlink(event, db: Database, state: dict, text: str):
    uid = event.sender_id
    mode = text.lower().strip()
    valid = {"keep", "remove", "replace"}
    if mode not in valid:
        return await event.reply(f"❌ Invalid option. Choose from: {', '.join(valid)}")

    if mode == "replace":
        _set(uid, {"cmd": "setlink", "step": "url"})
        await event.reply("🔗 Send the replacement URL:")
        return

    await db.update_settings({"link_mode": mode})
    _clear(uid)
    await event.reply(f"✅ Link mode set to **{mode}**")

    # Handle the URL step (called when step == "url")
    if state.get("step") == "url":
        url = text.strip()
        await db.update_settings({"link_mode": "replace", "replace_link": url})
        _clear(uid)
        await event.reply(f"✅ Links will be replaced with:\n{url}")
