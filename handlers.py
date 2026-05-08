import re
import logging
from telethon import events, TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from config import Config
from db import Database

logger = logging.getLogger(__name__)

# ── Multi-step state ────────────────────────────────────────────
_states: dict[int, dict] = {}


def _get(uid):
    return _states.get(uid)


def _set(uid, state):
    _states[uid] = state


def _clear(uid):
    _states.pop(uid, None)


def _clean(text: str) -> str:
    return re.sub(r"[*_~`>\[\]()|]", "", text).strip().lower()


def _to_input(text: str):
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        return text


async def _require_setup(event, db: Database):
    uid = event.sender_id
    sid = await db.get_selected_setup(uid)
    if not sid:
        await event.reply(
            "❌ No setup selected.\n"
            "Use /newsetup or /setups."
        )
        return None, None
    setup = await db.get_setup(sid)
    if not setup:
        await db.clear_selected_setup(uid)
        await event.reply("❌ Setup was deleted. Use /newsetup.")
        return None, None
    return sid, setup


async def _resolve_entity(client, text: str):
    """Resolve a channel from text, link, or numeric ID.

    Priority:
      1. t.me/c/... links  (private channels — most reliable)
      2. t.me/... links    (public channels)
      3. @username
      4. Numeric ID as int (private channels — works if user is member)
    """
    text = text.strip()

    # Method 1: Any t.me link (handles both public and private)
    if "t.me/" in text:
        try:
            return await client.get_entity(text)
        except Exception:
            pass  # Fall through to other methods

    # Method 2: Username
    if text.startswith("@"):
        try:
            return await client.get_entity(text)
        except Exception:
            pass

    # Method 3: Numeric ID as int
    input_val = _to_input(text)
    if isinstance(input_val, int):
        try:
            return await client.get_entity(input_val)
        except Exception:
            pass

    # Method 4: Last resort — try raw string (handles edge cases)
    try:
        return await client.get_entity(text)
    except Exception:
        pass

    raise ValueError(
        "Could not resolve channel. Try one of these:\n"
        "• Forward a message from the channel here\n"
        "• Send a message link: `https://t.me/c/xxxxx/123`\n"
        "• Send `@username` (public)\n"
        "• Send `-100xxxxxxxxx` (numeric ID)"
    )


def _extract_channel_info(entity) -> tuple[int, str]:
    """Get (id, display_name) from any entity."""
    cid = entity.id
    name = getattr(entity, "title", None)
    if not name:
        name = getattr(entity, "first_name", None) or str(cid)
    return cid, name


# ═══════════════════════════════════════════════════════════════
#  REGISTER ALL HANDLERS
# ═══════════════════════════════════════════════════════════════
def register_handlers(bot: TelegramClient, userbot, db: Database,
                      poster=None):

    # ──────────────── /start ────────────────
    @bot.on(events.NewMessage(pattern=r"/start(?!\S)"))
    async def cmd_start(event):
        if not await db.is_admin(event.sender_id):
            return
        await event.reply(
            "👋 **AutoPost Bot — Multi-Setup**\n\n"
            "Use /help to see all commands."
        )

    # ──────────────── /help ────────────────
    @bot.on(events.NewMessage(pattern=r"/help(?!\S)"))
    async def cmd_help(event):
        if not await db.is_admin(event.sender_id):
            return
        await event.reply(
            "📖 **Commands**\n\n"
            "📦 **Setups**\n"
            "/newsetup — Create a new setup\n"
            "/setups — List all setups\n"
            "/select _id_ — Select a setup\n"
            "/delsetup _id_ — Delete a setup\n\n"
            "📌 **Per-Setup Config** (select first)\n"
            "/setsource — Set source channel\n"
            "/addchannel — Add destination channel\n"
            "/removechannel — Remove destination\n"
            "/setlimit _ch_id limit_ — Daily limit\n"
            "/settime — Posting time window\n"
            "/setfooter — Caption footer\n"
            "/setmode _mode_ — forward / copy / text_only\n"
            "/setlink _mode_ — keep / remove / replace\n"
            "/loop — Toggle loop mode\n"
            "/pause — Pause selected setup\n"
            "/resume — Resume selected setup\n\n"
            "🔧 **System**\n"
            "/gensession — Generate session string\n"
            "/addadmin — Add admin\n"
            "/removeadmin — Remove admin\n"
            "/status — Full overview\n"
            "/cancel — Cancel operation\n\n"
            "💡 **Private Channels?**\n"
            "Forward any message from the channel,\n"
            "or send its message link `t.me/c/…/…`"
        )

    # ──────────────── /newsetup ────────────────
    @bot.on(events.NewMessage(pattern=r"/newsetup(?!\S)"))
    async def cmd_newsetup(event):
        if not await db.is_admin(event.sender_id):
            return
        sid = await db.create_setup()
        await db.set_selected_setup(event.sender_id, sid)
        await event.reply(
            f"✅ **Setup #{sid}** created & selected.\n\n"
            f"1. /setsource\n"
            f"2. /addchannel\n"
            f"3. /setmode, /setfooter …"
        )

    # ──────────────── /setups ────────────────
    @bot.on(events.NewMessage(pattern=r"/setups(?!\S)"))
    async def cmd_setups(event):
        if not await db.is_admin(event.sender_id):
            return
        setups = await db.get_all_setups()
        if not setups:
            return await event.reply("📭 No setups. /newsetup")
        sel = await db.get_selected_setup(event.sender_id)
        lines = []
        for s in setups:
            marker = " ◀️" if s["setup_id"] == sel else ""
            src = s.get("source_name") or "No source"
            dsts = len(s.get("destinations", []))
            icon = "⏸" if s.get("is_paused") else "▶️"
            lines.append(
                f"**#{s['setup_id']}**{marker}{icon}  "
                f"Src: {src}  →  {dsts} dest(s)"
            )
        await event.reply("📦 **Setups**\n\n" + "\n".join(lines))

    # ──────────────── /select ────────────────
    @bot.on(events.NewMessage(pattern=r"/select\s+(\d+)(?!\S)"))
    async def cmd_select(event):
        if not await db.is_admin(event.sender_id):
            return
        sid = int(event.pattern_match.group(1))
        setup = await db.get_setup(sid)
        if not setup:
            return await event.reply(f"❌ Setup #{sid} not found.")
        await db.set_selected_setup(event.sender_id, sid)
        src = setup.get("source_name") or "No source"
        dsts = len(setup.get("destinations", []))
        await event.reply(
            f"✅ Selected **Setup #{sid}**\n"
            f"📌 Source: {src}\n📤 Destinations: {dsts}"
        )

    # ──────────────── /delsetup ────────────────
    @bot.on(events.NewMessage(pattern=r"/delsetup\s+(\d+)(?!\S)"))
    async def cmd_delsetup(event):
        if not await db.is_admin(event.sender_id):
            return
        sid = int(event.pattern_match.group(1))
        if not await db.get_setup(sid):
            return await event.reply(f"❌ Setup #{sid} not found.")
        await db.delete_setup(sid)
        if poster:
            await poster.stop_setup(sid)
        sel = await db.get_selected_setup(event.sender_id)
        if sel == sid:
            await db.clear_selected_setup(event.sender_id)
        await event.reply(f"🗑 Setup #{sid} deleted.")

    # ──────────────── /setsource ────────────────
    @bot.on(events.NewMessage(pattern=r"/setsource(?!\S)"))
    async def cmd_setsource(event):
        if not await db.is_admin(event.sender_id):
            return
        if not userbot:
            return await event.reply("❌ Userbot not connected.")
        sid, _ = await _require_setup(event, db)
        if sid is None:
            return
        await event.reply(
            f"📌 **Setup #{sid}** — Set Source\n\n"
            "Send one of:\n"
            "• Forward a message from the source\n"
            "• A message link: `https://t.me/c/xxxx/123`\n"
            "• `@username` or `-100xxxxxxxxx`"
        )
        _set(event.sender_id, {"cmd": "setsource", "setup_id": sid})

    # ──────────────── /addchannel ────────────────
    @bot.on(events.NewMessage(pattern=r"/addchannel(?!\S)"))
    async def cmd_addchannel(event):
        if not await db.is_admin(event.sender_id):
            return
        if not userbot:
            return await event.reply("❌ Userbot not connected.")
        sid, _ = await _require_setup(event, db)
        if sid is None:
            return
        await event.reply(
            f"➕ **Setup #{sid}** — Add Destination\n\n"
            "Send one of:\n"
            "• Forward a message from the channel\n"
            "• A message link: `https://t.me/c/xxxx/123`\n"
            "• `@username` or `-100xxxxxxxxx`"
        )
        _set(event.sender_id, {"cmd": "addchannel", "setup_id": sid})

    # ──────────────── /removechannel ────────────────
    @bot.on(events.NewMessage(pattern=r"/removechannel(?!\S)"))
    async def cmd_removechannel(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, setup = await _require_setup(event, db)
        if sid is None:
            return
        dsts = setup.get("destinations", [])
        if not dsts:
            return await event.reply("❌ No destinations.")
        lines = []
        for d in dsts:
            lines.append(
                f"• {d.get('channel_name', d['channel_id'])} "
                f"(`{d['channel_id']}`)"
            )
        await event.reply(
            f"➖ **Setup #{sid}** — Remove\n\n"
            "Send channel ID:\n\n" + "\n".join(lines)
        )
        _set(event.sender_id, {"cmd": "removechannel", "setup_id": sid})

    # ──────────────── /setlimit ────────────────
    @bot.on(events.NewMessage(pattern=r"/setlimit\s+(\S+)\s+(\d+)(?!\S)"))
    async def cmd_setlimit(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db)
        if sid is None:
            return
        cid = int(event.pattern_match.group(1))
        limit = int(event.pattern_match.group(2))
        if limit < 1:
            return await event.reply("❌ Limit must be ≥ 1.")
        await db.set_destination_limit(sid, cid, limit)
        await event.reply(f"✅ `{cid}` limit → **{limit}**")

    # ──────────────── /settime ────────────────
    @bot.on(events.NewMessage(pattern=r"/settime(?!\S)"))
    async def cmd_settime(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db)
        if sid is None:
            return
        await event.reply(
            f"⏱ **Setup #{sid}** — Time Window\n\n"
            "`9-21` or `off` for 24/7"
        )
        _set(event.sender_id, {"cmd": "settime", "setup_id": sid})

    # ──────────────── /setfooter ────────────────
    @bot.on(events.NewMessage(pattern=r"/setfooter(?!\S)"))
    async def cmd_setfooter(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db)
        if sid is None:
            return
        await event.reply(
            f"✏️ **Setup #{sid}** — Footer\n\n"
            "Send text, or `none` to remove."
        )
        _set(event.sender_id, {"cmd": "setfooter", "setup_id": sid})

    # ──────────────── /setmode one-shot ────────────────
    @bot.on(events.NewMessage(pattern=r"/setmode\s+(\S+)(?!\S)"))
    async def cmd_setmode_oneshot(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db)
        if sid is None:
            return
        mode = _clean(event.pattern_match.group(1))
        if mode not in {"forward", "copy", "text_only"}:
            return await event.reply("❌ Use: forward, copy, or text_only")
        await db.update_setup(sid, {"posting_mode": mode})
        await event.reply(f"✅ Setup #{sid} mode → **{mode}**")

    # ──────────────── /setmode interactive ────────────────
    @bot.on(events.NewMessage(pattern=r"/setmode(?!\S)"))
    async def cmd_setmode_interactive(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, setup = await _require_setup(event, db)
        if sid is None:
            return
        cur = (setup or {}).get("posting_mode", "copy")
        await event.reply(
            f"📋 **Setup #{sid}** — Mode\n\n"
            f"Current: **{cur}**\n\n"
            "Send: forward / copy / text_only"
        )
        _set(event.sender_id, {"cmd": "setmode", "setup_id": sid})

    # ──────────────── /setlink one-shot ────────────────
    @bot.on(events.NewMessage(pattern=r"/setlink\s+(\S+)(?!\S)"))
    async def cmd_setlink_oneshot(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db)
        if sid is None:
            return
        mode = _clean(event.pattern_match.group(1))
        if mode not in {"keep", "remove", "replace"}:
            return await event.reply("❌ Use: keep, remove, or replace")
        if mode == "replace":
            await event.reply("🔗 Send the replacement URL:")
            _set(event.sender_id, {
                "cmd": "setlink", "step": "url", "setup_id": sid
            })
            return
        await db.update_setup(sid, {"link_mode": mode})
        await event.reply(f"✅ Setup #{sid} links → **{mode}**")

    # ──────────────── /setlink interactive ────────────────
    @bot.on(events.NewMessage(pattern=r"/setlink(?!\S)"))
    async def cmd_setlink_interactive(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db)
        if sid is None:
            return
        await event.reply(
            f"🔗 **Setup #{sid}** — Links\n\n"
            "Send: keep / remove / replace"
        )
        _set(event.sender_id, {
            "cmd": "setlink", "step": "mode", "setup_id": sid
        })

    # ──────────────── /loop ────────────────
    @bot.on(events.NewMessage(pattern=r"/loop(?!\S)"))
    async def cmd_loop(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, setup = await _require_setup(event, db)
        if sid is None:
            return
        new = not (setup or {}).get("loop_enabled", False)
        await db.update_setup(sid, {"loop_enabled": new})
        await event.reply(f"🔄 Setup #{sid} loop → {'ON ✅' if new else 'OFF ❌'}")

    # ──────────────── /pause ────────────────
    @bot.on(events.NewMessage(pattern=r"/pause(?!\S)"))
    async def cmd_pause(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db)
        if sid is None:
            return
        await db.update_setup(sid, {"is_paused": True})
        if poster:
            await poster.stop_setup(sid)
        await event.reply(f"⏸ Setup #{sid} **paused**.")

    # ──────────────── /resume ────────────────
    @bot.on(events.NewMessage(pattern=r"/resume(?!\S)"))
    async def cmd_resume(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, setup = await _require_setup(event, db)
        if sid is None:
            return
        if not (setup or {}).get("source_channel"):
            return await event.reply("❌ Set source first: /setsource")
        await db.update_setup(sid, {"is_paused": False})
        if poster:
            await poster.start_setup(sid)
        await event.reply(f"▶️ Setup #{sid} **resumed**.")

    # ──────────────── /status ────────────────
    @bot.on(events.NewMessage(pattern=r"/status(?!\S)"))
    async def cmd_status(event):
        if not await db.is_admin(event.sender_id):
            return
        setups = await db.get_all_setups()
        sel = await db.get_selected_setup(event.sender_id)
        if not setups:
            return await event.reply("📭 No setups. /newsetup")

        blocks = []
        for s in setups:
            marker = " ◀️" if s["setup_id"] == sel else ""
            icon = "⏸" if s.get("is_paused") else "▶️"
            src = s.get("source_name") or "Not set"
            mode = s.get("posting_mode", "copy")
            link = s.get("link_mode", "keep")
            footer = s.get("footer", "") or "None"
            loop = "ON" if s.get("loop_enabled") else "OFF"
            ts, te = s.get("time_start"), s.get("time_end")
            tw = f"{ts}:00–{te}:00" if ts is not None else "24/7"
            dsts = s.get("destinations", [])

            lines = [
                f"{'━' * 28}",
                f"{icon} **Setup #{s['setup_id']}**{marker}",
                f"📌 Source: {src}",
                f"📋 Mode: {mode}  |  🔗 Links: {link}",
                f"✏️ Footer: {footer}  |  🔄 Loop: {loop}",
                f"⏱ Window: {tw}",
                f"📤 Destinations: {len(dsts)}",
            ]
            if dsts:
                lines.append("")
                for d in dsts:
                    cnt = await db.get_daily_count(
                        s["setup_id"], d["channel_id"]
                    )
                    lim = d.get("daily_limit", 50)
                    nm = d.get("channel_name", str(d["channel_id"]))
                    filled = min(int(cnt / max(lim, 1) * 10), 10)
                    bar = "█" * filled + "░" * (10 - filled)
                    lines.append(f"  └ {nm}: [{bar}] {cnt}/{lim}")

            src_id = s.get("source_channel")
            if src_id:
                trk = await db.get_post_tracking(s["setup_id"], src_id)
                if trk:
                    lines.append(
                        f"  📍 Ptr: {trk.get('current_id', '?')} "
                        f"(start: {trk.get('start_id', '?')})"
                    )
            blocks.append("\n".join(lines))

        await event.reply("\n".join(blocks))

    # ──────────────── /gensession ────────────────
    @bot.on(events.NewMessage(pattern=r"/gensession(?!\S)"))
    async def cmd_gensession(event):
        if not await db.is_admin(event.sender_id):
            return
        if Config.SESSION_STRING:
            return await event.reply("⚠️ Session already configured.")
        await event.reply(
            "🔐 **Session Generator**\n\n"
            "Send phone with country code:\n`+1234567890`"
        )
        _set(event.sender_id, {"cmd": "gensession", "step": "phone"})

    # ──────────────── /addadmin ────────────────
    @bot.on(events.NewMessage(pattern=r"/addadmin(?!\S)"))
    async def cmd_addadmin(event):
        if not await db.is_admin(event.sender_id):
            return
        await event.reply("👤 Send the **user ID** to add:")
        _set(event.sender_id, {"cmd": "addadmin"})

    # ──────────────── /removeadmin ────────────────
    @bot.on(events.NewMessage(pattern=r"/removeadmin(?!\S)"))
    async def cmd_removeadmin(event):
        if not await db.is_admin(event.sender_id):
            return
        admins = await db.get_admins()
        lines = [
            f"• `{a['user_id']}` — {a.get('name', 'Admin')}"
            for a in admins
        ]
        await event.reply("🗑 Send user ID to remove:\n\n" + "\n".join(lines))
        _set(event.sender_id, {"cmd": "removeadmin"})

    # ──────────────── /cancel ────────────────
    @bot.on(events.NewMessage(pattern=r"/cancel(?!\S)"))
    async def cmd_cancel(event):
        _clear(event.sender_id)
        await event.reply("✅ Cancelled.")

    # ═══════════════════════════════════════════════════════════
    #  GENERIC PRIVATE MESSAGE
    #  Handles: text input, forwarded messages, links
    #     #  Handles: text input, forwarded messages, links
    # ═══════════════════════════════════════════════════════════
    @bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_private_msg(event):
        uid = event.sender_id
        state = _get(uid)
        if not state:
            return

        cmd = state["cmd"]
        sid = state.get("setup_id")

        try:
            # ── Forwarded message handler ──────────────────
            if event.forward:
                fwd = event.forward
                if cmd == "setsource":
                    await _apply_source(event, userbot, db, sid, fwd)
                    return
                if cmd == "addchannel":
                    await _apply_destination(event, userbot, db, sid, fwd)
                    return

            # ── Text handler ───────────────────────────────
            text = (event.text or "").strip()
            if not text:
                return
            if text.startswith("/"):
                return

            if cmd == "gensession":
                await _flow_gensession(event, state, text)
            elif cmd == "setsource":
                await _flow_setsource(event, userbot, db, sid, text)
            elif cmd == "addchannel":
                await _flow_addchannel(event, userbot, db, sid, text)
            elif cmd == "removechannel":
                await _flow_removechannel(event, db, sid, text)
            elif cmd == "settime":
                await _flow_settime(event, db, sid, text)
            elif cmd == "setfooter":
                await _flow_setfooter(event, db, sid, text)
            elif cmd == "setmode":
                await _flow_setmode(event, db, sid, text)
            elif cmd == "setlink":
                await _flow_setlink(event, db, state, text)
            elif cmd == "addadmin":
                await _flow_addadmin(event, db, text)
            elif cmd == "removeadmin":
                await _flow_removeadmin(event, db, text)

        except Exception as exc:
            logger.error(f"Flow [{cmd}]: {exc}", exc_info=True)
            _clear(uid)
            await event.reply(f"❌ Error: {exc}")


# ═══════════════════════════════════════════════════════════════
#  FORWARDED MESSAGE HANDLERS (most reliable for private channels)
# ═══════════════════════════════════════════════════════════════

async def _apply_source(event, userbot, db, sid, fwd):
    """Set source from a forwarded message."""
    uid = event.sender_id

    # Try to get chat entity from forward info
    chat_id = fwd.chat_id
    if not chat_id:
        # Fallback: try sender_id for channels that forward as user
        chat_id = fwd.sender_id
    if not chat_id:
        _clear(uid)
        return await event.reply(
            "❌ Could not identify channel from this forward.\n"
            "Try sending a message link instead."
        )

    try:
        entity = await userbot.get_entity(chat_id)
        cid, name = _extract_channel_info(entity)

        # Reset old tracking
        old = await db.get_setup(sid)
        if old and old.get("source_channel"):
            await db.delete_post_tracking(sid, old["source_channel"])

        await db.update_setup(sid, {
            "source_channel": cid, "source_name": name,
        })
        _clear(uid)
        await event.reply(
            f"✅ Setup #{sid} source → **{name}**\n"
            f"`{cid}`"
        )
    except Exception as e:
        _clear(uid)
        await event.reply(f"❌ Could not resolve: {e}")


async def _apply_destination(event, userbot, db, sid, fwd):
    """Add destination from a forwarded message."""
    uid = event.sender_id

    chat_id = fwd.chat_id
    if not chat_id:
        chat_id = fwd.sender_id
    if not chat_id:
        _clear(uid)
        return await event.reply(
            "❌ Could not identify channel from this forward."
        )

    try:
        entity = await userbot.get_entity(chat_id)
        cid, name = _extract_channel_info(entity)

        if await db.dest_exists_in_setup(sid, cid):
            _clear(uid)
            return await event.reply("⚠️ Already in this setup.")

        await db.add_destination(sid, cid, name)
        _clear(uid)
        await event.reply(
            f"✅ Added to setup #{sid}:\n**{name}**\n`{cid}`"
        )
    except Exception as e:
        _clear(uid)
        await event.reply(f"❌ Could not resolve: {e}")




# ═══════════════════════════════════════════════════════════════
#  TEXT FLOW IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════

async def _flow_gensession(event, state, text):
    uid = event.sender_id
    step = state["step"]

    if step == "phone":
        await event.reply("⏳ Connecting...")
        try:
            sess = StringSession()
            cl = TelegramClient(sess, Config.API_ID, Config.API_HASH)
            await cl.connect()
            r = await cl.send_code_request(text)
            _set(uid, {
                "cmd": "gensession", "step": "code",
                "phone": text, "client": cl, "hash": r.phone_code_hash,
            })
            await event.reply("📱 Code sent! Send digits only:")
        except Exception as e:
            _clear(uid)
            await event.reply(f"❌ {e}")

    elif step == "code":
        cl = state["client"]
        try:
            await cl.sign_in(
                phone=state["phone"],
                code=text.replace(" ", "").replace("-", ""),
                phone_code_hash=state["hash"],
            )
            s = cl.session.save()
            await cl.disconnect()
            _clear(uid)
            await event.reply(
                f"✅ **Session generated!**\n\n`{s}`\n\n"
                "Set as `SESSION_STRING` and redeploy."
            )
        except SessionPasswordNeededError:
            _set(uid, {
                "cmd": "gensession", "step": "2fa",
                "phone": state["phone"], "client": cl, "hash": state["hash"],
            })
            await event.reply("🔐 2FA enabled. Send password:")
        except Exception as e:
            _clear(uid)
            try:
                await cl.disconnect()
            except Exception:
                pass
            await event.reply(f"❌ {e}")

    elif step == "2fa":
        cl = state["client"]
        try:
            await cl.sign_in(password=text)
            s = cl.session.save()
            await cl.disconnect()
            _clear(uid)
            await event.reply(
                f"✅ **Session generated!**\n\n`{s}`\n\n"
                "Set as `SESSION_STRING` and redeploy."
            )
        except Exception as e:
            _clear(uid)
            try:
                await cl.disconnect()
            except Exception:
                pass
            await event.reply(f"❌ {e}")


async def _flow_setsource(event, userbot, db, sid, text):
    uid = event.sender_id
    try:
        entity = await _resolve_entity(userbot, text)
        cid, name = _extract_channel_info(entity)

        old = await db.get_setup(sid)
        if old and old.get("source_channel"):
            await db.delete_post_tracking(sid, old["source_channel"])

        await db.update_setup(sid, {
            "source_channel": cid, "source_name": name,
        })
        _clear(uid)
        await event.reply(
            f"✅ Setup #{sid} source → **{name}**\n`{cid}`"
        )
    except ValueError as e:
        await event.reply(str(e))
    except Exception as e:
        await event.reply(f"❌ {e}")


async def _flow_addchannel(event, userbot, db, sid, text):
    uid = event.sender_id
    try:
        entity = await _resolve_entity(userbot, text)
        cid, name = _extract_channel_info(entity)

        if await db.dest_exists_in_setup(sid, cid):
            _clear(uid)
            return await event.reply("⚠️ Already in this setup.")

        await db.add_destination(sid, cid, name)
        _clear(uid)
        await event.reply(
            f"✅ Added to setup #{sid}:\n**{name}**\n`{cid}`"
        )
    except ValueError as e:
        await event.reply(str(e))
    except Exception as e:
        await event.reply(f"❌ {e}")


async def _flow_removechannel(event, db, sid, text):
    uid = event.sender_id
    try:
        cid = int(text.strip())
        await db.remove_destination(sid, cid)
        _clear(uid)
        await event.reply(f"✅ Removed `{cid}` from setup #{sid}.")
    except ValueError:
        await event.reply("❌ Invalid channel ID.")



async def _flow_settime(event, db, sid, text):
    uid = event.sender_id
    if text.lower() == "off":
        await db.update_setup(sid, {"time_start": None, "time_end": None})
        _clear(uid)
        return await event.reply(f"✅ Setup #{sid} → 24/7.")
    try:
        parts = text.split("-")
        if len(parts) != 2:
            raise ValueError
        sh, eh = int(parts[0]), int(parts[1])
        if not (0 <= sh <= 23 and 0 <= eh <= 23):
            raise ValueError
        await db.update_setup(sid, {"time_start": sh, "time_end": eh})
        _clear(uid)
        await event.reply(f"✅ Setup #{sid} → **{sh}:00–{eh}:00**")
    except (ValueError, IndexError):
        await event.reply("❌ Use `start-end` (e.g. `9-21`) or `off`")


async def _flow_setfooter(event, db, sid, text):
    uid = event.sender_id
    if text.lower() == "none":
        await db.update_setup(sid, {"footer": ""})
        _clear(uid)
        return await event.reply(f"✅ Setup #{sid} footer removed.")
    await db.update_setup(sid, {"footer": text})
    _clear(uid)
    await event.reply(f"✅ Setup #{sid} footer →\n{text}")


async def _flow_setmode(event, db, sid, text):
    uid = event.sender_id
    mode = _clean(text)
    if mode not in {"forward", "copy", "text_only"}:
        return await event.reply("❌ Send exactly: forward, copy, or text_only")
    await db.update_setup(sid, {"posting_mode": mode})
    _clear(uid)
    await event.reply(f"✅ Setup #{sid} mode → **{mode}**")


async def _flow_setlink(event, db, state, text):
    uid = event.sender_id
    sid = state.get("setup_id")
    step = state.get("step", "mode")

    if step == "mode":
        mode = _clean(text)
        if mode not in {"keep", "remove", "replace"}:
            return await event.reply("❌ Send: keep, remove, or replace")
        if mode == "replace":
            _set(uid, {"cmd": "setlink", "step": "url", "setup_id": sid})
            return await event.reply("🔗 Send the replacement URL:")
        await db.update_setup(sid, {"link_mode": mode})
        _clear(uid)
        await event.reply(f"✅ Setup #{sid} links → **{mode}**")

    elif step == "url":
        await db.update_setup(sid, {
            "link_mode": "replace", "replace_link": text.strip()
        })
        _clear(uid)
        await event.reply(f"✅ Links replaced with:\n{text}")


async def _flow_addadmin(event, db, text):
    uid = event.sender_id
    try:
        aid = int(text.strip())
        await db.add_admin(aid)
        _clear(uid)
        await event.reply(f"✅ Admin added: `{aid}`")
    except ValueError:
        await event.reply("❌ Invalid user ID.")


async def _flow_removeadmin(event, db, text):
    uid = event.sender_id
    try:
        aid = int(text.strip())
        if aid == Config.OWNER_ID:
            _clear(uid)
            return await event.reply("❌ Cannot remove owner.")
        await db.remove_admin(aid)
        _clear(uid)
        await event.reply(f"✅ Admin removed: `{aid}`")
    except ValueError:
        await event.reply("❌ Invalid user ID.")
