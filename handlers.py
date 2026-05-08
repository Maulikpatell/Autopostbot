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


# ── Robust text cleaner ────────────────────────────────────────
def _clean(text: str) -> str:
    """Strip markdown formatting characters that Telegram may inject."""
    return re.sub(r"[*_~`>\[\]()|]", "", text).strip().lower()


# ── Ensure user has a selected setup, returns (setup_id, setup) ─
async def _require_setup(event, db: Database, action: str = "do this"):
    uid = event.sender_id
    sid = await db.get_selected_setup(uid)
    if not sid:
        await event.reply(
            f"❌ No setup selected.\n"
            f"Use /newsetup to create one, or /setups to see existing ones."
        )
        return None, None
    setup = await db.get_setup(sid)
    if not setup:
        await db.clear_selected_setup(uid)
        await event.reply("❌ Selected setup was deleted. Use /newsetup.")
        return None, None
    return sid, setup


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
            "👋 **AutoPost Bot — Multi-Setup Edition**\n\n"
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
            "/select _id_ — Select a setup to edit\n"
            "/delsetup _id_ — Delete a setup\n\n"
            "📌 **Per-Setup Config** (select a setup first)\n"
            "/setsource — Set source channel\n"
            "/addchannel — Add destination channel\n"
            "/removechannel — Remove destination\n"
            "/setlimit _channel_id limit_ — Daily limit\n"
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
            "/status — Full status overview\n"
            "/cancel — Cancel current operation"
        )

    # ──────────────── /newsetup ────────────────
    @bot.on(events.NewMessage(pattern=r"/newsetup(?!\S)"))
    async def cmd_newsetup(event):
        if not await db.is_admin(event.sender_id):
            return
        sid = await db.create_setup()
        await db.set_selected_setup(event.sender_id, sid)
        await event.reply(
            f"✅ **Setup #{sid}** created and selected.\n\n"
            f"Now configure it:\n"
            f"1. /setsource — pick the source channel\n"
            f"2. /addchannel — add destinations\n"
            f"3. /setmode, /setfooter, /settime, etc."
        )

    # ──────────────── /setups ────────────────
    @bot.on(events.NewMessage(pattern=r"/setups(?!\S)"))
    async def cmd_setups(event):
        if not await db.is_admin(event.sender_id):
            return
        setups = await db.get_all_setups()
        if not setups:
            return await event.reply("📭 No setups yet. Use /newsetup.")
        sel = await db.get_selected_setup(event.sender_id)
        lines = []
        for s in setups:
            marker = " ◀️" if s["setup_id"] == sel else ""
            src = s.get("source_name") or "No source"
            dsts = len(s.get("destinations", []))
            paused = " ⏸" if s.get("is_paused") else " ▶️"
            lines.append(
                f"**#{s['setup_id']}**{marker}{paused}  "
                f"Source: {src}  →  {dsts} dest(s)"
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
            f"📌 Source: {src}\n"
            f"📤 Destinations: {dsts}"
        )

    # ──────────────── /delsetup ────────────────
    @bot.on(events.NewMessage(pattern=r"/delsetup\s+(\d+)(?!\S)"))
    async def cmd_delsetup(event):
        if not await db.is_admin(event.sender_id):
            return
        sid = int(event.pattern_match.group(1))
        setup = await db.get_setup(sid)
        if not setup:
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
        sid, _ = await _require_setup(event, db, "set source")
        if sid is None:
            return
        await event.reply(
            f"📌 **Setup #{sid}** — Set Source Channel\n\n"
            "Send the channel username or ID:\n"
            "Example: `@mysource` or `-1001234567890`"
        )
        _set(event.sender_id, {"cmd": "setsource", "setup_id": sid})

    # ──────────────── /addchannel ────────────────
    @bot.on(events.NewMessage(pattern=r"/addchannel(?!\S)"))
    async def cmd_addchannel(event):
        if not await db.is_admin(event.sender_id):
            return
        if not userbot:
            return await event.reply("❌ Userbot not connected.")
        sid, _ = await _require_setup(event, db, "add channel")
        if sid is None:
            return
        await event.reply(
            f"➕ **Setup #{sid}** — Add Destination\n\n"
            "Send the channel username or ID:\n"
            "Example: `@mydest` or `-1001234567890`"
        )
        _set(event.sender_id, {"cmd": "addchannel", "setup_id": sid})

    # ──────────────── /removechannel ────────────────
    @bot.on(events.NewMessage(pattern=r"/removechannel(?!\S)"))
    async def cmd_removechannel(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, setup = await _require_setup(event, db, "remove channel")
        if sid is None:
            return
        dsts = setup.get("destinations", [])
        if not dsts:
            return await event.reply("❌ No destinations in this setup.")
        lines = []
        for d in dsts:
            lines.append(
                f"• {d.get('channel_name', d['channel_id'])} "
                f"(`{d['channel_id']}`)"
            )
        await event.reply(
            f"➖ **Setup #{sid}** — Remove Destination\n\n"
            "Send the channel ID:\n\n" + "\n".join(lines)
        )
        _set(event.sender_id, {"cmd": "removechannel", "setup_id": sid})

    # ──────────────── /setlimit (one-shot) ────────────────
    @bot.on(events.NewMessage(pattern=r"/setlimit\s+(\S+)\s+(\d+)(?!\S)"))
    async def cmd_setlimit(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db, "set limit")
        if sid is None:
            return
        cid = int(event.pattern_match.group(1))
        limit = int(event.pattern_match.group(2))
        if limit < 1:
            return await event.reply("❌ Limit must be ≥ 1.")
        await db.set_destination_limit(sid, cid, limit)
        await event.reply(f"✅ Daily limit for `{cid}` → **{limit}**")

    # ──────────────── /settime ────────────────
    @bot.on(events.NewMessage(pattern=r"/settime(?!\S)"))
    async def cmd_settime(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db, "set time")
        if sid is None:
            return
        await event.reply(
            f"⏱ **Setup #{sid}** — Set Time Window\n\n"
            "Send `start-end` (hours 0-23), e.g. `9-21`\n"
            "Send `off` for 24/7 posting."
        )
        _set(event.sender_id, {"cmd": "settime", "setup_id": sid})

    # ──────────────── /setfooter ────────────────
    @bot.on(events.NewMessage(pattern=r"/setfooter(?!\S)"))
    async def cmd_setfooter(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db, "set footer")
        if sid is None:
            return
        await event.reply(
            f"✏️ **Setup #{sid}** — Set Footer\n\n"
            "Send the footer text, or `none` to remove."
        )
        _set(event.sender_id, {"cmd": "setfooter", "setup_id": sid})

    # ──────────────── /setmode (one-shot OR interactive) ──────
    @bot.on(events.NewMessage(pattern=r"/setmode\s+(\S+)(?!\S)"))
    async def cmd_setmode_oneshot(event):
        """One-shot: /setmode copy"""
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db, "set mode")
        if sid is None:
            return
        raw = event.pattern_match.group(1)
        mode = _clean(raw)
        valid = {"forward", "copy", "text_only"}
        if mode not in valid:
            return await event.reply(
                f"❌ Invalid mode `{raw}`.\nUse: forward, copy, or text_only"
            )
        await db.update_setup(sid, {"posting_mode": mode})
        await event.reply(f"✅ Setup #{sid} mode → **{mode}**")

    @bot.on(events.NewMessage(pattern=r"/setmode(?!\S)"))
    async def cmd_setmode_interactive(event):
        """Interactive: /setmode (no arg)"""
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db, "set mode")
        if sid is None:
            return
        setup = await db.get_setup(sid)
        current = setup.get("posting_mode", "copy") if setup else "copy"
        await event.reply(
            f"📋 **Setup #{sid}** — Set Mode\n\n"
            f"Current: **{current}**\n\n"
            "Send:\n"
            "• forward\n"
            "• copy\n"
            "• text_only"
        )
        _set(event.sender_id, {"cmd": "setmode", "setup_id": sid})

    # ──────────────── /setlink (one-shot OR interactive) ──────
    @bot.on(events.NewMessage(pattern=r"/setlink\s+(\S+)(?!\S)"))
    async def cmd_setlink_oneshot(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db, "set link mode")
        if sid is None:
            return
        raw = event.pattern_match.group(1)
        mode = _clean(raw)
        if mode not in {"keep", "remove", "replace"}:
            return await event.reply(
                "❌ Invalid. Use: keep, remove, or replace"
            )
        if mode == "replace":
            await event.reply("🔗 Send the replacement URL:")
            _set(event.sender_id, {
                "cmd": "setlink", "step": "url", "setup_id": sid
            })
            return
        await db.update_setup(sid, {"link_mode": mode})
        await event.reply(f"✅ Setup #{sid} link mode → **{mode}**")

    @bot.on(events.NewMessage(pattern=r"/setlink(?!\S)"))
    async def cmd_setlink_interactive(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db, "set link mode")
        if sid is None:
            return
        await event.reply(
            f"🔗 **Setup #{sid}** — Set Link Mode\n\n"
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
        sid, setup = await _require_setup(event, db, "toggle loop")
        if sid is None:
            return
        new_val = not setup.get("loop_enabled", False)
        await db.update_setup(sid, {"loop_enabled": new_val})
        state = "ON ✅" if new_val else "OFF ❌"
        await event.reply(f"🔄 Setup #{sid} loop → **{state}**")

    # ──────────────── /pause ────────────────
    @bot.on(events.NewMessage(pattern=r"/pause(?!\S)"))
    async def cmd_pause(event):
        if not await db.is_admin(event.sender_id):
            return
        sid, _ = await _require_setup(event, db, "pause")
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
        sid, setup = await _require_setup(event, db, "resume")
        if sid is None:
            return
        if not setup.get("source_channel"):
            return await event.reply(
                "❌ Set a source channel first with /setsource"
            )
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
            return await event.reply("📭 No setups. Use /newsetup.")

        blocks = []
        for s in setups:
            marker = " ◀️" if s["setup_id"] == sel else ""
            icon = "⏸" if s.get("is_paused") else "▶️"
            src = s.get("source_name") or "Not set"
            mode = s.get("posting_mode", "copy")
            link = s.get("link_mode", "keep")
            footer = s.get("footer", "") or "None"
            loop = "ON" if s.get("loop_enabled") else "OFF"
            ts = s.get("time_start")
            te = s.get("time_end")
            tw = f"{ts}:00–{te}:00" if ts is not None else "24/7"
            dsts = s.get("destinations", [])

            lines = [
                f"{'━' * 30}",
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
                    cnt = await db.get_daily_count(s["setup_id"], d["channel_id"])
                    lim = d.get("daily_limit", 50)
                    nm = d.get("channel_name", str(d["channel_id"]))
                    bar_len = 10
                    filled = min(int(cnt / max(lim, 1) * bar_len), bar_len)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    lines.append(f"  └ {nm}: [{bar}] {cnt}/{lim}")

            # Tracking info
            src_id = s.get("source_channel")
            if src_id:
                trk = await db.get_post_tracking(s["setup_id"], src_id)
                if trk:
                    lines.append(
                        f"  📍 Pointer: {trk.get('current_id', '?')} "
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
        await event.reply("👤 Send the **user ID** to add as admin:")
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
        await event.reply(
            "🗑 Send the user ID to remove:\n\n" + "\n".join(lines)
        )
        _set(event.sender_id, {"cmd": "removeadmin"})

    # ──────────────── /cancel ────────────────
    @bot.on(events.NewMessage(pattern=r"/cancel(?!\S)"))
    async def cmd_cancel(event):
        _clear(event.sender_id)
        await event.reply("✅ Cancelled.")

    # ═══════════════════════════════════════════════════════════
    #  GENERIC PRIVATE MESSAGE (multi-step flows)
    # ═══════════════════════════════════════════════════════════
    @bot.on(events.NewMessage(
        incoming=True,
        func=lambda e: e.is_private and bool(e.text) and not e.text.startswith("/"),
    ))
    async def on_private_text(event):
        uid = event.sender_id
        state = _get(uid)
        if not state:
            return

        text = (event.text or "").strip()
        cmd = state["cmd"]
        sid = state.get("setup_id")

        try:
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
            logger.error(f"Flow error [{cmd}]: {exc}", exc_info=True)
            _clear(uid)
            await event.reply(f"❌ Error: {exc}")



# ═══════════════════════════════════════════════════════════════
#  FLOW IMPLEMENTATIONS
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
                "phone": text, "client": cl,
                "hash": r.phone_code_hash,
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
                "phone": state["phone"], "client": cl,
                "hash": state["hash"],
            })
            await event.reply("🔐 2FA enabled. Send password:")
        except Exception as e:
            _clear(uid)
            try: await cl.disconnect()
            except: pass
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
            try: await cl.disconnect()
            except: pass
            await event.reply(f"❌ {e}")


async def _flow_setsource(event, userbot, db, sid, text):
    uid = event.sender_id
    try:
        ent = await userbot.get_entity(text)
        cid, name = ent.id, getattr(ent, "title", None) or text
        # Reset old tracking
        old = await db.get_setup(sid)
        if old and old.get("source_channel"):
            await db.delete_post_tracking(sid, old["source_channel"])
        await db.update_setup(sid, {
            "source_channel": cid, "source_name": name,
        })
        _clear(uid)
        await event.reply(f"✅ Setup #{sid} source → **{name}** (`{cid}`)")
    except Exception as e:
        await event.reply(f"❌ {e}")


async def _flow_addchannel(event, userbot, db, sid, text):
    uid = event.sender_id
    try:
        ent = await userbot.get_entity(text)
        cid, name = ent.id, getattr(ent, "title", None) or text
        if await db.dest_exists_in_setup(sid, cid):
            _clear(uid)
            return await event.reply("⚠️ Already in this setup.")
        await db.add_destination(sid, cid, name)
        _clear(uid)
        await event.reply(f"✅ Added to setup #{sid}: **{name}**")
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
        return await event.reply(f"✅ Setup #{sid} → 24/7 posting.")
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
    """Interactive mode selection — uses _clean() to strip markdown chars."""
    uid = event.sender_id
    mode = _clean(text)
    valid = {"forward", "copy", "text_only"}
    if mode not in valid:
        return await event.reply(
            f"❌ Got `{text}`.\nSend exactly: forward, copy, or text_only"
        )
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
        await db.update_setup(sid, {"link_mode": "replace", "replace_link": text.strip()})
        _clear(uid)
        await event.reply(f"✅ Setup #{sid} links replaced with:\n{text}")


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

