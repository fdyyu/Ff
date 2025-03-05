import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta
from typing import Optional
from .utils import Embed, event_dispatcher
from database import get_connection
import logging
import asyncio

logger = logging.getLogger(__name__)

class Reminders(commands.Cog):
    """⏰ Reminder System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.setup_tables()
        self.check_reminders.start()
        self.register_handlers()

    def setup_tables(self):
        """Setup necessary database tables"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Drop existing reminders table if exists
            cursor.execute("DROP TABLE IF EXISTS reminders")
            
            # Create new reminders table with correct structure
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    trigger_time DATETIME NOT NULL,
                    repeat_interval TEXT,
                    last_triggered DATETIME,
                    mentions TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create reminders settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminder_settings (
                    guild_id TEXT PRIMARY KEY,
                    max_reminders INTEGER DEFAULT 25,
                    max_duration INTEGER DEFAULT 31536000,
                    reminder_channel TEXT,
                    timezone TEXT DEFAULT 'UTC',
                    mention_roles BOOLEAN DEFAULT FALSE,
                    allow_everyone BOOLEAN DEFAULT FALSE
                )
            """)

            # Create reminder templates table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminder_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    message TEXT NOT NULL,
                    duration TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, name)
                )
            """)

            # Create indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_guild 
                ON reminders(guild_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_user 
                ON reminders(user_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_trigger 
                ON reminders(trigger_time)
            """)

            conn.commit()
            logger.info("Reminder tables created successfully")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to setup reminder tables: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('reminder_triggered', self.handle_reminder_trigger)

    @tasks.loop(seconds=30)
    async def check_reminders(self):
        """Check for reminders that need to be triggered"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Get all active reminders that need to be triggered
            cursor.execute("""
                SELECT * FROM reminders 
                WHERE is_active = TRUE 
                AND trigger_time <= datetime('now')
                AND (last_triggered IS NULL OR 
                    datetime(last_triggered, '+' || repeat_interval) <= datetime('now'))
            """)
            
            reminders = cursor.fetchall()
            
            for reminder in reminders:
                try:
                    channel = self.bot.get_channel(int(reminder['channel_id']))
                    if not channel:
                        continue
                        
                    # Send reminder message
                    mentions = []
                    if reminder['mentions']:
                        for mention_id in reminder['mentions'].split(','):
                            try:
                                member = channel.guild.get_member(int(mention_id))
                                if member:
                                    mentions.append(member.mention)
                            except ValueError:
                                continue
                    
                    mention_str = ' '.join(mentions) if mentions else ''
                    await channel.send(
                        f"⏰ Reminder {mention_str}\n{reminder['message']}"
                    )
                    
                    # Update last triggered time for repeating reminders
                    if reminder['repeat_interval']:
                        cursor.execute("""
                            UPDATE reminders 
                            SET last_triggered = datetime('now')
                            WHERE id = ?
                        """, (reminder['id'],))
                    else:
                        # Deactivate non-repeating reminders
                        cursor.execute("""
                            UPDATE reminders 
                            SET is_active = FALSE
                            WHERE id = ?
                        """, (reminder['id'],))
                        
                except Exception as e:
                    logger.error(f"Error sending reminder {reminder['id']}: {e}")
                    continue
                    
            conn.commit()
            
        except sqlite3.Error as e:
            logger.error(f"Failed to check reminders: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    @check_reminders.before_loop
    async def before_check_reminders(self):
        """Wait until bot is ready before starting reminder check loop"""
        await self.bot.wait_until_ready()

    @commands.group(name="reminder", aliases=["remind"])
    async def reminder(self, ctx):
        """⏰ Reminder commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @reminder.command(name="add", aliases=["set"])
    async def add_reminder(self, ctx, time: str, *, message: str):
        """Add a new reminder"""
        try:
            # Parse time string
            trigger_time = self.parse_time(time)
            if not trigger_time:
                return await ctx.send("❌ Invalid time format! Use format like '1h30m' or '2d'")
                
            settings = await self.get_settings(ctx.guild.id)
            
            # Check max duration
            duration = (trigger_time - datetime.utcnow()).total_seconds()
            if duration > settings['max_duration']:
                return await ctx.send(
                    f"❌ Reminder duration cannot exceed "
                    f"{settings['max_duration'] // 86400} days!"
                )
                
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                # Check max reminders
                cursor.execute("""
                    SELECT COUNT(*) as count FROM reminders
                    WHERE guild_id = ? AND user_id = ? AND is_active = TRUE
                """, (str(ctx.guild.id), str(ctx.author.id)))
                
                if cursor.fetchone()['count'] >= settings['max_reminders']:
                    return await ctx.send(
                        f"❌ You can only have {settings['max_reminders']} "
                        "active reminders at a time!"
                    )
                
                # Add reminder
                cursor.execute("""
                    INSERT INTO reminders (
                        guild_id, channel_id, user_id, message, 
                        trigger_time, mentions
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    str(ctx.guild.id),
                    str(ctx.channel.id),
                    str(ctx.author.id),
                    message,
                    trigger_time.strftime('%Y-%m-%d %H:%M:%S'),
                    str(ctx.author.id)
                ))
                
                conn.commit()
                
                await ctx.send(
                    f"✅ I'll remind you about that on "
                    f"<t:{int(trigger_time.timestamp())}:F>"
                )
                
            except sqlite3.Error as e:
                logger.error(f"Failed to add reminder: {e}")
                await ctx.send("❌ Failed to add reminder")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    conn.close()
                    
        except Exception as e:
            logger.error(f"Error adding reminder: {e}")
            await ctx.send("❌ An error occurred")

    def parse_time(self, time_str: str) -> Optional[datetime]:
        """Parse time string into datetime"""
        try:
            total_seconds = 0
            current = ''
            
            for char in time_str:
                if char.isdigit():
                    current += char
                elif char.lower() in ['d', 'h', 'm', 's']:
                    if not current:
                        continue
                    num = int(current)
                    current = ''
                    
                    if char.lower() == 'd':
                        total_seconds += num * 86400
                    elif char.lower() == 'h':
                        total_seconds += num * 3600
                    elif char.lower() == 'm':
                        total_seconds += num * 60
                    elif char.lower() == 's':
                        total_seconds += num
                        
            if total_seconds > 0:
                return datetime.utcnow() + timedelta(seconds=total_seconds)
                
        except ValueError:
            pass
            
        return None

    async def get_settings(self, guild_id: int) -> dict:
        """Get reminder settings for a guild"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM reminder_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = cursor.fetchone()
            
            if not data:
                cursor.execute("""
                    INSERT INTO reminder_settings (guild_id)
                    VALUES (?)
                """, (str(guild_id),))
                conn.commit()
                
                return {
                    'max_reminders': 25,
                    'max_duration': 31536000,
                    'reminder_channel': None,
                    'timezone': 'UTC',
                    'mention_roles': False,
                    'allow_everyone': False
                }
                
            return dict(data)
            
        finally:
            if conn:
                conn.close()

    @reminder.command(name="list")
    async def list_reminders(self, ctx):
        """List your active reminders"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM reminders
                WHERE guild_id = ? AND user_id = ? AND is_active = TRUE
                ORDER BY trigger_time ASC
            """, (str(ctx.guild.id), str(ctx.author.id)))
            
            reminders = cursor.fetchall()
            
            if not reminders:
                return await ctx.send("❌ You have no active reminders!")
                
            embed = discord.Embed(
                title="📋 Your Active Reminders",
                color=discord.Color.blue()
            )
            
            for reminder in reminders:
                trigger_time = datetime.strptime(
                    reminder['trigger_time'],
                    '%Y-%m-%d %H:%M:%S'
                )
                
                embed.add_field(
                    name=f"ID: {reminder['id']}",
                    value=f"Message: {reminder['message']}\n"
                          f"Triggers: <t:{int(trigger_time.timestamp())}:R>",
                    inline=False
                )
                
            await ctx.send(embed=embed)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to list reminders: {e}")
            await ctx.send("❌ Failed to list reminders")
        finally:
            if conn:
                conn.close()

    @reminder.command(name="remove", aliases=["delete"])
    async def remove_reminder(self, ctx, reminder_id: int):
        """Remove a reminder"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM reminders
                WHERE id = ? AND guild_id = ? AND user_id = ?
            """, (reminder_id, str(ctx.guild.id), str(ctx.author.id)))
            
            if cursor.rowcount > 0:
                conn.commit()
                await ctx.send("✅ Reminder removed!")
            else:
                await ctx.send("❌ Reminder not found!")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to remove reminder: {e}")
            await ctx.send("❌ Failed to remove reminder")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    @reminder.command(name="clear")
    async def clear_reminders(self, ctx):
        """Clear all your reminders"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM reminders
                WHERE guild_id = ? AND user_id = ?
            """, (str(ctx.guild.id), str(ctx.author.id)))
            
            conn.commit()
            
            await ctx.send("✅ All your reminders have been cleared!")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to clear reminders: {e}")
            await ctx.send("❌ Failed to clear reminders")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

async def setup(bot):
    """Setup the Reminders cog"""
    await bot.add_cog(Reminders(bot))