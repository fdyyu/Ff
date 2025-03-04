import logging
import asyncio
import time
from typing import Optional, Dict
from datetime import datetime

import discord 
from discord.ext import commands

from .constants import Balance, TransactionError
from database import get_connection

class BalanceManagerService:
    _instance = None

    def __new__(cls, bot):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self, bot):
        if not self.initialized:
            self.bot = bot
            self.logger = logging.getLogger("BalanceManagerService")
            self._cache = {}
            self._cache_timeout = 30
            self._locks = {}
            self.initialized = True

    async def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get_growid(self, discord_id: str) -> Optional[str]:
        cache_key = f"growid_{discord_id}"
        
        if cache_key in self._cache:
            cached_data = self._cache[cache_key]
            if time.time() - cached_data['timestamp'] < self._cache_timeout:
                return cached_data['value']
            else:
                del self._cache[cache_key]

        async with await self._get_lock(cache_key):
            try:
                conn = get_connection()
                cursor = conn.cursor()
                # Case-sensitive query
                cursor.execute(
                    "SELECT growid FROM user_growid WHERE discord_id = ? COLLATE binary",
                    (str(discord_id),)
                )
                result = cursor.fetchone()
                
                if result:
                    growid = result['growid']
                    self._cache[cache_key] = {
                        'value': growid,  # Store original case
                        'timestamp': time.time()
                    }
                    self.logger.info(f"Found GrowID for Discord ID {discord_id}: {growid}")
                    return growid
                return None

            except Exception as e:
                self.logger.error(f"Error getting GrowID: {e}")
                return None
            finally:
                if conn:
                    conn.close()

    async def register_user(self, discord_id: str, growid: str) -> bool:
        async with await self._get_lock(f"register_{discord_id}"):
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                # Check if GrowID already exists (case-sensitive)
                cursor.execute("""
                    SELECT growid FROM users 
                    WHERE growid = ? COLLATE binary
                """, (growid,))
                
                existing = cursor.fetchone()
                if existing and existing['growid'] != growid:
                    raise ValueError(f"GrowID already exists with different case: {existing['growid']}")
                
                # Begin transaction
                conn.execute("BEGIN TRANSACTION")
                
                # Create user if not exists
                cursor.execute(
                    "INSERT OR IGNORE INTO users (growid) VALUES (?)",
                    (growid,)  # Keep original case
                )
                
                # Link Discord ID to GrowID
                cursor.execute(
                    "INSERT OR REPLACE INTO user_growid (discord_id, growid) VALUES (?, ?)",
                    (str(discord_id), growid)  # Keep original case
                )
                
                conn.commit()
                self.logger.info(f"Registered Discord user {discord_id} with GrowID {growid}")
                
                # Update cache
                cache_key = f"growid_{discord_id}"
                self._cache[cache_key] = {
                    'value': growid,  # Keep original case
                    'timestamp': time.time()
                }
                
                return True

            except Exception as e:
                self.logger.error(f"Error registering user: {e}")
                if conn:
                    conn.rollback()
                return False
            finally:
                if conn:
                    conn.close()

    async def get_balance(self, growid: str) -> Optional[Balance]:
        cache_key = f"balance_{growid}"
        
        if cache_key in self._cache:
            cached_data = self._cache[cache_key]
            if time.time() - cached_data['timestamp'] < self._cache_timeout:
                return cached_data['value']
            else:
                del self._cache[cache_key]

        async with await self._get_lock(cache_key):
            try:
                conn = get_connection()
                cursor = conn.cursor()
                # Case-sensitive query
                cursor.execute(
                    """
                    SELECT balance_wl, balance_dl, balance_bgl 
                    FROM users 
                    WHERE growid = ? COLLATE binary
                    """,
                    (growid,)  # Keep original case
                )
                result = cursor.fetchone()
                
                if result:
                    balance = Balance(
                        result['balance_wl'],
                        result['balance_dl'],
                        result['balance_bgl']
                    )
                    self._cache[cache_key] = {
                        'value': balance,
                        'timestamp': time.time()
                    }
                    return balance
                return None

            except Exception as e:
                self.logger.error(f"Error getting balance: {e}")
                return None
            finally:
                if conn:
                    conn.close()

    async def update_balance(self, growid: str, wl: int = 0, dl: int = 0, bgl: int = 0,
                           details: str = "", transaction_type: str = "") -> Optional[Balance]:
        async with await self._get_lock(f"balance_{growid}"):
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                # Get current balance - case-sensitive
                cursor.execute(
                    """
                    SELECT balance_wl, balance_dl, balance_bgl 
                    FROM users 
                    WHERE growid = ? COLLATE binary
                    """,
                    (growid,)  # Keep original case
                )
                current = cursor.fetchone()
                
                if not current:
                    raise TransactionError(f"User {growid} not found")
                
                old_balance = Balance(
                    current['balance_wl'],
                    current['balance_dl'],
                    current['balance_bgl']
                )
                
                # Calculate new balance
                new_wl = max(0, current['balance_wl'] + wl)
                new_dl = max(0, current['balance_dl'] + dl)
                new_bgl = max(0, current['balance_bgl'] + bgl)
                
                # Update balance - case-sensitive
                cursor.execute(
                    """
                    UPDATE users 
                    SET balance_wl = ?, balance_dl = ?, balance_bgl = ? 
                    WHERE growid = ? COLLATE binary
                    """,
                    (new_wl, new_dl, new_bgl, growid)  # Keep original case
                )
                
                # Record transaction - case-sensitive
                new_balance = Balance(new_wl, new_dl, new_bgl)
                cursor.execute(
                    """
                    INSERT INTO transactions 
                    (growid, type, details, old_balance, new_balance) 
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        growid,  # Keep original case
                        transaction_type,
                        details,
                        old_balance.format(),
                        new_balance.format()
                    )
                )
                
                conn.commit()
                
                # Update cache
                cache_key = f"balance_{growid}"
                self._cache[cache_key] = {
                    'value': new_balance,
                    'timestamp': time.time()
                }
                
                self.logger.info(f"Updated balance for {growid}: {old_balance.format()} -> {new_balance.format()}")
                return new_balance

            except Exception as e:
                self.logger.error(f"Error updating balance: {e}")
                if conn:
                    conn.rollback()
                return None
            finally:
                if conn:
                    conn.close()

    async def cleanup(self):
        """Cleanup resources"""
        self._cache.clear()
        self._locks.clear()

class BalanceManagerCog(commands.Cog):
    """Cog for balance management commands and functionality"""
    
    def __init__(self, bot):
        self.bot = bot
        self.balance_service = BalanceManagerService(bot)
        self.logger = logging.getLogger("BalanceManagerCog")

    @commands.Cog.listener()
    async def on_ready(self):
        self.logger.info(f"BalanceManagerCog is ready at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    async def cog_load(self):
        """Called when the cog is loaded"""
        self.logger.info("BalanceManagerCog loading...")

    async def cog_unload(self):
        """Called when the cog is unloaded"""
        await self.balance_service.cleanup()
        self.logger.info("BalanceManagerCog unloaded")

async def setup(bot):
    """Setup the BalanceManager cog"""
    if not hasattr(bot, 'balance_manager_loaded'):
        await bot.add_cog(BalanceManagerCog(bot))
        bot.balance_manager_loaded = True
        logging.info(f'BalanceManager cog loaded successfully at {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC')