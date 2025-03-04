import discord
from discord.ext import commands
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from .utils import Embed, event_dispatcher
from database import get_connection
import logging

logger = logging.getLogger(__name__)

class Leveling(commands.Cog):
    """⭐ Advanced Leveling System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.xp_cooldowns = {}
        self.register_handlers()

    def setup_tables(self):
        """Setup necessary database tables"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Levels table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS levels (
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 0,
                    messages INTEGER DEFAULT 0,
                    last_message_time TIMESTAMP,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            
            # Level rewards table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS level_rewards (
                    guild_id TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    role_id TEXT NOT NULL,
                    PRIMARY KEY (guild_id, level)
                )
            """)
            
            # Level settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS level_settings (
                    guild_id TEXT PRIMARY KEY,
                    min_xp INTEGER DEFAULT 15,
                    max_xp INTEGER DEFAULT 25,
                    cooldown INTEGER DEFAULT 60,
                    announcement_channel TEXT,
                    level_up_message TEXT DEFAULT 'Congratulations {user}! You reached level {level}!',
                    stack_roles BOOLEAN DEFAULT FALSE,
                    ignore_bots BOOLEAN DEFAULT TRUE
                )
            """)
            
            conn.commit()
            logger.info("Leveling tables created successfully")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to setup leveling tables: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('level_up', self.handle_level_up)
        event_dispatcher.register('level_reward', self.handle_reward)

    def get_settings(self, guild_id: int) -> Dict:
        """Get leveling settings for a guild"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM level_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = cursor.fetchone()
            
            if not data:
                default_settings = {
                    'min_xp': 15,
                    'max_xp': 25,
                    'cooldown': 60,
                    'announcement_channel': None,
                    'level_up_message': 'Congratulations {user}! You reached level {level}!',
                    'stack_roles': False,
                    'ignore_bots': True
                }
                
                cursor.execute("""
                    INSERT INTO level_settings
                    (guild_id, min_xp, max_xp, cooldown)
                    VALUES (?, 15, 25, 60)
                """, (str(guild_id),))
                conn.commit()
                return default_settings
                
            return dict(data)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to get level settings: {e}")
            raise
        finally:
            if conn:
                conn.close()

    async def add_xp(self, member: discord.Member, amount: int):
        """Add XP to a member"""
        if member.bot:
            return
            
        settings = self.get_settings(member.guild.id)
        
        # Check cooldown
        cooldown_key = f"{member.guild.id}-{member.id}"
        if cooldown_key in self.xp_cooldowns:
            if datetime.utcnow() < self.xp_cooldowns[cooldown_key]:
                return
                
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Get current level data
            cursor.execute("""
                INSERT INTO levels (user_id, guild_id, xp, level, messages)
                VALUES (?, ?, ?, 0, 1)
                ON CONFLICT(user_id, guild_id) DO UPDATE SET
                xp = xp + ?,
                messages = messages + 1,
                last_message_time = CURRENT_TIMESTAMP
                RETURNING *
            """, (str(member.id), str(member.guild.id), amount, amount))
            
            data = cursor.fetchone()
            
            # Calculate if level up
            old_level = data['level']
            new_level = 0
            xp = data['xp']
            
            while xp >= 100 * (new_level + 1):
                new_level += 1
                
            if new_level > old_level:
                cursor.execute("""
                    UPDATE levels
                    SET level = ?
                    WHERE user_id = ? AND guild_id = ?
                """, (new_level, str(member.id), str(member.guild.id)))
                
                conn.commit()
                
                # Handle level up
                await self.handle_level_up(member, new_level)
                
            else:
                conn.commit()
                
            # Set cooldown
            self.xp_cooldowns[cooldown_key] = datetime.utcnow() + timedelta(seconds=settings['cooldown'])
            
        except sqlite3.Error as e:
            logger.error(f"Failed to add XP: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    async def handle_level_up(self, member: discord.Member, new_level: int):
        """Handle member level up"""
        settings = self.get_settings(member.guild.id)
        
        # Send level up message
        if settings['announcement_channel']:
            channel = member.guild.get_channel(int(settings['announcement_channel']))
            if channel:
                message = settings['level_up_message'].format(
                    user=member.mention,
                    level=new_level
                )
                await channel.send(message)
                
        # Check for rewards
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT role_id FROM level_rewards
                WHERE guild_id = ? AND level <= ?
                ORDER BY level DESC
            """, (str(member.guild.id), new_level))
            rewards = cursor.fetchall()
            
            if rewards:
                if settings['stack_roles']:
                    # Add all role rewards
                    for reward in rewards:
                        role = member.guild.get_role(int(reward['role_id']))
                        if role and role not in member.roles:
                            await member.add_roles(role)
                else:
                    # Only add highest role
                    highest_role = member.guild.get_role(int(rewards[0]['role_id']))
                    if highest_role:
                        await member.add_roles(highest_role)
                        
                        # Remove lower reward roles
                        for reward in rewards[1:]:
                            role = member.guild.get_role(int(reward['role_id']))
                            if role and role in member.roles:
                                await member.remove_roles(role)
                                
        except sqlite3.Error as e:
            logger.error(f"Failed to handle level rewards: {e}")
        finally:
            if conn:
                conn.close()

    @commands.group(name="level")
    async def level(self, ctx):
        """⭐ Level commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @level.command(name="rank")
    async def show_rank(self, ctx, member: discord.Member = None):
        """Show your or someone else's rank"""
        member = member or ctx.author
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM levels
                WHERE user_id = ? AND guild_id = ?
            """, (str(member.id), str(ctx.guild.id)))
            data = cursor.fetchone()
            
            if not data:
                return await ctx.send("❌ This user has no level data!")
                
            # Get rank
            cursor.execute("""
                SELECT COUNT(*) as rank
                FROM levels
                WHERE guild_id = ? AND (xp > ? OR (xp = ? AND user_id < ?))
            """, (str(ctx.guild.id), data['xp'], data['xp'], str(member.id)))
            rank_data = cursor.fetchone()
            
            rank = rank_data['rank'] + 1
            next_level_xp = 100 * (data['level'] + 1)
            progress = (data['xp'] - (100 * data['level'])) / (next_level_xp - (100 * data['level'])) * 100
            
            embed = Embed.create(
                title=f"Rank - {member.display_name}",
                color=member.color,
                field_Level=str(data['level']),
                field_XP=f"{data['xp']}/{next_level_xp}",
                field_Rank=f"#{rank}",
                field_Messages=str(data['messages']),
                field_Progress=f"{progress:.1f}%"
            )
            
            await ctx.send(embed=embed)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to show rank: {e}")
            await ctx.send("❌ An error occurred while getting rank data")
        finally:
            if conn:
                conn.close()

    @level.command(name="top")
    async def show_leaderboard(self, ctx, page: int = 1):
        """Show server leaderboard"""
        if page < 1:
            return await ctx.send("❌ Page must be positive!")
            
        per_page = 10
        offset = (page - 1) * per_page
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT user_id, xp, level, messages
                FROM levels
                WHERE guild_id = ?
                ORDER BY xp DESC
                LIMIT ? OFFSET ?
            """, (str(ctx.guild.id), per_page, offset))
            leaders = cursor.fetchall()
            
            if not leaders:
                return await ctx.send("❌ No level data found!")
                
            embed = Embed.create(
                title=f"🏆 {ctx.guild.name}'s Leaderboard",
                color=discord.Color.gold()
            )
            
            for i, data in enumerate(leaders, offset + 1):
                member = ctx.guild.get_member(int(data['user_id']))
                if member:
                    embed.add_field(
                        name=f"#{i} {member.display_name}",
                        value=f"Level: {data['level']} | XP: {data['xp']} | Messages: {data['messages']}",
                        inline=False
                    )
                    
            # Get total ranked members
            cursor.execute("""
                SELECT COUNT(*) as count FROM levels WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            total = cursor.fetchone()['count']
            
            max_pages = (total + per_page - 1) // per_page
            embed.set_footer(text=f"Page {page}/{max_pages}")
            
            await ctx.send(embed=embed)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to show leaderboard: {e}")
            await ctx.send("❌ An error occurred while getting leaderboard data")
        finally:
            if conn:
                conn.close()

    @commands.group(name="levelset")
    @commands.has_permissions(manage_guild=True)
    async def levelset(self, ctx):
        """⚙️ Level system settings"""
        if ctx.invoked_subcommand is None:
            settings = self.get_settings(ctx.guild.id)
            
            embed = Embed.create(
                title="⚙️ Level Settings",
                color=discord.Color.blue(),
                field_XP_Range=f"{settings['min_xp']}-{settings['max_xp']}",
                field_Cooldown=f"{settings['cooldown']} seconds",
                field_Stack_Roles=str(settings['stack_roles']),
                field_Ignore_Bots=str(settings['ignore_bots']),
                field_Announcement_Channel=f"<#{settings['announcement_channel']}>" if settings['announcement_channel'] else "None",
                field_Level_Up_Message=settings['level_up_message']
            )
            
            await ctx.send(embed=embed)

    @levelset.command(name="xprange")
    async def set_xp_range(self, ctx, min_xp: int, max_xp: int):
        """Set XP gain range"""
        if min_xp < 1 or max_xp < min_xp:
            return await ctx.send("❌ Invalid XP range!")
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE level_settings
                SET min_xp = ?, max_xp = ?
                WHERE guild_id = ?
            """, (min_xp, max_xp, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ XP range set to {min_xp}-{max_xp}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set XP range: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="cooldown")
    async def set_cooldown(self, ctx, seconds: int):
        """Set XP gain cooldown"""
        if seconds < 1:
            return await ctx.send("❌ Cooldown must be positive!")
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE level_settings
                SET cooldown = ?
                WHERE guild_id = ?
            """, (seconds, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ XP cooldown set to {seconds} seconds")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set cooldown: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="reward")
    async def add_reward(self, ctx, level: int, role: discord.Role):
        """Add a level reward role"""
        if level < 1:
            return await ctx.send("❌ Level must be positive!")
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO level_rewards
                (guild_id, level, role_id)
                VALUES (?, ?, ?)
            """, (str(ctx.guild.id), level, str(role.id)))
            conn.commit()
            
            await ctx.send(f"✅ {role.mention} will be given at level {level}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to add level reward: {e}")
            await ctx.send("❌ An error occurred while adding the reward")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="removereward")
    async def remove_reward(self, ctx, level: int):
        """Remove a level reward"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM level_rewards
                WHERE guild_id = ? AND level = ?
            """, (str(ctx.guild.id), level))
            conn.commit()
            
            await ctx.send(f"✅ Reward removed for level {level}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to remove level reward: {e}")
            await ctx.send("❌ An error occurred while removing the reward")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="stackroles")
    async def toggle_stack_roles(self, ctx):
        """Toggle stacking of reward roles"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE level_settings 
                SET stack_roles = NOT stack_roles
                WHERE guild_id = ?
                RETURNING stack_roles
            """, (str(ctx.guild.id),))
            
            data = cursor.fetchone()
            enabled = data['stack_roles']
            conn.commit()
            
            await ctx.send(f"✅ Role stacking {'enabled' if enabled else 'disabled'}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to toggle role stacking: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="channel")
    async def set_announcement_channel(self, ctx, channel: discord.TextChannel = None):
        """Set level up announcement channel"""
        channel_id = str(channel.id) if channel else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE level_settings
                SET announcement_channel = ?
                WHERE guild_id = ?
            """, (channel_id, str(ctx.guild.id)))
            conn.commit()
            
            if channel:
                await ctx.send(f"✅ Level up announcements will be sent to {channel.mention}")
            else:
                await ctx.send("✅ Level up announcements disabled")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to set announcement channel: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="message")
    async def set_level_up_message(self, ctx, *, message: str):
        """Set level up message"""
        if len(message) > 1000:
            return await ctx.send("❌ Message too long!")
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE level_settings
                SET level_up_message = ?
                WHERE guild_id = ?
            """, (message, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ Level up message set to: {message}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set level up message: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle XP gain from messages"""
        if message.author.bot or not message.guild:
            return
            
        settings = self.get_settings(message.guild.id)
        if settings['ignore_bots'] and message.author.bot:
            return
            
        xp = random.randint(settings['min_xp'], settings['max_xp'])
        await self.add_xp(message.author, xp)

async def setup(bot):
    """Setup the Leveling cog"""
    cog = Leveling(bot)
    cog.setup_tables()
    await bot.add_cog(cog)