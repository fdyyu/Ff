import discord
from datetime import datetime
from typing import Optional, Union, Dict, Any
import logging
import aiosqlite

class DatabaseManager:
    def __init__(self, db_path: str = "database.db"):
        self.db_path = db_path
        self.pool = None

    async def initialize(self):
        self.pool = await aiosqlite.connect(self.db_path)
        await self.create_tables()

    async def create_tables(self):
        async with self.pool.cursor() as cursor:
            # Activity logs table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    activity_type TEXT NOT NULL,
                    timestamp DATETIME NOT NULL
                )
            """)
            
            # Warning logs table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    moderator_id TEXT NOT NULL,
                    timestamp DATETIME NOT NULL
                )
            """)
            
            await self.pool.commit()

    async def close(self):
        if self.pool:
            await self.pool.close()

class Embed:
    """Centralized embed creation"""
    
    @staticmethod
    def create(
        title: str, 
        description: Optional[str] = None, 
        color: discord.Color = discord.Color.blue(),
        **kwargs
    ) -> discord.Embed:
        """Create a standardized embed"""
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )
        
        for key, value in kwargs.items():
            if key.startswith("field_"):
                field_name = key.replace("field_", "")
                if isinstance(value, dict):
                    embed.add_field(
                        name=field_name,
                        value=value["value"],
                        inline=value.get("inline", True)
                    )
                else:
                    embed.add_field(name=field_name, value=value)
                    
        return embed

class Permissions:
    """Permission checking utilities"""
    
    @staticmethod
    async def check_admin(ctx) -> bool:
        """Check if user has admin permissions"""
        if not ctx.guild:
            return False
        return ctx.author.guild_permissions.administrator

    @staticmethod
    async def check_mod(ctx) -> bool:
        """Check if user has moderator permissions"""
        if not ctx.guild:
            return False
        return (ctx.author.guild_permissions.manage_messages or 
                ctx.author.guild_permissions.kick_members)

class EventDispatcher:
    """Central event dispatcher"""
    
    def __init__(self):
        self.handlers = {}
        self.logger = logging.getLogger('EventDispatcher')

    def register(self, event: str, handler, priority: int = 0):
        """Register an event handler"""
        if event not in self.handlers:
            self.handlers[event] = []
        self.handlers[event].append((priority, handler))
        self.handlers[event].sort(key=lambda x: x[0], reverse=True)

    async def dispatch(self, event: str, *args, **kwargs):
        """Dispatch an event to all registered handlers"""
        if event not in self.handlers:
            return

        for priority, handler in self.handlers[event]:
            try:
                await handler(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"Error in {event} handler: {e}")

# Initialize global instances
db = DatabaseManager()
event_dispatcher = EventDispatcher()