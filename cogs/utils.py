import discord
from datetime import datetime
from typing import Optional, Union, Dict, Any
import logging
import aiosqlite
import sqlite3

class DatabaseManager:
    def __init__(self, db_path: str = "shop.db"):
        self.db_path = db_path
        self.pool = None

    async def initialize(self):
        """Initialize database connection pool"""
        try:
            self.pool = await aiosqlite.connect(self.db_path)
            await self.pool.execute("PRAGMA foreign_keys = ON")
            await self.pool.execute("PRAGMA journal_mode = WAL")
            await self.pool.execute("PRAGMA busy_timeout = 5000")
            self.pool.row_factory = aiosqlite.Row
        except Exception as e:
            logging.error(f"Failed to initialize database: {e}")
            raise

    async def close(self):
        """Close database connection pool"""
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