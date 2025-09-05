# Ticket Bot  
## Overview  
A simple Matrix support ticketing system powered by Maubot.  
Each ticket is handled in its **own private room** between the user, the bot, and the fixed admin account.  

## Key Features  
- **Ticket Categories:**  
  - 1️ General Help  
  - 2️ Purchase Issues  
  - 3️ Other  

- **Room Creation:**  
  - Each ticket is created in a private room.  
  - Room **name** → `Ticket - <Category>`  
  - Room **topic/description** → `Support Ticket for <Category> | Subject: <User provided or N/A>`  

- **Access Control:**  
  - Only the user who opened the ticket and the fixed admin (`@admin:j5.chat`) are invited.  
  - Admin may invite additional moderators manually if needed.  
  - Users can only view their own tickets.  
  - Admin has full access to all tickets.  

- **Ticket Lifecycle:**  
  - `!ticket open <category> [subject]` → Opens a new ticket room.  
  - `!ticket close` → Closes the user’s active ticket.  
  - `!ticket status` → Shows whether the user has an open ticket.  
  - Closed tickets are **auto-deleted after 14 days** (configurable).  

- **Persistence:**  
  - Open tickets are cached in a local SQLite database provided by Maubot.  
  - On bot restart, ticket data is loaded back into memory.  

- **Automatic Admin Handling:**  
  - The admin account receives an invite for every ticket.  
  - If someone else tries to invite themselves into a ticket, the bot kicks them out automatically.  


