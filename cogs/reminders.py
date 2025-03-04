import discord
from discord.ext import commands, tasks
import asyncio
from datetime import datetime, timedelta
import pytz
from typing import Optional, Dict, List
from .utils import Embed, db, event_dispatcher

class Reminders(commands.Cog):
    """⏰ Advanced Reminder System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.active_reminders = {}
        self.check_reminders.start()
        self.register_handlers()

    async def setup_tables(self):
        """Setup necessary database tables"""
        async with db.pool.cursor() as cursor:
            # Reminder settings
            await cursor.execute("""
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
            
            # Active reminders
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    end_time DATETIME NOT NULL,
                    repeat_interval TEXT,
                    last_triggered DATETIME,
                    mentions TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Reminder templates
            await cursor.execute("""
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
            
            await db.pool.commit()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('reminder_trigger', self.handle_reminder_trigger)
        event_dispatcher.register('reminder_create', self.handle_reminder_create)

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        self.check_reminders.cancel()

    async def get_settings(self, guild_id: int) -> Dict:
        """Get reminder settings for a guild"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM reminder_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = await cursor.fetchone()
            
            if not data:
                default_settings = {
                    'max_reminders': 25,
                    'max_duration': 31536000,  # 1 year in seconds
                    'reminder_channel': None,
                    'timezone': 'UTC',
                    'mention_roles': False,
                    'allow_everyone': False
                }
                
                await cursor.execute("""
                    INSERT INTO reminder_settings
                    (guild_id, max_reminders, max_duration)
                    VALUES (?, ?, ?)
                """, (str(guild_id), 25, 31536000))
                await db.pool.commit()
                return default_settings
                
            return dict(data)

    async def parse_time(self, time_str: str, guild_timezone: str) -> datetime:
        """Parse time string into datetime object"""
        now = datetime.now(pytz.timezone(guild_timezone))
        duration = 0
        
        # Parse duration format (e.g., 1h30m, 2d, 1w)
        time_units = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400,
            'w': 604800
        }
        
        current = ''
        for char in time_str:
            if char.isdigit():
                current += char
            elif char.lower() in time_units:
                if current:
                    duration += int(current) * time_units[char.lower()]
                    current = ''
                    
        if duration == 0:
            raise ValueError("Invalid time format")
            
        return now + timedelta(seconds=duration)

    @tasks.loop(seconds=30)
    async def check_reminders(self):
        """Check for due reminders"""
        current_time = datetime.utcnow()
        
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM reminders
                WHERE is_active = TRUE AND end_time <= ?
            """, (current_time.strftime('%Y-%m-%d %H:%M:%S'),))
            due_reminders = await cursor.fetchall()
            
        for reminder in due_reminders:
            await self.trigger_reminder(reminder)

    @check_reminders.before_loop
    async def before_check_reminders(self):
        """Wait until bot is ready"""
        await self.bot.wait_until_ready()

    async def trigger_reminder(self, reminder: Dict):
        """Trigger a reminder"""
        channel = self.bot.get_channel(int(reminder['channel_id']))
        if not channel:
            return
            
        guild = channel.guild
        settings = await self.get_settings(guild.id)
        
        # Format mentions
        mentions = []
        if reminder['mentions']:
            for mention in reminder['mentions'].split(','):
                if mention.startswith('u:'):
                    user = guild.get_member(int(mention[2:]))
                    if user:
                        mentions.append(user.mention)
                elif mention.startswith('r:') and settings['mention_roles']:
                    role = guild.get_role(int(mention[2:]))
                    if role:
                        mentions.append(role.mention)
                        
        mention_str = ' '.join(mentions) if mentions else ''
        
        embed = Embed.create(
            title="⏰ Reminder",
            description=reminder['message'],
            field_Set_by=f"<@{reminder['user_id']}>",
            field_Created=f"<t:{int(datetime.strptime(reminder['created_at'], '%Y-%m-%d %H:%M:%S').timestamp())}:R>",
            color=discord.Color.blue()
        )
        
        await channel.send(content=mention_str, embed=embed)
        
        # Handle repeating reminders
        if reminder['repeat_interval']:
            next_time = datetime.strptime(reminder['end_time'], '%Y-%m-%d %H:%M:%S')
            interval = reminder['repeat_interval']
            
            if interval.endswith('h'):
                next_time += timedelta(hours=int(interval[:-1]))
            elif interval.endswith('d'):
                next_time += timedelta(days=int(interval[:-1]))
            elif interval.endswith('w'):
                next_time += timedelta(weeks=int(interval[:-1]))
            
            async with db.pool.cursor() as cursor:
                await cursor.execute("""
                    UPDATE reminders
                    SET end_time = ?, last_triggered = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (next_time.strftime('%Y-%m-%d %H:%M:%S'), reminder['id']))
                await db.pool.commit()
        else:
            async with db.pool.cursor() as cursor:
                await cursor.execute("""
                    UPDATE reminders
                    SET is_active = FALSE, last_triggered = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (reminder['id'],))
                await db.pool.commit()

    @commands.group(name="reminder", aliases=["remind"])
    async def reminder(self, ctx):
        """⏰ Reminder commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @reminder.command(name="set", usage="<time> <message>")
    async def set_reminder(self, ctx, time: str, *, message: str):
        """Set a new reminder
        Time format: 30s, 5m, 2h, 1d, 1w"""
        settings = await self.get_settings(ctx.guild.id)
        
        # Check reminder limits
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT COUNT(*) as count FROM reminders
                WHERE guild_id = ? AND user_id = ? AND is_active = TRUE
            """, (str(ctx.guild.id), str(ctx.author.id)))
            data = await cursor.fetchone()
            
            if data['count'] >= settings['max_reminders']:
                return await ctx.send("❌ You've reached the maximum number of active reminders!")
        
        try:
            end_time = await self.parse_time(time, settings['timezone'])
            
            # Check duration limit
            duration = (end_time - datetime.now(pytz.timezone(settings['timezone']))).total_seconds()
            if duration > settings['max_duration']:
                return await ctx.send("❌ Reminder duration exceeds the maximum allowed!")
                
            async with db.pool.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO reminders
                    (guild_id, channel_id, user_id, message, end_time)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    str(ctx.guild.id),
                    str(ctx.channel.id),
                    str(ctx.author.id),
                    message,
                    end_time.strftime('%Y-%m-%d %H:%M:%S')
                ))
                reminder_id = cursor.lastrowid
                await db.pool.commit()
                
            await ctx.send(f"✅ Reminder set for <t:{int(end_time.timestamp())}:R>")
            
        except ValueError:
            await ctx.send("❌ Invalid time format! Use: 30s, 5m, 2h, 1d, 1w")

    @reminder.command(name="repeat")
    async def set_repeat_reminder(self, ctx, interval: str, time: str, *, message: str):
        """Set a repeating reminder
        Interval format: 12h, 1d, 1w
        Time format: 30s, 5m, 2h, 1d, 1w"""
        if not interval[-1] in ['h', 'd', 'w']:
            return await ctx.send("❌ Invalid interval format! Use: 12h, 1d, 1w")
            
        try:
            int(interval[:-1])
        except ValueError:
            return await ctx.send("❌ Invalid interval format! Use: 12h, 1d, 1w")
            
        settings = await self.get_settings(ctx.guild.id)
        
        try:
            end_time = await self.parse_time(time, settings['timezone'])
            
            async with db.pool.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO reminders
                    (guild_id, channel_id, user_id, message, end_time, repeat_interval)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    str(ctx.guild.id),
                    str(ctx.channel.id),
                    str(ctx.author.id),
                    message,
                    end_time.strftime('%Y-%m-%d %H:%M:%S'),
                    interval
                ))
                await db.pool.commit()
                
            await ctx.send(
                f"✅ Repeating reminder set for <t:{int(end_time.timestamp())}:R>\n"
                f"Repeats every {interval}"
            )
            
        except ValueError:
            await ctx.send("❌ Invalid time format! Use: 30s, 5m, 2h, 1d, 1w")

    @reminder.command(name="list")
    async def list_reminders(self, ctx):
        """List your active reminders"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM reminders
                WHERE guild_id = ? AND user_id = ? AND is_active = TRUE
                ORDER BY end_time ASC
            """, (str(ctx.guild.id), str(ctx.author.id)))
            reminders = await cursor.fetchall()
            
        if not reminders:
            return await ctx.send("❌ You have no active reminders!")
            
        embed = Embed.create(
            title="📋 Your Active Reminders",
            color=discord.Color.blue()
        )
        
        for i, reminder in enumerate(reminders[:10], 1):
            end_time = datetime.strptime(reminder['end_time'], '%Y-%m-%d %H:%M:%S')
            repeat_str = f"\nRepeats: {reminder['repeat_interval']}" if reminder['repeat_interval'] else ""
            
            embed.add_field(
                name=f"{i}. Due {discord.utils.format_dt(end_time)}",
                value=f"Message: {reminder['message']}{repeat_str}",
                inline=False
            )
            
        if len(reminders) > 10:
            embed.set_footer(text=f"And {len(reminders) - 10} more reminders...")
            
        await ctx.send(embed=embed)

    @reminder.command(name="cancel", aliases=["delete"])
    async def cancel_reminder(self, ctx, reminder_id: int):
        """Cancel a reminder by its ID"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM reminders
                WHERE id = ? AND guild_id = ? AND user_id = ?
            """, (reminder_id, str(ctx.guild.id), str(ctx.author.id)))
            reminder = await cursor.fetchone()
            
            if not reminder:
                return await ctx.send("❌ Reminder not found!")
                
            await cursor.execute("""
                UPDATE reminders
                SET is_active = FALSE
                WHERE id = ?
            """, (reminder_id,))
            await db.pool.commit()
            
        await ctx.send("✅ Reminder cancelled!")

    @reminder.command(name="clear")
    async def clear_reminders(self, ctx):
        """Clear all your active reminders"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE reminders
                SET is_active = FALSE
                WHERE guild_id = ? AND user_id = ? AND is_active = TRUE
            """, (str(ctx.guild.id), str(ctx.author.id)))
            await db.pool.commit()
            
        await ctx.send("✅ All your reminders have been cleared!")

    @commands.group(name="remindertemplate", aliases=["rtemplate"])
    @commands.has_permissions(manage_guild=True)
    async def reminder_template(self, ctx):
        """📋 Reminder template management"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @reminder_template.command(name="add")
    async def add_template(self, ctx, name: str, duration: str, *, message: str):
        """Add a reminder template"""
        try:
            async with db.pool.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO reminder_templates
                    (guild_id, name, message, duration, created_by)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    str(ctx.guild.id),
                    name,
                    message,
                    duration,
                    str(ctx.author.id)
                ))
                await db.pool.commit()
                
            await ctx.send(f"✅ Template `{name}` added successfully!")
            
        except Exception as e:
            await ctx.send("❌ A template with that name already exists!")

    @reminder_template.command(name="list")
    async def list_templates(self, ctx):
        """List all reminder templates"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM reminder_templates
                WHERE guild_id = ?
                ORDER BY name ASC
            """, (str(ctx.guild.id),))
            templates = await cursor.fetchall()
            
        if not templates:
            return await ctx.send("❌ No templates found!")
            
        embed = Embed.create(
            title="📋 Reminder Templates",
            color=discord.Color.blue()
        )
        
        for template in templates:
            creator = ctx.guild.get_member(int(template['created_by']))
            embed.add_field(
                name=template['name'],
                value=f"Duration: {template['duration']}\n"
                      f"Message: {template['message']}\n"
                      f"Created by: {creator.mention if creator else 'Unknown'}",
                inline=False
            )
            
        await ctx.send(embed=embed)

    @reminder_template.command(name="use")
    async def use_template(self, ctx, name: str, *, additional_message: str = ""):
        """Create a reminder using a template"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM reminder_templates
                WHERE guild_id = ? AND name = ?
            """, (str(ctx.guild.id), name))
            template = await cursor.fetchone()
            
        if not template:
            return await ctx.send("❌ Template not found!")
            
        message = template['message']
        if additional_message:
            message += f"\n{additional_message}"
            
        await self.set_reminder(ctx, template['duration'], message=message)

    @reminder_template.command(name="delete")
    async def delete_template(self, ctx, name: str):
        """Delete a reminder template"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                DELETE FROM reminder_templates
                WHERE guild_id = ? AND name = ?
            """, (str(ctx.guild.id), name))
            await db.pool.commit()
            
        await ctx.send(f"✅ Template `{name}` deleted!")

    @commands.group(name="reminderset")
    @commands.has_permissions(manage_guild=True)
    async def reminderset(self, ctx):
        """⚙️ Reminder system settings"""
        if ctx.invoked_subcommand is None:
            settings = await self.get_settings(ctx.guild.id)
            
            embed = Embed.create(
                title="⚙️ Reminder Settings",
                color=discord.Color.blue(),
                field_Max_Reminders=str(settings['max_reminders']),
                field_Max_Duration=f"{settings['max_duration'] // 86400} days",
                field_Timezone=settings['timezone'],
                field_Reminder_Channel=f"<#{settings['reminder_channel']}>" if settings['reminder_channel'] else "Default",
                field_Allow_Role_Mentions=str(settings['mention_roles']),
                field_Allow_Everyone=str(settings['allow_everyone'])
            )
            
            await ctx.send(embed=embed)

    @reminderset.command(name="maxreminders")
    async def set_max_reminders(self, ctx, limit: int):
        """Set maximum reminders per user"""
        if limit < 1:
            return await ctx.send("❌ Limit must be positive!")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE reminder_settings
                SET max_reminders = ?
                WHERE guild_id = ?
            """, (limit, str(ctx.guild.id)))
            await db.pool.commit()
            
        await ctx.send(f"✅ Maximum reminders per user set to {limit}")

    @reminderset.command(name="maxduration")
    async def set_max_duration(self, ctx, days: int):
        """Set maximum reminder duration in days"""
        if days < 1:
            return await ctx.send("❌ Duration must be positive!")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE reminder_settings
                SET max_duration = ?
                WHERE guild_id = ?
            """, (days * 86400, str(ctx.guild.id)))
            await db.pool.commit()
            
        await ctx.send(f"✅ Maximum reminder duration set to {days} days")

    @reminderset.command(name="timezone")
    async def set_timezone(self, ctx, timezone: str):
        """Set server timezone"""
        try:
            pytz.timezone(timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            return await ctx.send("❌ Invalid timezone! Use a valid timezone name (e.g., UTC, America/New_York)")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE reminder_settings
                SET timezone = ?
                WHERE guild_id = ?
            """, (timezone, str(ctx.guild.id)))
            await db.pool.commit()
            
        await ctx.send(f"✅ Server timezone set to {timezone}")

    @reminderset.command(name="channel")
    async def set_reminder_channel(self, ctx, channel: discord.TextChannel = None):
        """Set default reminder channel"""
        channel_id = str(channel.id) if channel else None
        
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE reminder_settings
                SET reminder_channel = ?
                WHERE guild_id = ?
            """, (channel_id, str(ctx.guild.id)))
            await db.pool.commit()
            
        if channel:
            await ctx.send(f"✅ Default reminder channel set to {channel.mention}")
        else:
            await ctx.send("✅ Default reminder channel removed")

    @reminderset.command(name="mentionroles")
    async def toggle_role_mentions(self, ctx):
        """Toggle role mentions in reminders"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE reminder_settings
                SET mention_roles = NOT mention_roles
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            await db.pool.commit()
            
            await cursor.execute("""
                SELECT mention_roles FROM reminder_settings
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            data = await cursor.fetchone()
            
        enabled = data['mention_roles']
        await ctx.send(f"✅ Role mentions {'enabled' if enabled else 'disabled'}")

async def setup(bot):
    """Setup the Reminders cog"""
    cog = Reminders(bot)
    await cog.setup_tables()
    await bot.add_cog(cog)