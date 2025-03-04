import discord
from discord.ext import commands
import datetime
from collections import Counter
import matplotlib.pyplot as plt
import pandas as pd
import io
from .utils import Embed, db, event_dispatcher

class ServerStats(commands.Cog):
    """📊 Sistem Statistik Server"""
    
    def __init__(self, bot):
        self.bot = bot
        self.message_history = {}
        self.voice_time = {}
        self.register_handlers()
        
    async def setup_tables(self):
        """Setup necessary database tables"""
        async with db.pool.cursor() as cursor:
            # Activity logs
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    activity_type TEXT NOT NULL,
                    details TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Member history
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS member_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    member_count INTEGER NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await db.pool.commit()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('message', self.log_message_activity)
        event_dispatcher.register('voice_state_update', self.log_voice_activity)
        event_dispatcher.register('member_join', self.log_member_join)
        event_dispatcher.register('member_leave', self.log_member_leave)

    async def log_activity(self, guild_id: int, user_id: int, activity_type: str, details: str = None):
        """Log any server activity"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO activity_logs (guild_id, user_id, activity_type, details)
                VALUES (?, ?, ?, ?)
            """, (str(guild_id), str(user_id), activity_type, details))
            await db.pool.commit()

    async def log_message_activity(self, message):
        """Log message activity"""
        if not message.guild or message.author.bot:
            return
            
        await self.log_activity(
            message.guild.id,
            message.author.id,
            'message',
            f'Channel: {message.channel.name}'
        )

    async def log_voice_activity(self, member, before, after):
        """Log voice activity"""
        if not member.guild:
            return
            
        if before.channel is None and after.channel is not None:
            await self.log_activity(
                member.guild.id,
                member.id,
                'voice_join',
                f'Channel: {after.channel.name}'
            )
        elif before.channel is not None and after.channel is None:
            await self.log_activity(
                member.guild.id,
                member.id,
                'voice_leave',
                f'Channel: {before.channel.name}'
            )

    @commands.command(name="serverstats")
    async def show_server_stats(self, ctx):
        """📊 Tampilkan statistik server"""
        guild = ctx.guild
        
        embed = Embed.create(
            title=f"📊 Statistik Server {guild.name}",
            color=discord.Color.blue(),
            field_Members={
                "value": f"Total: {guild.member_count}\n"
                        f"Humans: {len([m for m in guild.members if not m.bot])}\n"
                        f"Bots: {len([m for m in guild.members if m.bot])}",
                "inline": True
            },
            field_Channels={
                "value": f"Text: {len(guild.text_channels)}\n"
                        f"Voice: {len(guild.voice_channels)}\n"
                        f"Categories: {len(guild.categories)}",
                "inline": True
            },
            field_Roles={
                "value": f"Total Roles: {len(guild.roles)}\n"
                        f"Highest Role: {guild.roles[-1].name}",
                "inline": True
            },
            field_Server_Info={
                "value": f"Created: <t:{int(guild.created_at.timestamp())}:R>\n"
                        f"Owner: {guild.owner.mention}\n"
                        f"Region: {guild.preferred_locale}",
                "inline": False
            }
        )
        
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
            
        await ctx.send(embed=embed)

    @commands.command(name="rolestat")
    async def role_statistics(self, ctx):
        """📊 Tampilkan statistik role"""
        roles = [role for role in ctx.guild.roles if not role.is_default()]
        
        if not roles:
            return await ctx.send("❌ Tidak ada role untuk ditampilkan!")
            
        member_counts = [len(role.members) for role in roles]
        role_names = [role.name for role in roles]
        
        # Create plot
        plt.figure(figsize=(10, 6))
        plt.bar(role_names, member_counts)
        plt.xticks(rotation=45, ha='right')
        plt.title('Role Distribution')
        plt.tight_layout()
        
        # Save plot
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()
        
        # Send result
        file = discord.File(buf, 'role_stats.png')
        embed = Embed.create(
            title="📊 Role Statistics",
            description=f"Distribution of {len(roles)} roles in the server"
        )
        embed.set_image(url="attachment://role_stats.png")
        
        await ctx.send(embed=embed, file=file)

    @commands.command(name="activitystats")
    async def activity_statistics(self, ctx, days: int = 7):
        """📈 Tampilkan statistik aktivitas"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT activity_type, COUNT(*) as count, 
                       strftime('%Y-%m-%d', timestamp) as date
                FROM activity_logs
                WHERE guild_id = ?
                AND timestamp > datetime('now', ?)
                GROUP BY activity_type, date
                ORDER BY date
            """, (str(ctx.guild.id), f'-{days} days'))
            
            data = await cursor.fetchall()
            
        if not data:
            return await ctx.send("❌ Tidak ada data aktivitas!")
            
        # Convert to DataFrame
        df = pd.DataFrame(data, columns=['activity_type', 'count', 'date'])
        pivot = df.pivot(index='date', columns='activity_type', values='count')
        
        # Create plot
        plt.figure(figsize=(10, 6))
        pivot.plot(kind='line', marker='o')
        plt.title(f'Server Activity (Last {days} days)')
        plt.xlabel('Date')
        plt.ylabel('Activity Count')
        plt.legend(title='Activity Type')
        plt.grid(True)
        plt.tight_layout()
        
        # Save plot
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()
        
        # Send result
        file = discord.File(buf, 'activity_stats.png')
        embed = Embed.create(
            title="📈 Activity Statistics",
            description=f"Activity overview for the last {days} days"
        )
        embed.set_image(url="attachment://activity_stats.png")
        
        await ctx.send(embed=embed, file=file)

    @commands.command(name="memberhistory")
    async def member_history(self, ctx):
        """📈 Tampilkan history member"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT member_count, 
                       strftime('%Y-%m-%d', timestamp) as date
                FROM member_history
                WHERE guild_id = ?
                ORDER BY timestamp
            """, (str(ctx.guild.id),))
            
            data = await cursor.fetchall()
            
        if not data:
            return await ctx.send("❌ Tidak ada data history member!")
            
        # Create plot
        dates = [row[1] for row in data]
        counts = [row[0] for row in data]
        
        plt.figure(figsize=(10, 6))
        plt.plot(dates, counts, marker='o')
        plt.title('Member Growth History')
        plt.xlabel('Date')
        plt.ylabel('Member Count')
        plt.xticks(rotation=45)
        plt.grid(True)
        plt.tight_layout()
        
        # Save plot
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()
        
        # Send result
        file = discord.File(buf, 'member_history.png')
        embed = Embed.create(
            title="📈 Member History",
            description="Server member count over time"
        )
        embed.set_image(url="attachment://member_history.png")
        
        await ctx.send(embed=embed, file=file)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Track member joins"""
        await self.log_activity(member.guild.id, member.id, 'member_join')
        
        # Update member history
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO member_history (guild_id, member_count)
                VALUES (?, ?)
            """, (str(member.guild.id), len(member.guild.members)))
            await db.pool.commit()

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Track member leaves"""
        await self.log_activity(member.guild.id, member.id, 'member_leave')
        
        # Update member history
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO member_history (guild_id, member_count)
                VALUES (?, ?)
            """, (str(member.guild.id), len(member.guild.members)))
            await db.pool.commit()

async def setup(bot):
    """Setup the Stats cog"""
    cog = ServerStats(bot)
    await cog.setup_tables()
    await bot.add_cog(cog)