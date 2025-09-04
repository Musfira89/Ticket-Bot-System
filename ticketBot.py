# ticketBot.py
from maubot import Plugin
from maubot.handlers import command
from mautrix.types import EventType, MessageEvent, RoomID, UserID
import time
import asyncio


class Ticket(Plugin):
    """
    Matrix Support Ticketing Bot
    Features:
      - !ticket open -> user selects category
      - Bot creates private room: user + staff + bot
      - Master log room (admins can configure)
      - !ticket close -> close ticket, schedule auto-delete in 14d
    """

    # Admin-configured: where to log new tickets (replace with your log room ID)
    MASTER_LOG_ROOM = "!your-admin-log-room-id:j5.chat"

    # categories
    CATEGORIES = {
        "1": "General Help",
        "2": "Purchase Issues",
        "3": "Other",
    }

    # Keep ticket metadata
    tickets = {}  # {room_id: {"id": int, "creator": str, "category": str, "status": str, "created_at": ts}}
    counter = 1000  # ticket numbers

    # ----------------- Main Command -----------------
    @command.new("ticket", help="Ticket system commands")
    async def ticket_root(self, evt: MessageEvent) -> None:
        args = evt.content.body.strip().split()

        if len(args) < 2:
            await evt.reply("Usage: !ticket open | close")
            return

        sub = args[1].lower()

        if sub == "open":
            await self._ticket_open(evt)
        elif sub == "close":
            await self._ticket_close(evt)
        else:
            await evt.reply("❌ Unknown subcommand. Use `!ticket open` or `!ticket close`.")

    # ----------------- Open Ticket -----------------
    async def _ticket_open(self, evt: MessageEvent) -> None:
        user = evt.sender

        # Ask category
        cat_msg = "Please choose a category:\n"
        for num, name in self.CATEGORIES.items():
            cat_msg += f"{num}. {name}\n"
        cat_msg += "Reply with the number."
        await evt.reply(cat_msg)

        # Wait for user reply (within 30s)
        try:
            resp_evt = await self.client.wait_for_event(
                EventType.ROOM_MESSAGE,
                timeout=30,
                predicate=lambda e: e.sender == user and e.room_id == evt.room_id,
            )
        except asyncio.TimeoutError:
            await evt.reply("⏰ Ticket creation timed out.")
            return

        body = resp_evt.content.body.strip()
        if body not in self.CATEGORIES:
            await evt.reply("❌ Invalid category number.")
            return
        category = self.CATEGORIES[body]

        # Generate ticket id
        self.counter += 1
        ticket_id = self.counter

        # Create private room (invite user + bot; staff must be invited manually or by config)
        name = f"Ticket #{ticket_id} – {category}"
        new_room = await self.client.create_room(
            is_direct=False,
            invite=[user],
            name=name,
            preset="private_chat",
        )

        # Save ticket info
        self.tickets[new_room] = {
            "id": ticket_id,
            "creator": user,
            "category": category,
            "status": "Open",
            "created_at": time.time(),
        }

        # Post intro message in ticket room
        await self.client.send_text(
            new_room,
            f"🎟️ Ticket #{ticket_id} opened by {user}\n"
            f"Category: {category}\nStatus: Open"
        )

        # Log in master log room
        if self.MASTER_LOG_ROOM:
            await self.client.send_text(
                self.MASTER_LOG_ROOM,
                f"📩 New Ticket #{ticket_id} – {category} by {user}\n"
                f"[Link to room](https://matrix.to/#/{new_room})"
            )

        await evt.reply(f"✅ Your ticket has been created: [Link](https://matrix.to/#/{new_room})")

    # ----------------- Close Ticket -----------------
    async def _ticket_close(self, evt: MessageEvent) -> None:
        room = evt.room_id
        ticket = self.tickets.get(room)
        if not ticket:
            await evt.reply("❌ This room is not a ticket.")
            return

        if ticket["status"] == "Closed":
            await evt.reply("❌ Ticket already closed.")
            return

        # Only creator or admin can close
        if evt.sender != ticket["creator"] and not await self._is_admin(room, evt.sender):
            await evt.reply("⛔ Only ticket creator or admins can close this ticket.")
            return

        ticket["status"] = "Closed"
        await self.client.send_text(
            room,
            f"✅ Ticket #{ticket['id']} has been closed.\n"
            f"This room will auto-delete in 14 days."
        )

        # Schedule delete after 14d
        asyncio.create_task(self._schedule_delete(room, ticket['id'], 14 * 24 * 3600))

    # ----------------- Utils -----------------
    async def _is_admin(self, room_id: RoomID, user: UserID) -> bool:
        try:
            pl = await self.client.get_state_event(room_id, "m.room.power_levels", "")
            user_pl = pl.get("users", {}).get(user, pl.get("users_default", 0))
            return user_pl >= 50 or user == self.client.mxid
        except Exception:
            return False

    async def _schedule_delete(self, room_id: str, ticket_id: int, delay: int):
        try:
            await asyncio.sleep(delay)
            await self.client.send_text(room_id, f"🗑️ Ticket #{ticket_id} expired and will be deleted.")
            await self.client.leave_room(room_id)
            await self.client.forget_room(room_id)
            self.tickets.pop(room_id, None)
        except Exception as e:
            self.log.warning(f"Failed to delete ticket room {room_id}: {e}")
