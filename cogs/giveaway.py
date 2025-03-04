import discord
from discord.ext import commands, tasks
import asyncio
from datetime import datetime, timedelta
import random
from typing import Optional, List, Dict
from .utils import Embed, db, event_dispatcher

class Giveaway(commands.Cog):
    """🎉 Advanced Giveaway System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.active_giveaways = {}
        self.check_giveaways.start()
        self.register_handlers()

    async def setup_tables(self):
        """Setup necessary database tables"""
        async with db.pool.cursor() as cursor:
            # Giveaway settings
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS giveaway_settings (
                    guild_id TEXT PRIMARY KEY,
                    default_duration INTEGER DEFAULT 3600,
                    default_winners INTEGER DEFAULT 1,
                    manager_role TEXT,
                    announcement_channel TEXT,
                    required_role TEXT,
                    blacklisted_roles TEXT
                )
            """)
            
            # Active giveaways
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS giveaways (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    host_id TEXT NOT NULL,
                    prize TEXT NOT NULL,
                    description TEXT,
                    winners_count INTEGER DEFAULT 1,
                    end_time DATETIME NOT NULL,
                    required_role TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Giveaway entries
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS giveaway_entries (
                    giveaway_id INTEGER,
                    user_id TEXT NOT NULL,
                    entry_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (giveaway_id) REFERENCES giveaways (id),
                    UNIQUE (giveaway_id, user_id)
                )
            """)
            
            await db.pool.commit()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('giveaway_start', self.log_giveaway_start)
        event_dispatcher.register('giveaway_end', self.log_giveaway_end)

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        self.check_giveaways.cancel()

    async def get_settings(self, guild_id: int) -> Dict:
        """Get giveaway settings for a guild"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM giveaway_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = await cursor.fetchone()
            
            if not data:
                default_settings = {
                    'default_duration': 3600,
                    'default_winners': 1,
                    'manager_role': None,
                    'announcement_channel': None,
                    'required_role': None,
                    'blacklisted_roles': ''
                }
                
                await cursor.execute("""
                    INSERT INTO giveaway_settings
                    (guild_id, default_duration, default_winners)
                    VALUES (?, ?, ?)
                """, (str(guild_id), 3600, 1))
                await db.pool.commit()
                return default_settings
                
            return dict(data)

    @tasks.loop(seconds=30)
    async def check_giveaways(self):
        """Check for ended giveaways"""
        current_time = datetime.utcnow()
        
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM giveaways
                WHERE is_active = TRUE AND end_time <= ?
            """, (current_time.strftime('%Y-%m-%d %H:%M:%S'),))
            ended_giveaways = await cursor.fetchall()
            
        for giveaway in ended_giveaways:
            await self.end_giveaway(giveaway['id'])

    @check_giveaways.before_loop
    async def before_check_giveaways(self):
        """Wait until bot is ready"""
        await self.bot.wait_until_ready()

    async def end_giveaway(self, giveaway_id: int):
        """End a giveaway and select winners"""
        async with db.pool.cursor() as cursor:
            # Get giveaway data
            await cursor.execute("""
                SELECT * FROM giveaways WHERE id = ?
            """, (giveaway_id,))
            giveaway = await cursor.fetchone()
            
            if not giveaway or not giveaway['is_active']:
                return
                
            # Get entries
            await cursor.execute("""
                SELECT user_id FROM giveaway_entries
                WHERE giveaway_id = ?
            """, (giveaway_id,))
            entries = await cursor.fetchall()
            
            # Mark giveaway as inactive
            await cursor.execute("""
                UPDATE giveaways
                SET is_active = FALSE
                WHERE id = ?
            """, (giveaway_id,))
            await db.pool.commit()

        if not entries:
            # No participants
            channel = self.bot.get_channel(int(giveaway['channel_id']))
            if channel:
                try:
                    message = await channel.fetch_message(int(giveaway['message_id']))
                    embed = message.embeds[0]
                    embed.description = f"🎉 Giveaway Ended!\n\nNo valid participants!"
                    embed.color = discord.Color.red()
                    await message.edit(embed=embed)
                    await channel.send("❌ No valid participants in the giveaway!")
                except (discord.NotFound, discord.Forbidden, IndexError):
                    pass
            return

        # Select winners
        winners_count = min(giveaway['winners_count'], len(entries))
        winner_ids = random.sample([entry['user_id'] for entry in entries], winners_count)
        
        # Update embed
        channel = self.bot.get_channel(int(giveaway['channel_id']))
        if channel:
            try:
                message = await channel.fetch_message(int(giveaway['message_id']))
                embed = message.embeds[0]
                
                winners_text = "\n".join([f"<@{winner_id}>" for winner_id in winner_ids])
                embed.description = f"🎉 Giveaway Ended!\n\n**Winners:**\n{winners_text}"
                embed.color = discord.Color.green()
                
                await message.edit(embed=embed)
                
                # Announce winners
                winners_mention = ", ".join([f"<@{winner_id}>" for winner_id in winner_ids])
                await channel.send(
                    f"🎉 Congratulations {winners_mention}! "
                    f"You won the giveaway for **{giveaway['prize']}**!"
                )
            except (discord.NotFound, discord.Forbidden, IndexError):
                pass

    @commands.group(name="giveaway", aliases=["g"])
    async def giveaway(self, ctx):
        """🎉 Giveaway commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @giveaway.command(name="start")
    @commands.has_permissions(manage_guild=True)
    async def start_giveaway(self, ctx, duration: str, winners: int, *, prize: str):
        """Start a new giveaway
        Duration format: 30s, 5m, 2h, 1d"""
        
        # Parse duration
        try:
            duration_seconds = 0
            duration_value = int(duration[:-1])
            duration_unit = duration[-1].lower()
            
            if duration_unit == 's':
                duration_seconds = duration_value
            elif duration_unit == 'm':
                duration_seconds = duration_value * 60
            elif duration_unit == 'h':
                duration_seconds = duration_value * 3600
            elif duration_unit == 'd':
                duration_seconds = duration_value * 86400
            else:
                return await ctx.send("❌ Invalid duration format! Use s/m/h/d")
                
            if duration_seconds < 30:
                return await ctx.send("❌ Duration must be at least 30 seconds!")
        except ValueError:
            return await ctx.send("❌ Invalid duration format!")

        if winners < 1:
            return await ctx.send("❌ Must have at least 1 winner!")

        end_time = datetime.utcnow() + timedelta(seconds=duration_seconds)
        
        # Create giveaway embed
        embed = Embed.create(
            title="🎉 New Giveaway!",
            description=(
                f"**Prize:** {prize}\n\n"
                f"React with 🎉 to enter!\n\n"
                f"Time Remaining: <t:{int(end_time.timestamp())}:R>"
            ),
            field_Host=ctx.author.mention,
            field_Winners=str(winners),
            field_Ends=f"<t:{int(end_time.timestamp())}:F>",
            color=discord.Color.blue()
        )
        
        # Send giveaway message
        message = await ctx.send(embed=embed)
        await message.add_reaction("🎉")
        
        # Save to database
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO giveaways (
                    guild_id, channel_id, message_id, host_id,
                    prize, winners_count, end_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                str(ctx.guild.id),
                str(ctx.channel.id),
                str(message.id),
                str(ctx.author.id),
                prize,
                winners,
                end_time.strftime('%Y-%m-%d %H:%M:%S')
            ))
            giveaway_id = cursor.lastrowid
            await db.pool.commit()

        self.active_giveaways[message.id] = giveaway_id

    @giveaway.command(name="end")
    @commands.has_permissions(manage_guild=True)
    async def end_giveaway_command(self, ctx, message_id: int):
        """End a giveaway early"""
        try:
            message = await ctx.channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send("❌ Giveaway not found!")

        if message_id not in self.active_giveaways:
            return await ctx.send("❌ This giveaway is not active!")

        await self.end_giveaway(self.active_giveaways[message_id])
        await ctx.send("✅ Giveaway ended successfully!")

    @giveaway.command(name="reroll")
    @commands.has_permissions(manage_guild=True)
    async def reroll_giveaway(self, ctx, message_id: int, winners: int = 1):
        """Reroll a giveaway with new winners"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM giveaways WHERE message_id = ?
            """, (str(message_id),))
            giveaway = await cursor.fetchone()
            
            if not giveaway:
                return await ctx.send("❌ Giveaway not found!")
            
            # Get entries
            await cursor.execute("""
                SELECT user_id FROM giveaway_entries
                WHERE giveaway_id = ?
            """, (giveaway['id'],))
            entries = await cursor.fetchall()
            
        if not entries:
            return await ctx.send("❌ No participants found!")
            
        # Select new winners
        winners_count = min(winners, len(entries))
        winner_ids = random.sample([entry['user_id'] for entry in entries], winners_count)
        
        winners_mention = ", ".join([f"<@{winner_id}>" for winner_id in winner_ids])
        await ctx.send(
            f"🎉 New winners for **{giveaway['prize']}**: {winners_mention}! "
            f"Congratulations!"
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle giveaway entries"""
        if payload.user_id == self.bot.user.id:
            return

        if str(payload.emoji) != "🎉":
            return

        if payload.message_id not in self.active_giveaways:
            return

        giveaway_id = self.active_giveaways[payload.message_id]
        
        # Record entry
        try:
            async with db.pool.cursor() as cursor:
                await cursor.execute("""
                    INSERT OR IGNORE INTO giveaway_entries 
                    (giveaway_id, user_id)
                    VALUES (?, ?)
                """, (giveaway_id, str(payload.user_id)))
                await db.pool.commit()
        except Exception as e:
            print(f"Error recording giveaway entry: {e}")

async def setup(bot):
    """Setup the Giveaway cog"""
    cog = Giveaway(bot)
    await cog.setup_tables()
    await bot.add_cog(cog)