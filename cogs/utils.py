import discord
from datetime import datetime
from typing import Optional, Union, Dict, Any
import logging
import sys
from pathlib import Path

# Add parent directory to path to import database
sys.path.append(str(Path(__file__).parent.parent))
from database import get_connection

# Configure logger
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

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

def execute_query(query: str, params: tuple = (), fetch: bool = False):
    """
    Execute a database query with proper connection management
    
    Args:
        query (str): SQL query to execute
        params (tuple): Query parameters
        fetch (bool): Whether to fetch results
        
    Returns:
        Results if fetch is True, else None
        
    Raises:
        Exception: If query execution fails
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        
        if fetch:
            result = cursor.fetchall()
        else:
            conn.commit()
            result = None
            
        return result
    except Exception as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def transaction(func):
    """
    Decorator for handling database transactions
    
    Usage:
        @transaction
        def my_db_function(conn, ...):
            # Use the connection here
            pass
    """
    def wrapper(*args, **kwargs):
        conn = None
        try:
            conn = get_connection()
            result = func(conn, *args, **kwargs)
            conn.commit()
            return result
        except Exception as e:
            logger.error(f"Transaction error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()
    return wrapper

# Helper functions for common database operations
@transaction
def get_user(conn, user_id: int):
    """Get user data from database"""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (str(user_id),))
    return cursor.fetchone()

@transaction
def update_user(conn, user_id: int, **kwargs):
    """Update user data in database"""
    cursor = conn.cursor()
    set_values = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    query = f"UPDATE users SET {set_values} WHERE user_id = ?"
    params = tuple(kwargs.values()) + (str(user_id),)
    cursor.execute(query, params)

@transaction
def log_activity(conn, guild_id: int, user_id: int, activity_type: str, details: str = None):
    """Log activity to database"""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO activity_logs (guild_id, user_id, activity_type, details)
        VALUES (?, ?, ?, ?)
    """, (str(guild_id), str(user_id), activity_type, details))

# Export commonly used functions and classes
__all__ = [
    'Embed',
    'get_connection',
    'execute_query',
    'transaction',
    'get_user',
    'update_user',
    'log_activity',
    'logger'
]