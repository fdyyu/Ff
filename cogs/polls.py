import discord
from discord.ext import commands
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import asyncio
from .utils import Embed, get_connection, logger
import sqlite3
from pathlib import Path

class Polls(commands.Cog):
    """📊 Sistem Polling Advanced"""
    
    def __init__(self, bot):
        self.bot = bot
        self.active_polls = {}
        self.emoji_numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", 
                            "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
    def setup_tables(self):
        """Setup necessary database tables"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Polls table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS polls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    author_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    options TEXT NOT NULL,
                    end_time DATETIME,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Poll votes table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS poll_votes (
                    poll_id INTEGER,
                    user_id TEXT NOT NULL,
                    option_index INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (poll_id) REFERENCES polls (id) ON DELETE CASCADE,
                    UNIQUE (poll_id, user_id)
                )
            """)

            # Create triggers
            triggers = [
                ("""
                CREATE TRIGGER IF NOT EXISTS update_polls_timestamp 
                AFTER UPDATE ON polls
                BEGIN
                    UPDATE polls SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = NEW.id;
                END;
                """),
                ("""
                CREATE TRIGGER IF NOT EXISTS update_poll_votes_timestamp 
                AFTER UPDATE ON poll_votes
                BEGIN
                    UPDATE poll_votes SET updated_at = CURRENT_TIMESTAMP
                    WHERE poll_id = NEW.poll_id AND user_id = NEW.user_id;
                END;
                """)
            ]

            for trigger in triggers:
                cursor.execute(trigger)

            # Create indexes
            indexes = [
                ("idx_polls_guild", "polls(guild_id)"),
                ("idx_polls_channel", "polls(channel_id)"),
                ("idx_polls_message", "polls(message_id)"),
                ("idx_polls_author", "polls(author_id)"),
                ("idx_polls_active", "polls(is_active)"),
                ("idx_poll_votes_poll", "poll_votes(poll_id)"),
                ("idx_poll_votes_user", "poll_votes(user_id)")
            ]

            for idx_name, idx_cols in indexes:
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_cols}")

            conn.commit()
            logger.info("Polls tables setup completed successfully")
            
        except sqlite3.Error as e:
            logger.error(f"Error creating polls tables: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    @commands.group(name="poll")
    async def poll(self, ctx):
        """📊 Poll command group"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @poll.command(name="create")
    async def create_poll(self, ctx, title: str, duration: Optional[str], *options: str):
        """Create a new poll
        Example: !poll create "Favorite Color?" 1h Red Blue Green
        Duration format: 30s, 5m, 2h, 1d"""
        
        if len(options) < 2:
            return await ctx.send("❌ Please provide at least 2 options!")
            
        if len(options) > 10:
            return await ctx.send("❌ Maximum 10 options allowed!")

        # Parse duration
        end_time = None
        if duration:
            try:
                duration_value = int(duration[:-1])
                duration_unit = duration[-1].lower()
                
                if duration_unit == 's':
                    end_time = datetime.utcnow() + timedelta(seconds=duration_value)
                elif duration_unit == 'm':
                    end_time = datetime.utcnow() + timedelta(minutes=duration_value)
                elif duration_unit == 'h':
                    end_time = datetime.utcnow() + timedelta(hours=duration_value)
                elif duration_unit == 'd':
                    end_time = datetime.utcnow() + timedelta(days=duration_value)
                else:
                    return await ctx.send("❌ Invalid duration format! Use s/m/h/d")
            except ValueError:
                return await ctx.send("❌ Invalid duration format!")

        # Create embed
        embed = Embed.create(
            title=f"📊 {title}",
            description="React with the corresponding number to vote!",
            color=discord.Color.blue()
        )
        
        # Add options
        for idx, option in enumerate(options):
            embed.add_field(
                name=f"Option {idx + 1}",
                value=f"{self.emoji_numbers[idx]} {option}",
                inline=False
            )
            
        if end_time:
            embed.add_field(
                name="⏰ End Time",
                value=f"<t:{int(end_time.timestamp())}:R>",
                inline=False
            )
            
        # Send poll
        poll_msg = await ctx.send(embed=embed)
        
        # Add reactions
        for idx in range(len(options)):
            await poll_msg.add_reaction(self.emoji_numbers[idx])

        try:
            conn = get_connection()
            cursor = conn.cursor()

            # Save to database
            cursor.execute("""
                INSERT INTO polls (
                    guild_id, channel_id, message_id, author_id,
                    title, options, end_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                str(ctx.guild.id),
                str(ctx.channel.id),
                str(poll_msg.id),
                str(ctx.author.id),
                title,
                ','.join(options),
                end_time.strftime('%Y-%m-%d %H:%M:%S') if end_time else None
            ))
            
            poll_id = cursor.lastrowid

            # Log poll creation
            cursor.execute("""
                INSERT INTO admin_logs (admin_id, action, target, details)
                VALUES (?, ?, ?, ?)
            """, (
                str(ctx.author.id),
                'poll_create',
                str(poll_msg.id),
                f"Created poll: {title}"
            ))

            conn.commit()

            # Setup end timer if duration specified
            if end_time:
                self.active_polls[poll_msg.id] = poll_id
                await self.schedule_poll_end(poll_msg, end_time)
        
        except sqlite3.Error as e:
            logger.error(f"Error creating poll in database: {e}")
            await ctx.send("❌ Failed to create poll!")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    @poll.command(name="end")
    async def end_poll(self, ctx, message_id: int):
        """End a poll early"""
        try:
            message = await ctx.channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send("❌ Poll not found!")

        if message_id not in self.active_polls:
            return await ctx.send("❌ This poll is not active!")

        await self.end_poll_message(message)
        await ctx.send("✅ Poll ended successfully!")

    @poll.command(name="list")
    async def list_polls(self, ctx):
        """List all active polls in the server"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, title, message_id, end_time
                FROM polls
                WHERE guild_id = ? AND is_active = TRUE
                ORDER BY created_at DESC
            """, (str(ctx.guild.id),))
            
            polls = cursor.fetchall()

            if not polls:
                return await ctx.send("❌ No active polls found!")

            embed = Embed.create(
                title="📊 Active Polls",
                description="List of all active polls in this server"
            )

            for poll in polls:
                end_time = poll['end_time']
                value = f"ID: {poll['message_id']}\n"
                if end_time:
                    value += f"Ends: <t:{int(datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S').timestamp())}:R>"
                else:
                    value += "No end time set"
                
                embed.add_field(
                    name=poll['title'],
                    value=value,
                    inline=False
                )

            await ctx.send(embed=embed)
        
        except sqlite3.Error as e:
            logger.error(f"Error listing polls: {e}")
            await ctx.send("❌ Failed to list polls!")
        finally:
            if conn:
                conn.close()

    async def schedule_poll_end(self, message: discord.Message, end_time: datetime):
        """Schedule a poll to end at the specified time"""
        delay = (end_time - datetime.utcnow()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
            await self.end_poll_message(message)

    async def end_poll_message(self, message: discord.Message):
        """End a poll and display results"""
        if message.id not in self.active_polls:
            return

        poll_id = self.active_polls[message.id]
        del self.active_polls[message.id]

        try:
            conn = get_connection()
            cursor = conn.cursor()

            # Get poll data
            cursor.execute("""
                SELECT title, options FROM polls WHERE id = ?
            """, (poll_id,))
            poll_data = cursor.fetchone()

            if not poll_data:
                return

            # Get votes
            cursor.execute("""
                SELECT option_index, COUNT(*) as count
                FROM poll_votes
                WHERE poll_id = ?
                GROUP BY option_index
            """, (poll_id,))
            vote_counts = cursor.fetchall()

            # Mark poll as inactive
            cursor.execute("""
                UPDATE polls SET is_active = FALSE
                WHERE id = ?
            """, (poll_id,))

            # Log poll end
            cursor.execute("""
                INSERT INTO admin_logs (admin_id, action, target, details)
                VALUES (?, ?, ?, ?)
            """, (
                str(self.bot.user.id),
                'poll_end',
                str(message.id),
                f"Ended poll: {poll_data['title']}"
            ))

            conn.commit()

            options = poll_data['options'].split(',')
            vote_data = {row['option_index']: row['count'] for row in vote_counts}
            total_votes = sum(vote_data.values())

            # Create results embed
            embed = Embed.create(
                title=f"📊 Poll Results: {poll_data['title']}",
                description=f"Total Votes: {total_votes}",
                color=discord.Color.gold()
            )

            for idx, option in enumerate(options):
                votes = vote_data.get(idx, 0)
                percentage = (votes / total_votes * 100) if total_votes > 0 else 0
                bar_length = int(percentage / 10)
                bar = "█" * bar_length + "░" * (10 - bar_length)
                
                embed.add_field(
                    name=f"{self.emoji_numbers[idx]} {option}",
                    value=f"{bar} {votes} votes ({percentage:.1f}%)",
                    inline=False
                )

            await message.edit(embed=embed)
            await message.clear_reactions()
        
        except sqlite3.Error as e:
            logger.error(f"Error ending poll: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()
            
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle poll votes"""
        if payload.user_id == self.bot.user.id:
            return

        if payload.message_id not in self.active_polls:
            return

        try:
            emoji_idx = self.emoji_numbers.index(str(payload.emoji))
        except ValueError:
            return

        poll_id = self.active_polls[payload.message_id]

        try:
            conn = get_connection()
            cursor = conn.cursor()

            # Record vote
            cursor.execute("""
                INSERT OR REPLACE INTO poll_votes (poll_id, user_id, option_index)
                VALUES (?, ?, ?)
            """, (poll_id, str(payload.user_id), emoji_idx))

            conn.commit()

            # Remove other reactions from this user
            channel = self.bot.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            member = await channel.guild.fetch_member(payload.user_id)
            
            for reaction in message.reactions:
                if str(reaction.emoji) != str(payload.emoji):
                    await reaction.remove(member)
        
        except sqlite3.Error as e:
            logger.error(f"Error recording vote: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

async def setup(bot):
    """Setup the Polls cog"""
    cog = Polls(bot)
    cog.setup_tables()
    await bot.add_cog(cog)