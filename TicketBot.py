from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import RoomCreatePreset
import asyncio

# Auto-delete closed tickets after 14 days
AUTO_DELETE_SECONDS = 14 * 24 * 60 * 60

CATEGORY_MAP = {
    "1": "general",
    "2": "purchase",
    "3": "other"
}

# Add fixed admin account and banned bots list
ADMIN_USER = "@admin:j5.chat"  

# Accounts that should NEVER be allowed into ticket rooms (even if invited)
BANNED_BOTS = {
    "@karma:j5.chat",
    "@antithread:j5.chat",
    "@poll:j5.chat",
    "@helpdesk:j5.chat",
}


class Ticket(Plugin):
    tickets = {}  # user_id -> room_id cache

    async def start(self) -> None:
        """On plugin startup, ensure DB table exists and load tickets into memory."""
        # create table if missing and load rows into memory
        self.database.execute(
            "CREATE TABLE IF NOT EXISTS tickets (user_id TEXT PRIMARY KEY, room_id TEXT NOT NULL)"
        )
        rows = self.database.execute("SELECT user_id, room_id FROM tickets").fetchall()
        self.tickets = {row[0]: row[1] for row in rows}
        self.log.info(f"Loaded {len(self.tickets)} tickets from database")

    # ---------- commands ----------
    @command.new(name="ticket", help="Ticket system")
    @command.argument("subcommand", required=False)
    @command.argument("category", required=False)
    @command.argument("subject", required=False, pass_raw=True)
    async def ticket_handler(
        self, evt: MessageEvent, subcommand: str, category: str, subject: str
    ) -> None:
        user = evt.sender

        if not subcommand:
            await evt.reply(
                "📋 Please choose a category:\n"
                "1️⃣ General Help\n"
                "2️⃣ Purchase Issues\n"
                "3️⃣ Other\n\n"
                "Reply with: `!ticket open <number> [subject]`"
            )
            return

        # --------------- OPEN ---------------
        if subcommand == "open":
            # already has an open ticket?
            if user in self.tickets:
                await evt.reply(f" You already have an open ticket: {self.tickets[user]}")
                return

            # map numeric -> name
            if category in CATEGORY_MAP:
                category = CATEGORY_MAP[category]

            if category not in ("general", "purchase", "other"):
                await evt.reply(
                    " Invalid category.\n"
                    "Usage: !ticket open <1|2|3|general|purchase|other> [subject]"
                )
                return

            # create room with bot as creator
            try:
                room_id = await self.client.create_room(
                    is_direct=False,
                    preset=RoomCreatePreset.TRUSTED_PRIVATE,
                    name=f"Ticket - {category.capitalize()}",
                )
            except Exception as e:
                self.log.warning(f"TRUSTED_PRIVATE preset failed: {e}. Trying PRIVATE preset.")
                try:
                    room_id = await self.client.create_room(
                        is_direct=False,
                        preset=RoomCreatePreset.PRIVATE,
                        name=f"Ticket - {category.capitalize()}",
                    )
                except Exception as e2:
                    self.log.exception(f"Room creation failed (all presets). Aborting: {e2}")
                    await evt.reply("Failed to create ticket room. Please contact an admin.")
                    return

            topic_text = (
                f"Ticket - {category.capitalize()}\n"
                f"This is the beginning of your direct message history with Ticket - {category.capitalize()}.\n"
                f"Only the 3 of you are in this convo - unless admin invited"
            )
            try:
                try:
                    await self.client.set_room_topic(room_id, topic_text)
                except Exception:
                    await self.client.set_state_event(room_id, "m.room.topic", {"topic": topic_text})
            except Exception as e:
                self.log.warning(f"Failed to set topic for {room_id}: {e}")

            # Important: lock down invites so only PL >= 50 can invite
            bot_id = getattr(self.client, "user_id", None) or getattr(self.client, "mxid", None)
            power_levels = {
                "users": {},
                "users_default": 0,
                "events": {
                    "m.room.name": 50,
                    "m.room.topic": 50,
                },
                "events_default": 0,
                # require power 50 to invite / kick / ban / redact
                "invite": 50,
                "kick": 50,
                "ban": 50,
            }
            # give bot full power so it can manage the room
            if bot_id:
                power_levels["users"][bot_id] = 100
            power_levels["users"][ADMIN_USER] = 50

            try:
                await self.client.set_state_event(room_id, "m.room.power_levels", power_levels)
            except Exception as e:
                self.log.warning(f"Failed to set power_levels for {room_id}: {e}")

            for who in (user, ADMIN_USER):
                try:
                    # invite_user signature: (room_id, user_id)
                    await self.client.invite_user(room_id, who)
                except Exception as e:
                    self.log.warning(f"Failed to invite {who} to {room_id}: {e}")

            # persist mapping
            self.tickets[user] = room_id
            try:
                self.database.execute(
                    "INSERT OR REPLACE INTO tickets (user_id, room_id) VALUES (?, ?)", (user, room_id)
                )
                try:
                    self.database.commit()
                except Exception:
                    pass
            except Exception as e:
                self.log.warning(f"Failed to write ticket to DB for {user}: {e}")

            base_url = self.config.get("public_baseurl", "https://j5.chat")
            join_link = f"{base_url}/#/room/{room_id}"
            await evt.reply(
                f"Ticket created in room: {room_id}\n"
                f"🔗 Admin join link: {join_link}"
            )

            try:
                await self.client.send_text(
                    room_id,
                    f" New ticket opened by {user}\n"
                    f"Category: {category}\n"
                    f"Subject: {subject or 'N/A'}\n\n"
                    f" Admin {ADMIN_USER} was invited automatically. Admin may invite moderators as needed."
                )
            except Exception as e:
                self.log.warning(f"Failed to send initial message in {room_id}: {e}")

            self.log.info(f"Created ticket room {room_id} for {user}")
            asyncio.create_task(self.auto_close_ticket(user, room_id))

        # --------------- CLOSE ---------------
        elif subcommand == "close":
            if user not in self.tickets:
                await evt.reply("You don’t have any open ticket.")
                return

            room_id = self.tickets[user]

            try:
                await self.client.send_text(room_id, "Ticket closed. This room will be deleted in 14 days.")
            except Exception:
                pass

            await evt.reply("Ticket closed. This room will be deleted in 14 days.")

            # kick the user and the admin immediately (so both leave)
            for who in (user, ADMIN_USER):
                try:
                    await self.client.kick_user(who, room_id, "Ticket closed")
                except Exception as e:
                    self.log.warning(f"Failed to kick {who} from {room_id}: {e}")

            # schedule final delete after 14 days
            asyncio.create_task(self.delete_after(room_id, user))

        # --------------- STATUS ---------------
        elif subcommand == "status":
            if user in self.tickets:
                await evt.reply(f"You have a ticket open in {self.tickets[user]}")
            else:
                await evt.reply("No open tickets found.")

        else:
            await evt.reply("Usage: !ticket <open|close|status>")

    # ---------- auto-delete helpers ----------
    async def auto_close_ticket(self, user, room_id):
        await asyncio.sleep(AUTO_DELETE_SECONDS)
        if user in self.tickets and self.tickets[user] == room_id:
            try:
                await self.client.send_text(room_id, "⏳ Auto-closing this ticket after inactivity. It will be deleted in 14 days.")
            except Exception:
                pass
            # on auto-close we also kick the members immediately, then schedule deletion
            for who in (user, ADMIN_USER):
                try:
                    await self.client.kick_user(who, room_id, "Ticket auto-closed")
                except Exception:
                    pass
            await self._delete_ticket(user, room_id)

    async def delete_after(self, room_id, user):
        # Wait 14 days then *delete* the room and remove DB mapping
        await asyncio.sleep(AUTO_DELETE_SECONDS)
        try:
            await self.client.send_text(room_id, "🗑️ Ticket deleted after expiry.")
        except Exception:
            pass
        await self._delete_ticket(user, room_id)

    async def _delete_ticket(self, user, room_id):
        # attempt to leave & delete; remove DB entry + in-memory map
        try:
            await self.client.leave_room(room_id)
        except Exception as e:
            self.log.warning(f"Failed to leave room {room_id}: {e}")
        try:
            await self.client.delete_room(room_id)
        except Exception as e:
            self.log.warning(f"Failed to delete room {room_id}: {e}")

        if user in self.tickets:
            del self.tickets[user]

        try:
            self.database.execute("DELETE FROM tickets WHERE user_id = ?", (user,))
            try:
                self.database.commit()
            except Exception:
                pass
        except Exception as e:
            self.log.warning(f"Failed to delete ticket DB row for {user}: {e}")

    # ---------- invitations & joins enforcement ----------
    async def on_event(self, room, event):
        """
        Monitor membership events to enforce rules:
         - If a banned bot is invited or joins, kick them immediately.
         - If any non-admin tries to self-invite or otherwise get into a ticket room,
           we will remove them (strict enforcement).
        Note: maubot will call this for all events; check event type.
        """
        try:
            # we guard to only membership changes
            if getattr(event, "type", "") != "m.room.member":
                return

            # who is the membership target (state_key)
            target = getattr(event, "state_key", None)
            content = getattr(event, "content", {}) or {}
            membership = content.get("membership")

            # Only handle invite/join events
            if membership not in ("invite", "join"):
                return

            # if the target is in our banned-bots list -> kick them
            if target in BANNED_BOTS:
                try:
                    await self.client.kick_user(target, room.room_id, "This bot is not allowed in ticket rooms.")
                    self.log.info(f"Kicked banned bot {target} from {room.room_id}")
                except Exception as e:
                    self.log.warning(f"Failed to kick banned bot {target} from {room.room_id}: {e}")
                return

            if target and (target in self.tickets and self.tickets[target] == room.room_id or room.room_id in self.tickets.values()):
                bot_id = getattr(self.client, "user_id", None) or getattr(self.client, "mxid", None)
                allowed = {ADMIN_USER, bot_id}
                # add opener(s) who own this ticket
                owners = [u for u, rid in self.tickets.items() if rid == room.room_id]
                allowed.update(owners)

                if target not in allowed:
                    try:
                        await self.client.kick_user(target, room.room_id, "Only ticket opener and admin are allowed in this room.")
                        self.log.info(f"Kicked unauthorized {target} from ticket {room.room_id}")
                    except Exception as e:
                        self.log.warning(f"Failed to kick unauthorized member {target} from {room.room_id}: {e}")

        except Exception as e:
            self.log.exception(f"Exception in on_event enforcement: {e}")
