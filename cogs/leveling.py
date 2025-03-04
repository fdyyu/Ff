import discord
from discord.ext import commands
import asyncio
import random
from datetime import datetime
from typing import Optional, Dict
from .utils import Embed, db, event_dispatcher

class Leveling(commands.Cog):
    """⭐ Advanced Leveling System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.xp_cooldown = {}
        self.register_handlers()

    async def setup_tables(self):
        """Setup necessary database tables"""
        async with db.pool.cursor() as cursor:
            # Level settings
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS level_settings (
                    guild_id TEXT PRIMARY KEY,
                    min_xp INTEGER DEFAULT 15,
                    max_xp INTEGER DEFAULT 25,
                    cooldown INTEGER DEFAULT 60,
                    announcement_channel TEXT,
                    stack_roles BOOLEAN DEFAULT FALSE,
                    disabled_channels TEXT,
                    level_up_message TEXT DEFAULT 'Congratulations {user}! You reached level {level}!'
                )
            """)
            
            # User levels
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_levels (
                    user_id TEXT,
                    guild_id TEXT,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 0,
                    total_messages INTEGER DEFAULT 0,
                    last_message DATETIME,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            
            # Level roles
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS level_roles (
                    guild_id TEXT,
                    level INTEGER,
                    role_id TEXT,
                    PRIMARY KEY (guild_id, level)
                )
            """)
            
            await db.pool.commit()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('message', self.handle_message)
        event_dispatcher.register('level_up', self.handle_level_up)

    async def get_settings(self, guild_id: int) -> Dict:
        """Get leveling settings for a guild"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM level_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = await cursor.fetchone()
            
            if not data:
                default_settings = {
                    'min_xp': 15,
                    'max_xp': 25,
                    'cooldown': 60,
                    'announcement_channel': None,
                    'stack_roles': False,
                    'disabled_channels': '',
                    'level_up_message': 'Congratulations {user}! You reached level {level}!'
                }
                
                await cursor.execute("""
                    INSERT INTO level_settings 
                    (guild_id, min_xp, max_xp, cooldown, level_up_message)
                    VALUES (?, ?, ?, ?, ?)
                """, (str(guild_id), 15, 25, 60, default_settings['level_up_message']))
                await db.pool.commit()
                return default_settings
                
            return dict(data)

    async def calculate_level(self, xp: int) -> int:
        """Calculate level from XP"""
        level = 0
        while xp >= ((50 * (level ** 2)) + (50 * level)):
            level += 1
        return level

    async def get_xp_for_next_level(self, level: int) -> int:
        """Get XP needed for next level"""
        return (50 * (level ** 2)) + (50 * level)

    async def handle_message(self, message: discord.Message):
        """Handle XP gain from messages"""
        if message.author.bot or not message.guild:
            return
            
        settings = await self.get_settings(message.guild.id)
        
        # Check cooldown
        user_id = str(message.author.id)
        guild_id = str(message.guild.id)
        cooldown_key = f"{guild_id}-{user_id}"
        
        current_time = datetime.utcnow()
        if cooldown_key in self.xp_cooldown:
            time_difference = (current_time - self.xp_cooldown[cooldown_key]).total_seconds()
            if time_difference < settings['cooldown']:
                return

        # Check if channel is disabled
        if str(message.channel.id) in settings['disabled_channels'].split(','):
            return

        # Award XP
        xp_gained = random.randint(settings['min_xp'], settings['max_xp'])
        
        async with db.pool.cursor() as cursor:
            # Get current level data
            await cursor.execute("""
                SELECT xp, level FROM user_levels
                WHERE user_id = ? AND guild_id = ?
            """, (user_id, guild_id))
            data = await cursor.fetchone()
            
            if not data:
                # Create new user entry
                current_xp = xp_gained
                current_level = 0
                await cursor.execute("""
                    INSERT INTO user_levels (user_id, guild_id, xp, level, total_messages)
                    VALUES (?, ?, ?, ?, 1)
                """, (user_id, guild_id, current_xp, current_level))
            else:
                # Update existing user
                current_xp = data['xp'] + xp_gained
                current_level = data['level']
                await cursor.execute("""
                    UPDATE user_levels
                    SET xp = ?, total_messages = total_messages + 1,
                        last_message = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND guild_id = ?
                """, (current_xp, user_id, guild_id))

            await db.pool.commit()
            
        # Check for level up
        new_level = await self.calculate_level(current_xp)
        if new_level > current_level:
            await self.handle_level_up(message.author, message.guild, new_level)
            
            async with db.pool.cursor() as cursor:
                await cursor.execute("""
                    UPDATE user_levels
                    SET level = ?
                    WHERE user_id = ? AND guild_id = ?
                """, (new_level, user_id, guild_id))
                await db.pool.commit()
        
        # Update cooldown
        self.xp_cooldown[cooldown_key] = current_time

    async def handle_level_up(self, member: discord.Member, guild: discord.Guild, new_level: int):
        """Handle level up events"""
        settings = await self.get_settings(guild.id)
        
        # Send level up message
        if settings['announcement_channel']:
            channel = guild.get_channel(int(settings['announcement_channel']))
            if channel:
                message = settings['level_up_message'].format(
                    user=member.mention,
                    level=new_level
                )
                await channel.send(message)

        # Handle level roles
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT role_id FROM level_roles
                WHERE guild_id = ? AND level <= ?
                ORDER BY level DESC
            """, (str(guild.id), new_level))
            level_roles = await cursor.fetchall()

        if level_roles:
            if settings['stack_roles']:
                # Add all roles up to current level
                for role_data in level_roles:
                    role = guild.get_role(int(role_data['role_id']))
                    if role and role not in member.roles:
                        try:
                            await member.add_roles(role)
                        except discord.Forbidden:
                            pass
            else:
                # Only add highest level role
                highest_role = guild.get_role(int(level_roles[0]['role_id']))
                if highest_role:
                    try:
                        # Remove other level roles first
                        for role_data in level_roles[1:]:
                            role = guild.get_role(int(role_data['role_id']))
                            if role and role in member.roles:
                                await member.remove_roles(role)
                        # Add highest role
                        await member.add_roles(highest_role)
                    except discord.Forbidden:
                        pass

    @commands.group(name="level")
    async def level(self, ctx):
        """⭐ Level system commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @level.command(name="rank")
    async def check_rank(self, ctx, member: discord.Member = None):
        """Check your or someone else's rank"""
        member = member or ctx.author
        
        async with db.pool.cursor() as cursor:
            # Get user's rank
            await cursor.execute("""
                SELECT xp, level, total_messages
                FROM user_levels
                WHERE user_id = ? AND guild_id = ?
            """, (str(member.id), str(ctx.guild.id)))
            data = await cursor.fetchone()
            
            if not data:
                return await ctx.send("This user hasn't gained any XP yet!")
                
            # Get user's position
            await cursor.execute("""
                SELECT COUNT(*) as rank
                FROM user_levels
                WHERE guild_id = ? AND xp > ?
            """, (str(ctx.guild.id), data['xp']))
            rank_data = await cursor.fetchone()
            
            rank = rank_data['rank'] + 1
            
            next_level_xp = await self.get_xp_for_next_level(data['level'])
            progress = (data['xp'] / next_level_xp) * 100 if next_level_xp > 0 else 100

            embed = Embed.create(
                title=f"Rank - {member.display_name}",
                color=member.color,
                field_Rank=f"#{rank}",
                field_Level=str(data['level']),
                field_XP=f"{data['xp']:,} / {next_level_xp:,} ({progress:.1f}%)",
                field_Messages=f"{data['total_messages']:,}"
            )
            
            await ctx.send(embed=embed)

    @level.command(name="leaderboard", aliases=["lb"])
    async def leaderboard(self, ctx):
        """View the server's leaderboard"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT user_id, xp, level
                FROM user_levels
                WHERE guild_id = ?
                ORDER BY xp DESC
                LIMIT 10
            """, (str(ctx.guild.id),))
            leaders = await cursor.fetchall()
            
        if not leaders:
            return await ctx.send("No one has gained any XP yet!")
            
        embed = Embed.create(
            title=f"🏆 {ctx.guild.name}'s Leaderboard",
            color=discord.Color.gold()
        )
        
        for idx, user_data in enumerate(leaders, 1):
            member = ctx.guild.get_member(int(user_data['user_id']))
            if member:
                embed.add_field(
                    name=f"#{idx} {member.display_name}",
                    value=f"Level: {user_data['level']} | XP: {user_data['xp']:,}",
                    inline=False
                )
                
        await ctx.send(embed=embed)

    @commands.group(name="levelset")
    @commands.has_permissions(manage_guild=True)
    async def levelset(self, ctx):
        """⚙️ Level system settings"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @levelset.command(name="xp")
    async def set_xp_range(self, ctx, min_xp: int, max_xp: int):
        """Set XP gain range"""
        if min_xp < 1 or max_xp < min_xp:
            return await ctx.send("❌ Invalid XP range!")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO level_settings 
                (guild_id, min_xp, max_xp)
                VALUES (?, ?, ?)
            """, (str(ctx.guild.id), min_xp, max_xp))
            await db.pool.commit()
            
        await ctx.send(f"✅ XP range set to {min_xp}-{max_xp}")

    @levelset.command(name="cooldown")
    async def set_cooldown(self, ctx, seconds: int):
        """Set XP gain cooldown"""
        if seconds < 0:
            return await ctx.send("❌ Cooldown must be positive!")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO level_settings 
                (guild_id, cooldown)
                VALUES (?, ?)
            """, (str(ctx.guild.id), seconds))
            await db.pool.commit()
            
        await ctx.send(f"✅ XP cooldown set to {seconds} seconds")

    @levelset.command(name="channel")
    async def set_announcement_channel(self, ctx, channel: discord.TextChannel = None):
        """Set level up announcement channel"""
        channel_id = str(channel.id) if channel else None
        
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO level_settings 
                (guild_id, announcement_channel)
                VALUES (?, ?)
            """, (str(ctx.guild.id), channel_id))
            await db.pool.commit()
            
        if channel:
            await ctx.send(f"✅ Level up announcements will be sent to {channel.mention}")
        else:
            await ctx.send("✅ Level up announcements disabled")

    @levelset.command(name="addrole")
    async def add_level_role(self, ctx, level: int, role: discord.Role):
        """Add a level-up role reward"""
        if level < 0:
            return await ctx.send("❌ Level must be positive!")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO level_roles 
                (guild_id, level, role_id)
                VALUES (?, ?, ?)
            """, (str(ctx.guild.id), level, str(role.id)))
            await db.pool.commit()
            
        await ctx.send(f"✅ {role.mention} will be given at level {level}")

    @levelset.command(name="removerole")
    async def remove_level_role(self, ctx, level: int):
        """Remove a level-up role reward"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                DELETE FROM level_roles 
                WHERE guild_id = ? AND level = ?
            """, (str(ctx.guild.id), level))
            await db.pool.commit()
            
        await ctx.send(f"✅ Removed role reward for level {level}")

async def setup(bot):
    """Setup the Leveling cog"""
    cog = Leveling(bot)
    await cog.setup_tables()
    await bot.add_cog(cog)