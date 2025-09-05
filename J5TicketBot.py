from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import RoomCreatePreset
import asyncio

AUTO_DELETE_SECONDS = 10 * 60

CATEGORY_MAP = {
    "1": "general",
    "2": "purchase",
    "3": "other"
}

# Fixed admin account 
ADMIN_USER = "@admin:j5.chat"


class Ticket(Plugin):
    tickets = {}  

    async def start(self) -> None:
        """On plugin startup, ensure DB table exists and load tickets into memory."""
        self.database.execute(
            "CREATE TABLE IF NOT EXISTS tickets (user_id TEXT PRIMARY KEY, room_id TEXT NOT NULL)"
        )
        rows = self.database.execute("SELECT user_id, room_id FROM tickets").fetchall()
        self.tickets = {row[0]: row[1] for row in rows}
        self.log.info(f"Loaded {len(self.tickets)} tickets from database")

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

      
        if subcommand == "open":
            if user in self.tickets:
                await evt.reply(f"⚠️ You already have an open ticket: {self.tickets[user]}")
                return

            if category in CATEGORY_MAP:
                category = CATEGORY_MAP[category]

            if category not in ["general", "purchase", "other"]:
                await evt.reply(
                    "⚠️ Invalid category.\n"
                    "Usage: !ticket open <1|2|3|general|purchase|other> [subject]"
                )
                return

            # invite list: user + fixed admin
            invite_list = [user, ADMIN_USER]

            # Step A: create the room (no invites passed to create_room)
            try:
                room_id = await self.client.create_room(
                is_direct=False,
                preset=RoomCreatePreset.TRUSTED_PRIVATE,
                name=f"Ticket - {category.capitalize()}",
                )
            except Exception as e:
               self.log.warning(f"Room creation with TRUSTED_PRIVATE failed: {e}. Trying PRIVATE preset.")
               try:
                   room_id = await self.client.create_room(
                       is_direct=False,
                       preset=RoomCreatePreset.PRIVATE,
                       name=f"Ticket - {category.capitalize()}",
                   )
            except Exception as e2:
               self.log.warning(f"Fallback room creation also failed: {e2}. Aborting ticket creation.")
               await evt.reply("❌ Failed to create ticket room. Please contact an admin.")
               return
            
            #  Step A2: set a topic/description right after creation
            try:
                await self.client.set_room_topic(
                   room_id,
                   f"Support Ticket for {category.capitalize()} | Subject: {subject or 'N/A'}"
                )
            except Exception as e:
                self.log.warning(f"Failed to set topic for {room_id}: {e}")


            # Step B: invite (manually) 
            for who in invite_list:
                try:
                    await self.client.invite_user(room_id, who)
                except Exception as e:
                    # invite may fail if the user is local and the server rejects, but we continue
                    self.log.warning(f"Failed to invite {who} to {room_id}: {e}")

            # persist ticket mapping in memory + DB
            self.tickets[user] = room_id
            try:
                self.database.execute(
                    "INSERT OR REPLACE INTO tickets (user_id, room_id) VALUES (?, ?)", (user, room_id)
                )
                # commit so the DB actually saves
                try:
                    self.database.commit()
                except Exception:
                    # some maubot DB wrappers don't need commit; ignore if not available
                    pass
            except Exception as e:
                self.log.warning(f"Failed to write ticket to DB for {user}: {e}")

            # reply in origin room with a permalink / join link for admin convenience
            base_url = self.config.get("public_baseurl", "https://j5.chat")
            join_link = f"{base_url}/#/room/{room_id}"
            await evt.reply(
                f"✅ Ticket created in room: {room_id}\n"
                f"🔗 Admin join link: {join_link}"
            )

            # announce inside the ticket room
            try:
                await self.client.send_text(
                    room_id,
                    f"🎫 New ticket opened by {user}\n"
                    f"Category: {category}\n"
                    f"Subject: {subject or 'N/A'}\n\n"
                    f"👮 Admin {ADMIN_USER} was invited automatically. Admin may invite moderators as needed."
                )
            except Exception as e:
                self.log.warning(f"Failed to send initial message in {room_id}: {e}")

            self.log.info(f"Created ticket room {room_id} for {user}")
            # schedule auto-close/delete
            asyncio.create_task(self.auto_close_ticket(user, room_id))

        # -------------------------------------------------------------
        # CLOSE: user closes their ticket
        # -------------------------------------------------------------
        elif subcommand == "close":
            if user not in self.tickets:
                await evt.reply("⚠️ You don’t have any open ticket.")
                return

            room_id = self.tickets[user]
            try:
                await self.client.send_text(room_id, "✅ Ticket closed. This room will be deleted soon.")
            except Exception:
                pass
            await evt.reply("Your ticket has been closed.")
            asyncio.create_task(self.delete_after(room_id, user))

        # -------------------------------------------------------------
        # STATUS: show open ticket room id
        # -------------------------------------------------------------
        elif subcommand == "status":
            if user in self.tickets:
                await evt.reply(f"✅ You have a ticket open in {self.tickets[user]}")
            else:
                await evt.reply("⚠️ No open tickets found.")

        else:
            await evt.reply("Usage: !ticket <open|close|status>")

    # -------------------------------------------------------------
    # Auto-close and delete helpers
    # -------------------------------------------------------------
    async def auto_close_ticket(self, user, room_id):
        await asyncio.sleep(AUTO_DELETE_SECONDS)
        if user in self.tickets and self.tickets[user] == room_id:
            try:
                await self.client.send_text(room_id, "⏳ Auto-closing this ticket after inactivity.")
            except Exception:
                pass
            await self._delete_ticket(user, room_id)

    async def delete_after(self, room_id, user):
        await asyncio.sleep(AUTO_DELETE_SECONDS)
        try:
            await self.client.send_text(room_id, "🗑️ Ticket deleted after expiry.")
        except Exception:
            pass
        await self._delete_ticket(user, room_id)

    async def _delete_ticket(self, user, room_id):
        # attempt to leave and delete the room, then remove DB/memory entry
        try:
            await self.client.leave_room(room_id)
            await self.client.delete_room(room_id)
        except Exception as e:
            self.log.warning(f"Failed to leave/delete room {room_id}: {e}")

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

 
    async def on_invite(self, room, event):
        """
        Keep logic simple: accept invite only when it's the fixed admin joining,
        otherwise reject (prevents arbitrary users joining ticket rooms).
        """
        user = event.sender
        if user == ADMIN_USER:
            try:
                await self.client.join_room(room.room_id)
                await self.client.send_text(room.room_id, f"✅ {user} (Admin) joined the ticket.")
            except Exception:
                pass
        else:
            try:
                await self.client.kick_user(user, room.room_id, "Only admin can join tickets directly.")
            except Exception:
                pass
