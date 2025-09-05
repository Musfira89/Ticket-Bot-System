
# Ticket Bot 
# =========================
# Key design choices:
# - Each ticket is a separate private room with just: user, staff, bot.
# - Ticket metadata is stored in two places (no external DB):
#     1) Bot account-data: global index of tickets for recovery
#     2) Ticket room state: per-ticket details (status, category, creator, timestamps)
# - Auto-delete after 14 days when closed. Rescheduled on startup by scanning index.