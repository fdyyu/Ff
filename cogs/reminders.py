import discord
from discord.ext import commands
import sqlite3
from datetime import datetime, timedelta
import asyncio
from typing import Optional, Dict, List
from .utils import Embed, event_dispatcher
from database import get_connection
import logging
import re

logger = logging.getLogger(__name__)

class Reminders(commands.Cog):
    """⏰ Advanced Reminder System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.active_reminders = {}
        self.check_reminders.start()
        self.register_handlers()

    def setup_tables(self):
        """Setup necessary database tables"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Reminders table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    trigger_time DATETIME NOT NULL,
                    repeat_interval INTEGER,
                    repeat_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Reminder settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminder_settings (
                    guild_id TEXT PRIMARY KEY,
                    max_reminders INTEGER DEFAULT 10,
                    max_duration INTEGER DEFAULT 2592000,
                    default_channel TEXT,
                    manager_role TEXT
                )
            """)
            
            conn.commit()
            logger.info("Reminder tables created successfully")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to setup reminder tables: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('reminder_trigger', self.handle_reminder_trigger)

    async def handle_reminder_trigger(self, reminder_data):
        """Handle reminder triggering"""
        try:
            guild = self.bot.get_guild(int(reminder_data["guild_id"]))
            if not guild:
                return

            channel = guild.get_channel(int(reminder_data["channel_id"]))
            if not channel:
                return

            user = guild.get_member(int(reminder_data["user_id"]))
            if not user:
                return

            # Create embed for reminder
            embed = Embed.create(
                title="⏰ Reminder!",
                description=reminder_data["message"],
                color=discord.Color.blue(),
                field_Created_by=user.mention,
                field_Created_at=f"<t:{int(datetime.strptime(reminder_data['created_at'], '%Y-%m-%d %H:%M:%S').timestamp())}:R>"
            )

            # Send reminder
            try:
                await channel.send(
                    content=f"{user.mention}, here's your reminder!",
                    embed=embed
                )
            except discord.Forbidden:
                try:
                    await user.send(
                        content="I couldn't send your reminder in the original channel!",
                        embed=embed
                    )
                except discord.Forbidden:
                    logger.error(f"Could not send reminder to user {user.id}")
                    return

            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()

                # Handle repeating reminders
                if reminder_data.get("repeat_interval"):
                    next_trigger = datetime.strptime(reminder_data["trigger_time"], '%Y-%m-%d %H:%M:%S') + \
                                 timedelta(seconds=reminder_data["repeat_interval"])
                    
                    cursor.execute("""
                        UPDATE reminders
                        SET trigger_time = ?, repeat_count = repeat_count + 1
                        WHERE id = ?
                    """, (next_trigger.strftime('%Y-%m-%d %H:%M:%S'), reminder_data["id"]))
                else:
                    # Delete one-time reminder
                    cursor.execute("""
                        DELETE FROM reminders
                        WHERE id = ?
                    """, (reminder_data["id"],))

                conn.commit()

            except sqlite3.Error as e:
                logger.error(f"Database error in handle_reminder_trigger: {e}")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    conn.close()

        except Exception as e:
            logger.error(f"Error handling reminder trigger: {e}")

    def get_settings(self, guild_id: int) -> Dict:
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
                default_settings = {
                    'max_reminders': 10,
                    'max_duration': 2592000,  # 30 days
                    'default_channel': None,
                    'manager_role': None
                }
                
                cursor.execute("""
                    INSERT INTO reminder_settings (guild_id)
                    VALUES (?)
                """, (str(guild_id),))
                conn.commit()
                return default_settings
                
            return dict(data)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to get reminder settings: {e}")
            raise
        finally:
            if conn:
                conn.close()

    @tasks.loop(seconds=30)
    async def check_reminders(self):
        """Check for due reminders"""
        current_time = datetime.utcnow()
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM reminders
                WHERE trigger_time <= ?
            """, (current_time.strftime('%Y-%m-%d %H:%M:%S'),))
            due_reminders = cursor.fetchall()
            
            for reminder in due_reminders:
                await self.handle_reminder_trigger(dict(reminder))
                
        except sqlite3.Error as e:
            logger.error(f"Failed to check reminders: {e}")
        finally:
            if conn:
                conn.close()

    @check_reminders.before_loop
    async def before_check_reminders(self):
        """Wait until bot is ready"""
        await self.bot.wait_until_ready()

    def parse_time(self, time_str: str) -> int:
        """Parse time string into seconds"""
        total_seconds = 0
        time_units = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400,
            'w': 604800
        }
        
        pattern = r'(\d+)([smhdw])'
        matches = re.findall(pattern, time_str.lower())
        
        for value, unit in matches:
            total_seconds += int(value) * time_units[unit]
            
        if not total_seconds:
            raise ValueError("Invalid time format")
            
        return total_seconds

    @commands.group(name="reminder", aliases=["rm"])
    async def reminder(self, ctx):
        """⏰ Reminder commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @reminder.command(name="add", aliases=["create", "set"])
    async def add_reminder(self, ctx, time: str, *, message: str):
        """Add a reminder"""
        settings = self.get_settings(ctx.guild.id)
        
        try:
            duration = self.parse_time(time)
            if duration > settings['max_duration']:
                return await ctx.send(f"❌ Duration cannot exceed {settings['max_duration']} seconds!")
        except ValueError:
            return await ctx.send("❌ Invalid time format! Use: 1d, 12h, 30m, etc.")
            
        trigger_time = datetime.utcnow() + timedelta(seconds=duration)
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Check reminder limit
            cursor.execute("""
                SELECT COUNT(*) as count FROM reminders
                WHERE guild_id = ? AND user_id = ?
            """, (str(ctx.guild.id), str(ctx.author.id)))
            count = cursor.fetchone()['count']
            
            if count >= settings['max_reminders']:
                return await ctx.send(f"❌ You can only have {settings['max_reminders']} active reminders!")
            
            # Add reminder
            cursor.execute("""
                INSERT INTO reminders
                (guild_id, channel_id, user_id, message, trigger_time)
                VALUES (?, ?, ?, ?, ?)
            """, (
                str(ctx.guild.id),
                str(ctx.channel.id),
                str(ctx.author.id),
                message,
                trigger_time.strftime('%Y-%m-%d %H:%M:%S')
            ))
            reminder_id = cursor.lastrowid
            conn.commit()
            
            await ctx.send(
                f"✅ I'll remind you about: **{message}**\n"
                f"⏰ When: <t:{int(trigger_time.timestamp())}:R>\n"
                f"🔔 ID: `{reminder_id}`"
            )
            
        except sqlite3.Error as e:
            logger.error(f"Failed to add reminder: {e}")
            await ctx.send("❌ An error occurred while creating the reminder")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    @reminder.command(name="remove", aliases=["delete", "del"])
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
            conn.commit()
            
            if cursor.rowcount > 0:
                await ctx.send("✅ Reminder removed!")
            else:
                await ctx.send("❌ Reminder not found or not yours!")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to remove reminder: {e}")
            await ctx.send("❌ An error occurred while removing the reminder")
        finally:
            if conn:
                conn.close()

    @reminder.command(name="list", aliases=["show"])
    async def list_reminders(self, ctx):
        """List your active reminders"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM reminders
                WHERE guild_id = ? AND user_id = ?
                ORDER BY trigger_time ASC
            """, (str(ctx.guild.id), str(ctx.author.id)))
            reminders = cursor.fetchall()
            
            if not reminders:
                return await ctx.send("❌ You have no active reminders!")
            
            embed = Embed.create(
                title="📝 Your Reminders",
                color=discord.Color.blue()
            )
            
            for reminder in reminders:
                trigger_time = datetime.strptime(reminder['trigger_time'], '%Y-%m-%d %H:%M:%S')
                embed.add_field(
                    name=f"ID: {reminder['id']}",
                    value=f"⏰ When: <t:{int(trigger_time.timestamp())}:R>\n"
                          f"📝 Message: {reminder['message']}",
                    inline=False
                )
                
            await ctx.send(embed=embed)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to list reminders: {e}")
            await ctx.send("❌ An error occurred while getting reminders")
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
            
            if cursor.rowcount > 0:
                await ctx.send(f"✅ Cleared {cursor.rowcount} reminders!")
            else:
                await ctx.send("❌ You had no active reminders!")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to clear reminders: {e}")
            await ctx.send("❌ An error occurred while clearing reminders")
        finally:
            if conn:
                conn.close()

    @commands.group(name="reminderset", aliases=["rmset"])
    @commands.has_permissions(administrator=True)
    async def reminderset(self, ctx):
        """⚙️ Reminder system settings"""
        if ctx.invoked_subcommand is None:
            settings = self.get_settings(ctx.guild.id)
            
            embed = Embed.create(
                title="⚙️ Reminder Settings",
                color=discord.Color.blue(),
                field_Max_Reminders=str(settings['max_reminders']),
                field_Max_Duration=f"{settings['max_duration']} seconds"
            )
            
            if settings['default_channel']:
                channel = ctx.guild.get_channel(int(settings['default_channel']))
                if channel:
                    embed.add_field(name="Default Channel", value=channel.mention)
                    
            if settings['manager_role']:
                role = ctx.guild.get_role(int(settings['manager_role']))
                if role:
                    embed.add_field(name="Manager Role", value=role.mention)
                    
            await ctx.send(embed=embed)

    @reminderset.command(name="maxreminders")
    async def set_max_reminders(self, ctx, limit: int):
        """Set maximum reminders per user"""
        if limit < 1:
            return await ctx.send("❌ Limit must be at least 1!")
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE reminder_settings
                SET max_reminders = ?
                WHERE guild_id = ?
            """, (limit, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ Maximum reminders per user set to {limit}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set max reminders: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @reminderset.command(name="maxduration")
    async def set_max_duration(self, ctx, *, duration: str):
        """Set maximum reminder duration"""
        try:
            max_seconds = self.parse_time(duration)
        except ValueError:
            return await ctx.send("❌ Invalid duration format!")
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE reminder_settings
                SET max_duration = ?
                WHERE guild_id = ?
            """, (max_seconds, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ Maximum reminder duration set to {duration}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set max duration: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @reminderset.command(name="defaultchannel")
    async def set_default_channel(self, ctx, channel: discord.TextChannel = None):
        """Set default reminder channel"""
        channel_id = str(channel.id) if channel else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE reminder_settings
                SET default_channel = ?
                WHERE guild_id = ?
            """, (channel_id, str(ctx.guild.id)))
            conn.commit()
            
            if channel:
                await ctx.send(f"✅ Default reminder channel set to {channel.mention}")
            else:
                await ctx.send("✅ Default reminder channel cleared")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set default channel: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @reminderset.command(name="managerrole")
    async def set_manager_role(self, ctx, role: discord.Role = None):
        """Set reminder manager role"""
        role_id = str(role.id) if role else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE reminder_settings
                SET manager_role = ?
                WHERE guild_id = ?
            """, (role_id, str(ctx.guild.id)))
            conn.commit()
            
            if role:
                await ctx.send(f"✅ Reminder manager role set to {role.mention}")
            else:
                await ctx.send("✅ Reminder manager role cleared")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set manager role: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Initialize settings when bot joins a guild"""
        try:
            self.get_settings(guild.id)  # This will create default settings
        except Exception as e:
            logger.error(f"Failed to initialize reminder settings for guild {guild.id}: {e}")

async def setup(bot):
    """Setup the Reminders cog"""
    cog = Reminders(bot)
    cog.setup_tables()
    await bot.add_cog(cog)