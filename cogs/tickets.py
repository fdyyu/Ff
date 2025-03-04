import discord
from discord.ext import commands
import asyncio
from datetime import datetime
import json
from typing import Optional, Dict
from .utils import Embed, db, event_dispatcher

class TicketSystem(commands.Cog):
    """🎫 Sistem Ticket Support Advanced"""
    
    def __init__(self, bot):
        self.bot = bot
        self.active_tickets = {}
        self.register_handlers()

    async def setup_tables(self):
        """Setup necessary database tables"""
        async with db.pool.cursor() as cursor:
            # Ticket settings
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS ticket_settings (
                    guild_id TEXT PRIMARY KEY,
                    category_id TEXT,
                    log_channel_id TEXT,
                    support_role_id TEXT,
                    max_tickets INTEGER DEFAULT 1,
                    ticket_format TEXT DEFAULT 'ticket-{user}-{number}',
                    auto_close_hours INTEGER DEFAULT 48
                )
            """)
            
            # Tickets
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    reason TEXT,
                    status TEXT DEFAULT 'open',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    closed_at DATETIME,
                    closed_by TEXT
                )
            """)
            
            # Ticket responses
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS ticket_responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
                )
            """)
            
            await db.pool.commit()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('ticket_create', self.log_ticket_event)
        event_dispatcher.register('ticket_close', self.log_ticket_event)
        event_dispatcher.register('ticket_response', self.log_ticket_event)

    async def get_guild_settings(self, guild_id: int) -> Dict:
        """Get ticket settings for a guild"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM ticket_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = await cursor.fetchone()
            
            if not data:
                return {
                    'category_id': None,
                    'log_channel_id': None,
                    'support_role_id': None,
                    'max_tickets': 1,
                    'ticket_format': 'ticket-{user}-{number}',
                    'auto_close_hours': 48
                }
                
            return dict(data)

    async def create_ticket_channel(self, ctx, reason: str, settings: Dict) -> Optional[discord.TextChannel]:
        """Create a new ticket channel"""
        # Check max tickets
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT COUNT(*) FROM tickets 
                WHERE guild_id = ? AND user_id = ? AND status = 'open'
            """, (str(ctx.guild.id), str(ctx.author.id)))
            count = (await cursor.fetchone())[0]
            
            if count >= settings['max_tickets']:
                await ctx.send("❌ You have reached the maximum number of open tickets!")
                return None

        # Get category
        category_id = settings.get('category_id')
        category = ctx.guild.get_channel(int(category_id)) if category_id else None
        
        if not category:
            category = await ctx.guild.create_category("Tickets")
            async with db.pool.cursor() as cursor:
                await cursor.execute("""
                    INSERT OR REPLACE INTO ticket_settings (guild_id, category_id)
                    VALUES (?, ?)
                """, (str(ctx.guild.id), str(category.id)))
                await db.pool.commit()

        # Create channel
        ticket_number = count + 1
        channel_name = settings['ticket_format'].format(
            user=ctx.author.name.lower(),
            number=ticket_number
        )

        # Set permissions
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        # Add support role permissions
        if settings['support_role_id']:
            support_role = ctx.guild.get_role(int(settings['support_role_id']))
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True
                )

        # Create the channel
        channel = await category.create_text_channel(
            channel_name,
            overwrites=overwrites
        )

        # Save ticket to database
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO tickets (guild_id, channel_id, user_id, reason)
                VALUES (?, ?, ?, ?)
            """, (str(ctx.guild.id), str(channel.id), str(ctx.author.id), reason))
            ticket_id = cursor.lastrowid
            await db.pool.commit()

        self.active_tickets[channel.id] = ticket_id
        
        return channel

    async def log_ticket_event(self, guild_id: int, event_type: str, data: Dict):
        """Log ticket events to the designated channel"""
        settings = await self.get_guild_settings(guild_id)
        if not settings['log_channel_id']:
            return
            
        log_channel = self.bot.get_channel(int(settings['log_channel_id']))
        if not log_channel:
            return

        embed = Embed.create(
            title=f"🎫 Ticket {event_type.title()}",
            color=discord.Color.blue() if event_type == 'create' else
                  discord.Color.red() if event_type == 'close' else
                  discord.Color.green()
        )

        for key, value in data.items():
            embed.add_field(name=key.title(), value=str(value), inline=True)

        await log_channel.send(embed=embed)

    @commands.group(name="ticket")
    async def ticket(self, ctx):
        """🎫 Ticket management commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @ticket.command(name="create")
    async def create_ticket(self, ctx, *, reason: str = "No reason provided"):
        """Create a new support ticket"""
        settings = await self.get_guild_settings(ctx.guild.id)
        
        channel = await self.create_ticket_channel(ctx, reason, settings)
        if not channel:
            return

        embed = Embed.create(
            title="🎫 Support Ticket",
            description=f"Ticket created by {ctx.author.mention}",
            color=discord.Color.blue(),
            field_Reason=reason,
            field_Instructions="React with 🔒 to close the ticket\nSupport team will assist you shortly."
        )

        msg = await channel.send(embed=embed)
        await msg.add_reaction("🔒")
        
        # Log ticket creation
        await event_dispatcher.dispatch('ticket_create', ctx.guild.id, {
            'User': ctx.author.name,
            'Reason': reason,
            'Channel': channel.name
        })

    @ticket.command(name="close")
    async def close_ticket(self, ctx):
        """Close the current ticket"""
        if not ctx.channel.id in self.active_tickets:
            return await ctx.send("❌ This is not a ticket channel!")

        ticket_id = self.active_tickets[ctx.channel.id]
        
        # Update database
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE tickets 
                SET status = 'closed', 
                    closed_at = CURRENT_TIMESTAMP,
                    closed_by = ?
                WHERE id = ?
            """, (str(ctx.author.id), ticket_id))
            await db.pool.commit()

        # Create transcript
        transcript = await self.create_transcript(ctx.channel)
        
        # Log closure
        await event_dispatcher.dispatch('ticket_close', ctx.guild.id, {
            'Closed By': ctx.author.name,
            'Channel': ctx.channel.name,
            'Duration': self.get_ticket_duration(ticket_id)
        })

        # Delete channel
        await ctx.send("🔒 Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await ctx.channel.delete()

        del self.active_tickets[ctx.channel.id]

    @ticket.command(name="add")
    async def add_user(self, ctx, user: discord.Member):
        """Add a user to the current ticket"""
        if not ctx.channel.id in self.active_tickets:
            return await ctx.send("❌ This is not a ticket channel!")

        await ctx.channel.set_permissions(user, read_messages=True, send_messages=True)
        await ctx.send(f"✅ Added {user.mention} to the ticket")

    @ticket.command(name="remove")
    async def remove_user(self, ctx, user: discord.Member):
        """Remove a user from the current ticket"""
        if not ctx.channel.id in self.active_tickets:
            return await ctx.send("❌ This is not a ticket channel!")

        await ctx.channel.set_permissions(user, overwrite=None)
        await ctx.send(f"✅ Removed {user.mention} from the ticket")

    @commands.group(name="ticketset")
    @commands.has_permissions(administrator=True)
    async def ticketset(self, ctx):
        """⚙️ Ticket system settings"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @ticketset.command(name="supportrole")
    async def set_support_role(self, ctx, role: discord.Role):
        """Set the support team role"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO ticket_settings (guild_id, support_role_id)
                VALUES (?, ?)
            """, (str(ctx.guild.id), str(role.id)))
            await db.pool.commit()
        
        await ctx.send(f"✅ Support role set to {role.mention}")

    @ticketset.command(name="maxtickets")
    async def set_max_tickets(self, ctx, amount: int):
        """Set maximum open tickets per user"""
        if amount < 1:
            return await ctx.send("❌ Amount must be at least 1!")

        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO ticket_settings (guild_id, max_tickets)
                VALUES (?, ?)
            """, (str(ctx.guild.id), amount))
            await db.pool.commit()
        
        await ctx.send(f"✅ Maximum tickets per user set to {amount}")

    @ticketset.command(name="logchannel")
    async def set_log_channel(self, ctx, channel: discord.TextChannel):
        """Set the ticket log channel"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO ticket_settings (guild_id, log_channel_id)
                VALUES (?, ?)
            """, (str(ctx.guild.id), str(channel.id)))
            await db.pool.commit()
        
        await ctx.send(f"✅ Log channel set to {channel.mention}")

    async def create_transcript(self, channel: discord.TextChannel) -> str:
        """Create a transcript of the ticket"""
        messages = []
        async for message in channel.history(limit=None, oldest_first=True):
            messages.append({
                'author': str(message.author),
                'content': message.content,
                'timestamp': message.created_at.strftime('%Y-%m-%d %H:%M:%S')
            })

        return json.dumps(messages, indent=2)

    def get_ticket_duration(self, ticket_id: int) -> str:
        """Get the duration of a ticket"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT created_at, closed_at FROM tickets WHERE id = ?
            """, (ticket_id,))
            data = await cursor.fetchone()
            
            if not data or not data['closed_at']:
                return "Unknown"
                
            created = datetime.strptime(data['created_at'], '%Y-%m-%d %H:%M:%S')
            closed = datetime.strptime(data['closed_at'], '%Y-%m-%d %H:%M:%S')
            duration = closed - created
            
            return str(duration).split('.')[0]

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle ticket reactions"""
        if payload.user_id == self.bot.user.id:
            return

        if str(payload.emoji) != "🔒":
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel.id in self.active_tickets:
            return

        ctx = await self.bot.get_context(await channel.fetch_message(payload.message_id))
        await self.close_ticket(ctx)

async def setup(bot):
    """Setup the Ticket cog"""
    cog = TicketSystem(bot)
    await cog.setup_tables()
    await bot.add_cog(cog)