import discord
from discord.ext import commands
import asyncio
import wavelink
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from .utils import Embed, event_dispatcher
from database import get_connection  # Import fungsi database yang sudah ada

class Music(commands.Cog):
    """🎵 Advanced Music System"""
    
    def __init__(self, bot):
        self.bot = bot
        bot.loop.create_task(self.connect_nodes())
        self.music_queues = {}
        self.now_playing = {}
        self.text_channels = {}
        self.register_handlers()

    async def connect_nodes(self):
        """Connect to Lavalink nodes"""
        await self.bot.wait_until_ready()
        
        await wavelink.NodePool.create_node(
            bot=self.bot,
            host='127.0.0.1',
            port=2333,
            password='youshallnotpass'
        )

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('track_start', self.handle_track_start)
        event_dispatcher.register('track_end', self.handle_track_end)
        event_dispatcher.register('track_error', self.handle_track_error)

    async def get_settings(self, guild_id: int) -> Dict:
        """Get music settings for a guild"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM music_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = cursor.fetchone()
            
            if not data:
                default_settings = {
                    'default_volume': 100,
                    'vote_skip_ratio': 0.5,
                    'max_queue_size': 500,
                    'max_song_duration': 7200,
                    'dj_role': None,
                    'music_channel': None,
                    'announce_songs': True,
                    'auto_play': False
                }
                
                cursor.execute("""
                    INSERT INTO music_settings
                    (guild_id, default_volume, vote_skip_ratio)
                    VALUES (?, ?, ?)
                """, (str(guild_id), 100, 0.5))
                conn.commit()
                return default_settings
                
            return dict(data)
        finally:
            if conn:
                conn.close()
                
    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('track_start', self.handle_track_start)
        event_dispatcher.register('track_end', self.handle_track_end)
        event_dispatcher.register('track_error', self.handle_track_error)

    async def get_settings(self, guild_id: int) -> Dict:
        """Get music settings for a guild"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM music_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = await cursor.fetchone()
            
            if not data:
                default_settings = {
                    'default_volume': 100,
                    'vote_skip_ratio': 0.5,
                    'max_queue_size': 500,
                    'max_song_duration': 7200,
                    'dj_role': None,
                    'music_channel': None,
                    'announce_songs': True,
                    'auto_play': False
                }
                
                await cursor.execute("""
                    INSERT INTO music_settings
                    (guild_id, default_volume, vote_skip_ratio)
                    VALUES (?, ?, ?)
                """, (str(guild_id), 100, 0.5))
                await db.pool.commit()
                return default_settings
                
            return dict(data)

    async def ensure_voice(self, ctx):
        """Ensure the bot and user are in a voice channel"""
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError("❌ You need to be in a voice channel!")
            
        if not ctx.voice_client:
            try:
                player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
                self.music_queues[ctx.guild.id] = []
                self.text_channels[ctx.guild.id] = ctx.channel
                return player
            except Exception as e:
                raise commands.CommandError(f"❌ Failed to join voice channel: {str(e)}")
        else:
            if ctx.author.voice.channel != ctx.voice_client.channel:
                raise commands.CommandError("❌ You need to be in my voice channel!")
            return ctx.voice_client

    async def handle_track_start(self, player, track):
        """Handle track start event"""
        guild_id = player.guild.id
        if guild_id not in self.text_channels:
            return
            
        settings = await self.get_settings(guild_id)
        if not settings['announce_songs']:
            return
            
        channel = self.text_channels[guild_id]
        embed = Embed.create(
            title="🎵 Now Playing",
            description=f"[{track.title}]({track.uri})",
            field_Duration=self.format_duration(track.duration),
            field_Requested_by=track.requester.mention if hasattr(track, 'requester') else 'Unknown',
            color=discord.Color.blue()
        )
        
        await channel.send(embed=embed)

    async def handle_track_end(self, player, track, reason):
        """Handle track end event"""
        guild_id = player.guild.id
        
        if guild_id in self.music_queues and self.music_queues[guild_id]:
            next_track = self.music_queues[guild_id].pop(0)
            await player.play(next_track)
        else:
            settings = await self.get_settings(guild_id)
            if settings['auto_play']:
                # TODO: Implement auto-play feature
                pass
            else:
                await player.disconnect()
                if guild_id in self.text_channels:
                    await self.text_channels[guild_id].send("✅ Queue finished!")

    async def handle_track_error(self, player, track, error):
        """Handle track error event"""
        guild_id = player.guild.id
        if guild_id in self.text_channels:
            await self.text_channels[guild_id].send(f"❌ Error playing track: {error}")

    def format_duration(self, duration: int) -> str:
        """Format duration in milliseconds to readable string"""
        minutes, seconds = divmod(duration // 1000, 60)
        hours, minutes = divmod(minutes, 60)
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx, *, query: str):
        """🎵 Play a song"""
        player = await self.ensure_voice(ctx)
        
        try:
            # Search for tracks using wavelink v3
            if not query.startswith('http'):
                search_query = f'ytsearch:{query}'
                tracks = await wavelink.NodePool.get_node().get_tracks(wavelink.YouTubeTrack, search_query)
            else:
                tracks = await wavelink.NodePool.get_node().get_tracks(wavelink.Track, query)

            if not tracks:
                return await ctx.send("❌ No songs found!")
                
            track = tracks[0]
            track.requester = ctx.author
            
            settings = await self.get_settings(ctx.guild.id)
            
            # Check duration
            if track.duration > settings['max_song_duration'] * 1000:
                return await ctx.send("❌ Song is too long!")
                
            # Add to queue or play
            if player.is_playing():
                if len(self.music_queues[ctx.guild.id]) >= settings['max_queue_size']:
                    return await ctx.send("❌ Queue is full!")
                    
                self.music_queues[ctx.guild.id].append(track)
                await ctx.send(f"✅ Added to queue: **{track.title}**")
            else:
                await player.play(track)
                await ctx.send(f"🎵 Now playing: **{track.title}**")

        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}")

    @commands.command(name="stop")
    async def stop(self, ctx):
        """⏹️ Stop playing and clear queue"""
        await self.ensure_voice(ctx)
        
        ctx.voice_client.stop()
        self.music_queues[ctx.guild.id].clear()
        await ctx.send("⏹️ Stopped playing and cleared queue")

    @commands.command(name="skip", aliases=["s"])
    async def skip(self, ctx):
        """⏭️ Skip current song"""
        await self.ensure_voice(ctx)
        
        if not ctx.voice_client.is_playing():
            return await ctx.send("❌ Nothing is playing!")
            
        settings = await self.get_settings(ctx.guild.id)
        
        # Check for DJ role
        if settings['dj_role']:
            dj_role = ctx.guild.get_role(int(settings['dj_role']))
            if dj_role and dj_role not in ctx.author.roles:
                # Implement vote skip
                # TODO: Add vote skip system
                return await ctx.send("❌ You need the DJ role to skip!")
        
        ctx.voice_client.stop()
        await ctx.send("⏭️ Skipped current song")

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx):
        """📜 Show current queue"""
        if not ctx.voice_client or not ctx.voice_client.is_playing():
            return await ctx.send("❌ Nothing is playing!")
            
        if ctx.guild.id not in self.music_queues:
            return await ctx.send("❌ Queue is empty!")
            
        current = ctx.voice_client.source
        queue = self.music_queues[ctx.guild.id]
        
        embed = Embed.create(
            title="🎵 Music Queue",
            description=f"Now Playing: **{current.title}**\n"
                       f"Duration: {self.format_duration(current.duration)}\n"
                       f"Requested by: {current.requester.mention}\n\n"
                       f"**Queue:**",
            color=discord.Color.blue()
        )
        
        for i, track in enumerate(queue[:10], 1):
            embed.add_field(
                name=f"{i}. {track.title}",
                value=f"Duration: {self.format_duration(track.duration)}\n"
                      f"Requested by: {track.requester.mention}",
                inline=False
            )
            
        if len(queue) > 10:
            embed.set_footer(text=f"And {len(queue) - 10} more songs...")
            
        await ctx.send(embed=embed)

    @commands.command(name="volume", aliases=["vol"])
    async def volume(self, ctx, volume: int = None):
        """🔊 Set or show volume"""
        await self.ensure_voice(ctx)
        
        if volume is None:
            return await ctx.send(f"🔊 Current volume: {ctx.voice_client.volume}%")
            
        if not 0 <= volume <= 200:
            return await ctx.send("❌ Volume must be between 0 and 200!")
            
        await ctx.voice_client.set_volume(volume)
        await ctx.send(f"🔊 Volume set to {volume}%")

    @commands.command(name="pause")
    async def pause(self, ctx):
        """⏸️ Pause current song"""
        await self.ensure_voice(ctx)
        
        if not ctx.voice_client.is_playing():
            return await ctx.send("❌ Nothing is playing!")
            
        if ctx.voice_client.is_paused():
            return await ctx.send("❌ Already paused!")
            
        await ctx.voice_client.pause()
        await ctx.send("⏸️ Paused")

    @commands.command(name="resume")
    async def resume(self, ctx):
        """▶️ Resume paused song"""
        await self.ensure_voice(ctx)
        
        if not ctx.voice_client.is_paused():
            return await ctx.send("❌ Not paused!")
            
        await ctx.voice_client.resume()
        await ctx.send("▶️ Resumed")

    @commands.command(name="seek")
    async def seek(self, ctx, position: str):
        """⏩ Seek to position in song (format: MM:SS)"""
        await self.ensure_voice(ctx)
        
        if not ctx.voice_client.is_playing():
            return await ctx.send("❌ Nothing is playing!")
            
        try:
            minutes, seconds = map(int, position.split(':'))
            position_ms = (minutes * 60 + seconds) * 1000
            
            if position_ms > ctx.voice_client.source.duration:
                return await ctx.send("❌ Position is beyond song duration!")
                
            await ctx.voice_client.seek(position_ms)
            await ctx.send(f"⏩ Seeked to {position}")
        except ValueError:
            await ctx.send("❌ Invalid position format! Use MM:SS")

    @commands.group(name="playlist")
    async def playlist(self, ctx):
        """📋 Playlist management"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @playlist.command(name="create")
    async def playlist_create(self, ctx, *, name: str):
        """Create a new playlist"""
        async with db.pool.cursor() as cursor:
            try:
                await cursor.execute("""
                    INSERT INTO playlists (guild_id, name, owner_id)
                    VALUES (?, ?, ?)
                """, (str(ctx.guild.id), name, str(ctx.author.id)))
                await db.pool.commit()
                await ctx.send(f"✅ Created playlist: **{name}**")
            except:
                await ctx.send("❌ A playlist with that name already exists!")

    @playlist.command(name="add")
    async def playlist_add(self, ctx, playlist_name: str, *, query: str):
        """Add a song to a playlist"""
        # Use new wavelink search
        if not query.startswith('http'):
            search_query = f'ytsearch:{query}'
            tracks = await wavelink.NodePool.get_node().get_tracks(wavelink.YouTubeTrack, search_query)
        else:
            tracks = await wavelink.NodePool.get_node().get_tracks(wavelink.Track, query)

        if not tracks:
            return await ctx.send("❌ No songs found!")
            
        track = tracks[0]
        
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT id FROM playlists
                WHERE guild_id = ? AND name = ?
            """, (str(ctx.guild.id), playlist_name))
            playlist = await cursor.fetchone()
            if not playlist:
                return await ctx.send("❌ Playlist not found!")
                
            await cursor.execute("""
                INSERT INTO playlist_songs
                (playlist_id, track_url, track_title, added_by)
                VALUES (?, ?, ?, ?)
            """, (playlist['id'], track.uri, track.title, str(ctx.author.id)))
            await db.pool.commit()
            
        await ctx.send(f"✅ Added **{track.title}** to playlist: **{playlist_name}**")

    @playlist.command(name="play")
    async def playlist_play(self, ctx, *, name: str):
        """Play a playlist"""
        player = await self.ensure_voice(ctx)
        
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT ps.track_url, ps.track_title
                FROM playlists p
                JOIN playlist_songs ps ON p.id = ps.playlist_id
                WHERE p.guild_id = ? AND p.name = ?
            """, (str(ctx.guild.id), name))
            songs = await cursor.fetchall()
            
        if not songs:
            return await ctx.send("❌ Playlist is empty!")
            
        for song in songs:
            try:
                # Use new wavelink search
                if not song['track_url'].startswith('http'):
                    search_query = f'ytsearch:{song["track_url"]}'
                    tracks = await wavelink.NodePool.get_node().get_tracks(wavelink.YouTubeTrack, search_query)
                else:
                    tracks = await wavelink.NodePool.get_node().get_tracks(wavelink.Track, song['track_url'])

                if tracks:
                    track = tracks[0]
                    track.requester = ctx.author
                    
                    if player.is_playing():
                        self.music_queues[ctx.guild.id].append(track)
                    else:
                        await player.play(track)
            except Exception as e:
                await ctx.send(f"❌ Error loading track {song['track_title']}: {str(e)}")
                    
        await ctx.send(f"✅ Added {len(songs)} songs from playlist **{name}** to queue")

    @playlist.command(name="list")
    async def playlist_list(self, ctx):
        """List all playlists"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT name, owner_id, 
                    (SELECT COUNT(*) FROM playlist_songs ps WHERE ps.playlist_id = p.id) as song_count
                FROM playlists p
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            playlists = await cursor.fetchall()
            
        if not playlists:
            return await ctx.send("❌ No playlists found!")
            
        embed = Embed.create(
            title="📋 Server Playlists",
            color=discord.Color.blue()
        )
        
        for playlist in playlists:
            owner = ctx.guild.get_member(int(playlist['owner_id']))
            embed.add_field(
                name=playlist['name'],
                value=f"Owner: {owner.mention if owner else 'Unknown'}\n"
                      f"Songs: {playlist['song_count']}",
                inline=False
            )
            
        await ctx.send(embed=embed)

    @playlist.command(name="view")
    async def playlist_view(self, ctx, *, name: str):
        """View songs in a playlist"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT ps.track_title, ps.added_by, ps.added_at
                FROM playlists p
                JOIN playlist_songs ps ON p.id = ps.playlist_id
                WHERE p.guild_id = ? AND p.name = ?
                ORDER BY ps.added_at ASC
            """, (str(ctx.guild.id), name))
            songs = await cursor.fetchall()
            
        if not songs:
            return await ctx.send("❌ Playlist is empty!")
            
        embed = Embed.create(
            title=f"📋 Playlist: {name}",
            color=discord.Color.blue()
        )
        
        for i, song in enumerate(songs[:15], 1):
            added_by = ctx.guild.get_member(int(song['added_by']))
            embed.add_field(
                name=f"{i}. {song['track_title']}",
                value=f"Added by: {added_by.mention if added_by else 'Unknown'}\n"
                      f"Added: <t:{int(datetime.strptime(song['added_at'], '%Y-%m-%d %H:%M:%S').timestamp())}:R>",
                inline=False
            )
            
        if len(songs) > 15:
            embed.set_footer(text=f"And {len(songs) - 15} more songs...")
            
        await ctx.send(embed=embed)

    @playlist.command(name="remove")
    async def playlist_remove(self, ctx, playlist_name: str, *, song_name: str):
        """Remove a song from a playlist"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT p.id, p.owner_id FROM playlists p
                WHERE p.guild_id = ? AND p.name = ?
            """, (str(ctx.guild.id), playlist_name))
            playlist = await cursor.fetchone()
            
        if not playlist:
            return await ctx.send("❌ Playlist not found!")
            
        if str(ctx.author.id) != playlist['owner_id']:
            return await ctx.send("❌ You don't own this playlist!")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                DELETE FROM playlist_songs
                WHERE playlist_id = ? AND track_title LIKE ?
            """, (playlist['id'], f"%{song_name}%"))
            await db.pool.commit()
            
        await ctx.send(f"✅ Removed matching songs from playlist: **{playlist_name}**")

    @playlist.command(name="delete")
    async def playlist_delete(self, ctx, *, name: str):
        """Delete a playlist"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                SELECT owner_id FROM playlists
                WHERE guild_id = ? AND name = ?
            """, (str(ctx.guild.id), name))
            playlist = await cursor.fetchone()
            
        if not playlist:
            return await ctx.send("❌ Playlist not found!")
            
        if str(ctx.author.id) != playlist['owner_id']:
            return await ctx.send("❌ You don't own this playlist!")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                DELETE FROM playlists
                WHERE guild_id = ? AND name = ?
            """, (str(ctx.guild.id), name))
            await db.pool.commit()
            
        await ctx.send(f"✅ Deleted playlist: **{name}**")

    @commands.group(name="musicset")
    @commands.has_permissions(manage_guild=True)
    async def musicset(self, ctx):
        """⚙️ Music system settings"""
        if ctx.invoked_subcommand is None:
            settings = await self.get_settings(ctx.guild.id)
            
            embed = Embed.create(
                title="⚙️ Music Settings",
                color=discord.Color.blue(),
                field_Default_Volume=f"{settings['default_volume']}%",
                field_Vote_Skip_Ratio=f"{settings['vote_skip_ratio'] * 100}%",
                field_Max_Queue_Size=str(settings['max_queue_size']),
                field_Max_Song_Duration=f"{settings['max_song_duration'] // 60} minutes",
                field_DJ_Role=f"<@&{settings['dj_role']}>" if settings['dj_role'] else "None",
                field_Music_Channel=f"<#{settings['music_channel']}>" if settings['music_channel'] else "Any",
                field_Announce_Songs=str(settings['announce_songs']),
                field_Auto_Play=str(settings['auto_play'])
            )
            
            await ctx.send(embed=embed)

    @musicset.command(name="volume")
    async def set_default_volume(self, ctx, volume: int):
        """Set default volume"""
        if not 0 <= volume <= 200:
            return await ctx.send("❌ Volume must be between 0 and 200!")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE music_settings
                SET default_volume = ?
                WHERE guild_id = ?
            """, (volume, str(ctx.guild.id)))
            await db.pool.commit()
            
        await ctx.send(f"✅ Default volume set to {volume}%")

    @musicset.command(name="maxduration")
    async def set_max_duration(self, ctx, minutes: int):
        """Set maximum song duration in minutes"""
        if minutes < 1:
            return await ctx.send("❌ Duration must be positive!")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE music_settings
                SET max_song_duration = ?
                WHERE guild_id = ?
            """, (minutes * 60, str(ctx.guild.id)))
            await db.pool.commit()
            
        await ctx.send(f"✅ Maximum song duration set to {minutes} minutes")

    @musicset.command(name="maxqueue")
    async def set_max_queue(self, ctx, size: int):
        """Set maximum queue size"""
        if size < 1:
            return await ctx.send("❌ Queue size must be positive!")
            
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE music_settings
                SET max_queue_size = ?
                WHERE guild_id = ?
            """, (size, str(ctx.guild.id)))
            await db.pool.commit()
            
        await ctx.send(f"✅ Maximum queue size set to {size}")

    @musicset.command(name="djrole")
    async def set_dj_role(self, ctx, role: discord.Role = None):
        """Set DJ role"""
        role_id = str(role.id) if role else None
        
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE music_settings
                SET dj_role = ?
                WHERE guild_id = ?
            """, (role_id, str(ctx.guild.id)))
            await db.pool.commit()
            
        if role:
            await ctx.send(f"✅ DJ role set to {role.mention}")
        else:
            await ctx.send("✅ DJ role removed")

    @musicset.command(name="channel")
    async def set_music_channel(self, ctx, channel: discord.TextChannel = None):
        """Set music commands channel"""
        channel_id = str(channel.id) if channel else None
        
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE music_settings
                SET music_channel = ?
                WHERE guild_id = ?
            """, (channel_id, str(ctx.guild.id)))
            await db.pool.commit()
            
        if channel:
            await ctx.send(f"✅ Music commands restricted to {channel.mention}")
        else:
            await ctx.send("✅ Music commands allowed in all channels")

    @musicset.command(name="announce")
    async def toggle_announcements(self, ctx):
        """Toggle song announcements"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE music_settings
                SET announce_songs = NOT announce_songs
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            await db.pool.commit()
            
            await cursor.execute("""
                SELECT announce_songs FROM music_settings
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            data = await cursor.fetchone()
            
        enabled = data['announce_songs']
        await ctx.send(f"✅ Song announcements {'enabled' if enabled else 'disabled'}")

    @musicset.command(name="autoplay")
    async def toggle_autoplay(self, ctx):
        """Toggle auto-play feature"""
        async with db.pool.cursor() as cursor:
            await cursor.execute("""
                UPDATE music_settings
                SET auto_play = NOT auto_play
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            await db.pool.commit()
            
            await cursor.execute("""
                SELECT auto_play FROM music_settings
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            data = await cursor.fetchone()
            
        enabled = data['auto_play']
        await ctx.send(f"✅ Auto-play {'enabled' if enabled else 'disabled'}")

async def setup(bot):
    """Setup the Music cog"""
    cog = Music(bot)
    await cog.setup_tables()
    await bot.add_cog(cog)