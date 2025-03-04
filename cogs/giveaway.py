import discord
from discord.ext import commands, tasks
import sqlite3
import random
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from .utils import Embed, event_dispatcher
from database import get_connection
import logging

logger = logging.getLogger(__name__)

class Giveaway(commands.Cog):
    """🎉 Advanced Giveaway System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.active_giveaways = {}
        self.check_giveaways.start()
        self.register_handlers()

    def setup_tables(self):
        """Setup necessary database tables"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Giveaways table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS giveaways (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    host_id TEXT NOT NULL,
                    prize TEXT NOT NULL,
                    winners INTEGER DEFAULT 1,
                    entries INTEGER DEFAULT 0,
                    requirements TEXT,
                    end_time DATETIME NOT NULL,
                    ended BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Giveaway entries table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS giveaway_entries (
                    giveaway_id INTEGER,
                    user_id TEXT NOT NULL,
                    entries INTEGER DEFAULT 1,
                    entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (giveaway_id, user_id),
                    FOREIGN KEY (giveaway_id) REFERENCES giveaways (id) ON DELETE CASCADE
                )
            """)
            
            # Giveaway settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS giveaway_settings (
                    guild_id TEXT PRIMARY KEY,
                    manager_role TEXT,
                    default_duration INTEGER DEFAULT 86400,
                    minimum_duration INTEGER DEFAULT 300,
                    maximum_duration INTEGER DEFAULT 2592000,
                    maximum_winners INTEGER DEFAULT 20,
                    bypass_roles TEXT,
                    required_roles TEXT,
                    blacklisted_roles TEXT
                )
            """)
            
            conn.commit()
            logger.info("Giveaway tables created successfully")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to setup giveaway tables: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('giveaway_end', self.handle_giveaway_end)
        event_dispatcher.register('giveaway_reroll', self.handle_reroll)

    def get_settings(self, guild_id: int) -> Dict:
        """Get giveaway settings for a guild"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM giveaway_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = cursor.fetchone()
            
            if not data:
                default_settings = {
                    'manager_role': None,
                    'default_duration': 86400,
                    'minimum_duration': 300,
                    'maximum_duration': 2592000,
                    'maximum_winners': 20,
                    'bypass_roles': None,
                    'required_roles': None,
                    'blacklisted_roles': None
                }
                
                cursor.execute("""
                    INSERT INTO giveaway_settings (guild_id)
                    VALUES (?)
                """, (str(guild_id),))
                conn.commit()
                return default_settings
                
            return dict(data)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to get giveaway settings: {e}")
            raise
        finally:
            if conn:
                conn.close()

    @tasks.loop(seconds=30)
    async def check_giveaways(self):
        """Check for ended giveaways"""
        current_time = datetime.utcnow()
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM giveaways
                WHERE ended = FALSE AND end_time <= ?
            """, (current_time.strftime('%Y-%m-%d %H:%M:%S'),))
            ended_giveaways = cursor.fetchall()
            
            for giveaway in ended_giveaways:
                await self.end_giveaway(giveaway['id'])
                
        except sqlite3.Error as e:
            logger.error(f"Failed to check giveaways: {e}")
        finally:
            if conn:
                conn.close()

    @check_giveaways.before_loop
    async def before_check_giveaways(self):
        """Wait until bot is ready"""
        await self.bot.wait_until_ready()

    async def end_giveaway(self, giveaway_id: int):
        """End a giveaway and select winners"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Get giveaway data
            cursor.execute("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))
            giveaway = cursor.fetchone()
            
            if not giveaway or giveaway['ended']:
                return
            
            # Get entries
            cursor.execute("""
                SELECT user_id, entries FROM giveaway_entries
                WHERE giveaway_id = ?
            """, (giveaway_id,))
            entries = cursor.fetchall()
            
            # Select winners
            winners = []
            if entries:
                weighted_entries = []
                for entry in entries:
                    weighted_entries.extend([entry['user_id']] * entry['entries'])
                    
                num_winners = min(giveaway['winners'], len(set(weighted_entries)))
                winners = random.sample(weighted_entries, num_winners)
            
            # Mark giveaway as ended
            cursor.execute("""
                UPDATE giveaways
                SET ended = TRUE
                WHERE id = ?
            """, (giveaway_id,))
            conn.commit()
            
            # Send winner announcement
            channel = self.bot.get_channel(int(giveaway['channel_id']))
            if channel:
                message = await channel.fetch_message(int(giveaway['message_id']))
                if message:
                    if winners:
                        winner_mentions = [f"<@{winner}>" for winner in set(winners)]
                        win_message = f"🎉 Congratulations {', '.join(winner_mentions)}! You won: **{giveaway['prize']}**"
                        await message.reply(win_message)
                    else:
                        await message.reply("❌ No valid entries for this giveaway!")
                    
                    # Update embed
                    embed = message.embeds[0]
                    embed.color = discord.Color.greyple()
                    embed.description = "🎉 Giveaway Ended!"
                    
                    if winners:
                        embed.add_field(
                            name="Winners",
                            value="\n".join(f"<@{winner}>" for winner in set(winners)),
                            inline=False
                        )
                    
                    await message.edit(embed=embed)
            
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Failed to handle giveaway end message: {e}")
        except sqlite3.Error as e:
            logger.error(f"Failed to end giveaway: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    @commands.group(name="giveaway", aliases=["g"])
    async def giveaway(self, ctx):
        """🎉 Giveaway commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @giveaway.command(name="start")
    @commands.has_permissions(manage_guild=True)
    async def start_giveaway(self, ctx, duration: str, winners: int, *, prize: str):
        """Start a giveaway"""
        settings = self.get_settings(ctx.guild.id)
        
        # Parse duration
        try:
            duration_seconds = self.parse_duration(duration)
            if duration_seconds < settings['minimum_duration']:
                return await ctx.send(f"❌ Duration must be at least {settings['minimum_duration']} seconds!")
            if duration_seconds > settings['maximum_duration']:
                return await ctx.send(f"❌ Duration cannot exceed {settings['maximum_duration']} seconds!")
        except ValueError:
            return await ctx.send("❌ Invalid duration format! Use: 1d, 12h, 30m, etc.")
            
        if winners < 1 or winners > settings['maximum_winners']:
            return await ctx.send(f"❌ Number of winners must be between 1 and {settings['maximum_winners']}!")
            
        end_time = datetime.utcnow() + timedelta(seconds=duration_seconds)
        
        # Create embed
        embed = Embed.create(
            title="🎉 New Giveaway!",
            description=f"React with 🎉 to enter!\nEnds: <t:{int(end_time.timestamp())}:R>",
            color=discord.Color.blue(),
            field_Prize=prize,
            field_Winners=str(winners),
            field_Host=ctx.author.mention
        )
        
        message = await ctx.send(embed=embed)
        await message.add_reaction("🎉")
        
        # Save giveaway
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO giveaways
                (guild_id, channel_id, message_id, host_id, prize, winners, end_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                str(ctx.guild.id),
                str(ctx.channel.id),
                str(message.id),
                str(ctx.author.id),
                prize,
                winners,
                end_time.strftime('%Y-%m-%d %H:%M:%S')
            ))
            conn.commit()
            
        except sqlite3.Error as e:
            logger.error(f"Failed to save giveaway: {e}")
            await ctx.send("❌ An error occurred while creating the giveaway")
            if message:
                await message.delete()
        finally:
            if conn:
                conn.close()

    @giveaway.command(name="end")
    @commands.has_permissions(manage_guild=True)
    async def end_giveaway_command(self, ctx, message_id: int):
        """End a giveaway early"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id FROM giveaways
                WHERE message_id = ? AND guild_id = ? AND ended = FALSE
            """, (str(message_id), str(ctx.guild.id)))
            data = cursor.fetchone()
            
            if not data:
                return await ctx.send("❌ Giveaway not found or already ended!")
                
            await self.end_giveaway(data['id'])
            await ctx.send("✅ Giveaway ended!")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to end giveaway: {e}")
            await ctx.send("❌ An error occurred while ending the giveaway")
        finally:
            if conn:
                conn.close()

    @giveaway.command(name="reroll")
    @commands.has_permissions(manage_guild=True)
    async def reroll_giveaway(self, ctx, message_id: int, winners: int = 1):
        """Reroll giveaway winners"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM giveaways
                WHERE message_id = ? AND guild_id = ? AND ended = TRUE
            """, (str(message_id), str(ctx.guild.id)))
            giveaway = cursor.fetchone()
            
            if not giveaway:
                return await ctx.send("❌ Ended giveaway not found!")
                
            cursor.execute("""
                SELECT user_id, entries FROM giveaway_entries
                WHERE giveaway_id = ?
            """, (giveaway['id'],))
            entries = cursor.fetchall()
            
            if not entries:
                return await ctx.send("❌ No entries found for this giveaway!")
                
            # Select new winners
            weighted_entries = []
            for entry in entries:
                weighted_entries.extend([entry['user_id']] * entry['entries'])
                
            num_winners = min(winners, len(set(weighted_entries)))
            new_winners = random.sample(weighted_entries, num_winners)
            
            if new_winners:
                winner_mentions = [f"<@{winner}>" for winner in set(new_winners)]
                await ctx.send(
                    f"🎉 New winners for **{giveaway['prize']}**: {', '.join(winner_mentions)}"
                )
            else:
                await ctx.send("❌ Could not determine new winners!")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to reroll giveaway: {e}")
            await ctx.send("❌ An error occurred while rerolling the giveaway")
        finally:
            if conn:
                conn.close()

    @giveaway.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def list_giveaways(self, ctx):
        """List active giveaways"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM giveaways
                WHERE guild_id = ? AND ended = FALSE
                ORDER BY end_time ASC
            """, (str(ctx.guild.id),))
            giveaways = cursor.fetchall()
            
            if not giveaways:
                return await ctx.send("❌ No active giveaways!")
                
            embed = Embed.create(
                title="🎉 Active Giveaways",
                color=discord.Color.blue()
            )
            
            for g in giveaways:
                end_time = datetime.strptime(g['end_time'], '%Y-%m-%d %H:%M:%S')
                embed.add_field(
                    name=g['prize'],
                    value=f"ID: {g['message_id']}\n"
                          f"Winners: {g['winners']}\n"
                          f"Ends: <t:{int(end_time.timestamp())}:R>",
                    inline=False
                )
                
            await ctx.send(embed=embed)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to list giveaways: {e}")
            await ctx.send("❌ An error occurred while getting giveaways")
        finally:
            if conn:
                conn.close()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle giveaway entries"""
        if payload.emoji.name != "🎉" or payload.member.bot:
            return
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM giveaways
                WHERE channel_id = ? AND message_id = ? AND ended = FALSE
            """, (str(payload.channel_id), str(payload.message_id)))
            giveaway = cursor.fetchone()
            
            if not giveaway:
                return
                
            # Check requirements
            settings = self.get_settings(payload.guild_id)
            if settings['required_roles']:
                required = settings['required_roles'].split(',')
                if not any(str(role.id) in required for role in payload.member.roles):
                    return
                    
            if settings['blacklisted_roles']:
                blacklisted = settings['blacklisted_roles'].split(',')
                if any(str(role.id) in blacklisted for role in payload.member.roles):
                    return
            
            # Add entry
            cursor.execute("""
                INSERT OR REPLACE INTO giveaway_entries
                (giveaway_id, user_id, entries)
                VALUES (?, ?, 
                    COALESCE(
                        (SELECT entries + 1 FROM giveaway_entries 
                        WHERE giveaway_id = ? AND user_id = ?),
                        1
                    )
                )
            """, (
                giveaway['id'], 
                str(payload.user_id),
                giveaway['id'],
                str(payload.user_id)
            ))
            
            # Update entry count
            cursor.execute("""
                UPDATE giveaways
                SET entries = (
                    SELECT COUNT(*) FROM giveaway_entries
                    WHERE giveaway_id = ?
                )
                WHERE id = ?
            """, (giveaway['id'], giveaway['id']))
            
            conn.commit()
            
        except sqlite3.Error as e:
            logger.error(f"Failed to handle giveaway entry: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        """Handle giveaway entry removals"""
        if payload.emoji.name != "🎉":
            return
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM giveaways
                WHERE channel_id = ? AND message_id = ? AND ended = FALSE
            """, (str(payload.channel_id), str(payload.message_id)))
            giveaway = cursor.fetchone()
            
            if not giveaway:
                return
                
            # Remove entry
            cursor.execute("""
                DELETE FROM giveaway_entries
                WHERE giveaway_id = ? AND user_id = ?
            """, (giveaway['id'], str(payload.user_id)))
            
            # Update entry count
            cursor.execute("""
                UPDATE giveaways
                SET entries = (
                    SELECT COUNT(*) FROM giveaway_entries
                    WHERE giveaway_id = ?
                )
                WHERE id = ?
            """, (giveaway['id'], giveaway['id']))
            
            conn.commit()
            
        except sqlite3.Error as e:
            logger.error(f"Failed to handle giveaway entry removal: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    def parse_duration(self, duration: str) -> int:
        """Parse duration string into seconds"""
        total_seconds = 0
        current = ''
        
        duration_units = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400,
            'w': 604800
        }
        
        for char in duration.lower():
            if char.isdigit():
                current += char
            elif char in duration_units:
                if current:
                    total_seconds += int(current) * duration_units[char]
                    current = ''
                    
        if not total_seconds:
            raise ValueError("Invalid duration format")
            
        return total_seconds

    @commands.group(name="giveawayset", aliases=["gset"])
    @commands.has_permissions(administrator=True)
    async def giveawayset(self, ctx):
        """⚙️ Giveaway system settings"""
        if ctx.invoked_subcommand is None:
            settings = self.get_settings(ctx.guild.id)
            
            embed = Embed.create(
                title="⚙️ Giveaway Settings",
                color=discord.Color.blue(),
                field_Manager_Role=f"<@&{settings['manager_role']}>" if settings['manager_role'] else "None",
                field_Default_Duration=f"{settings['default_duration']} seconds",
                field_Min_Duration=f"{settings['minimum_duration']} seconds",
                field_Max_Duration=f"{settings['maximum_duration']} seconds",
                field_Max_Winners=str(settings['maximum_winners'])
            )
            
            if settings['required_roles']:
                roles = [f"<@&{role}>" for role in settings['required_roles'].split(',')]
                embed.add_field(name="Required Roles", value="\n".join(roles))
                
            if settings['blacklisted_roles']:
                roles = [f"<@&{role}>" for role in settings['blacklisted_roles'].split(',')]
                embed.add_field(name="Blacklisted Roles", value="\n".join(roles))
                
            await ctx.send(embed=embed)

    @giveawayset.command(name="manager")
    async def set_manager_role(self, ctx, role: discord.Role = None):
        """Set giveaway manager role"""
        role_id = str(role.id) if role else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE giveaway_settings
                SET manager_role = ?
                WHERE guild_id = ?
            """, (role_id, str(ctx.guild.id)))
            conn.commit()
            
            if role:
                await ctx.send(f"✅ Giveaway manager role set to {role.mention}")
            else:
                await ctx.send("✅ Giveaway manager role removed")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to set manager role: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @giveawayset.command(name="required")
    async def set_required_roles(self, ctx, *roles: discord.Role):
        """Set required roles for giveaway entry"""
        role_ids = ','.join(str(role.id) for role in roles) if roles else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE giveaway_settings
                SET required_roles = ?
                WHERE guild_id = ?
            """, (role_ids, str(ctx.guild.id)))
            conn.commit()
            
            if roles:
                role_mentions = ' '.join(role.mention for role in roles)
                await ctx.send(f"✅ Required roles set to: {role_mentions}")
            else:
                await ctx.send("✅ Required roles cleared")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to set required roles: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @giveawayset.command(name="blacklist")
    async def set_blacklisted_roles(self, ctx, *roles: discord.Role):
        """Set blacklisted roles for giveaway entry"""
        role_ids = ','.join(str(role.id) for role in roles) if roles else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE giveaway_settings
                SET blacklisted_roles = ?
                WHERE guild_id = ?
            """, (role_ids, str(ctx.guild.id)))
            conn.commit()
            
            if roles:
                role_mentions = ' '.join(role.mention for role in roles)
                await ctx.send(f"✅ Blacklisted roles set to: {role_mentions}")
            else:
                await ctx.send("✅ Blacklisted roles cleared")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to set blacklisted roles: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

async def setup(bot):
    """Setup the Giveaway cog"""
    cog = Giveaway(bot)
    cog.setup_tables()
    await bot.add_cog(cog)
