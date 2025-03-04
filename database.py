import sqlite3
import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

def get_connection(max_retries: int = 3, timeout: int = 5) -> sqlite3.Connection:
    """Get SQLite database connection with retry mechanism"""
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect('shop.db', timeout=timeout)
            conn.row_factory = sqlite3.Row
            
            # Enable foreign keys and set pragmas
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA busy_timeout = 5000")
            
            return conn
        except sqlite3.Error as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Database connection attempt {attempt + 1} failed, retrying... Error: {e}")
            time.sleep(0.1 * (attempt + 1))

def setup_database():
    """Initialize database tables"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Admin System Tables (Keep Existing)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                growid TEXT PRIMARY KEY,
                balance_wl INTEGER DEFAULT 0,
                balance_dl INTEGER DEFAULT 0,
                balance_bgl INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_growid (
                discord_id TEXT PRIMARY KEY,
                growid TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (growid) REFERENCES users(growid) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_code TEXT NOT NULL,
                content TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'available' CHECK (status IN ('available', 'sold', 'deleted')),
                added_by TEXT NOT NULL,
                buyer_id TEXT,
                seller_id TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_code) REFERENCES products(code) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                growid TEXT NOT NULL,
                type TEXT NOT NULL,
                details TEXT NOT NULL,
                old_balance TEXT,
                new_balance TEXT,
                items_count INTEGER DEFAULT 0,
                total_price INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (growid) REFERENCES users(growid) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS world_info (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                world TEXT NOT NULL,
                owner TEXT NOT NULL,
                bot TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                growid TEXT PRIMARY KEY,
                added_by TEXT NOT NULL,
                reason TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (growid) REFERENCES users(growid) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS role_permissions (
                role_id TEXT PRIMARY KEY,
                permissions TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (discord_id) REFERENCES user_growid(discord_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_table (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Leveling System Tables
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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS level_rewards (
                guild_id TEXT NOT NULL,
                level INTEGER NOT NULL,
                role_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, level)
            )
        """)

        # Reputation System Tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reputation (
                user_id TEXT NOT NULL,
                guild_id TEXT NOT NULL,
                points INTEGER DEFAULT 0,
                received_count INTEGER DEFAULT 0,
                given_count INTEGER DEFAULT 0,
                last_given TIMESTAMP,
                PRIMARY KEY (user_id, guild_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rep_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                giver_id TEXT NOT NULL,
                receiver_id TEXT NOT NULL,
                reason TEXT,
                points INTEGER NOT NULL,
                given_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Logging System Tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logging_settings (
                guild_id TEXT PRIMARY KEY,
                log_channel TEXT,
                enabled_events TEXT,
                webhook_url TEXT,
                ignored_channels TEXT,
                ignored_users TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                user_id TEXT NOT NULL,
                target_id TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Music System Tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS music_settings (
                guild_id TEXT PRIMARY KEY,
                default_volume INTEGER DEFAULT 100,
                vote_skip_ratio FLOAT DEFAULT 0.5,
                max_queue_size INTEGER DEFAULT 500,
                max_song_duration INTEGER DEFAULT 7200,
                dj_role TEXT,
                music_channel TEXT,
                announce_songs BOOLEAN DEFAULT TRUE,
                auto_play BOOLEAN DEFAULT FALSE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT,
                name TEXT,
                owner_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, name)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlist_songs (
                playlist_id INTEGER,
                track_url TEXT,
                track_title TEXT,
                added_by TEXT,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
            )
        """)

        # AutoMod System Tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS automod_settings (
                guild_id TEXT PRIMARY KEY,
                enabled BOOLEAN DEFAULT TRUE,
                spam_threshold INTEGER DEFAULT 5,
                spam_timeframe INTEGER DEFAULT 5,
                caps_threshold FLOAT DEFAULT 0.7,
                caps_min_length INTEGER DEFAULT 10,
                banned_words TEXT,
                banned_wildcards TEXT,
                warn_threshold INTEGER DEFAULT 3,
                mute_duration INTEGER DEFAULT 10,
                dj_role TEXT,
                disabled_channels TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                guild_id TEXT NOT NULL,
                warning_type TEXT NOT NULL,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create triggers
        triggers = [
            ("""
            CREATE TRIGGER IF NOT EXISTS update_users_timestamp 
            AFTER UPDATE ON users
            BEGIN
                UPDATE users SET updated_at = CURRENT_TIMESTAMP
                WHERE growid = NEW.growid;
            END;
            """),
            ("""
            CREATE TRIGGER IF NOT EXISTS update_products_timestamp 
            AFTER UPDATE ON products
            BEGIN
                UPDATE products SET updated_at = CURRENT_TIMESTAMP
                WHERE code = NEW.code;
            END;
            """),
            ("""
            CREATE TRIGGER IF NOT EXISTS update_stock_timestamp 
            AFTER UPDATE ON stock
            BEGIN
                UPDATE stock SET updated_at = CURRENT_TIMESTAMP
                WHERE id = NEW.id;
            END;
            """),
            ("""
            CREATE TRIGGER IF NOT EXISTS update_bot_settings_timestamp 
            AFTER UPDATE ON bot_settings
            BEGIN
                UPDATE bot_settings SET updated_at = CURRENT_TIMESTAMP
                WHERE key = NEW.key;
            END;
            """),
            ("""
            CREATE TRIGGER IF NOT EXISTS update_role_permissions_timestamp 
            AFTER UPDATE ON role_permissions
            BEGIN
                UPDATE role_permissions SET updated_at = CURRENT_TIMESTAMP
                WHERE role_id = NEW.role_id;
            END;
            """),
            ("""
            CREATE TRIGGER IF NOT EXISTS update_playlists_timestamp 
            AFTER UPDATE ON playlists
            BEGIN
                UPDATE playlists SET updated_at = CURRENT_TIMESTAMP
                WHERE id = NEW.id;
            END;
            """),
            ("""
            CREATE TRIGGER IF NOT EXISTS update_automod_settings_timestamp 
            AFTER UPDATE ON automod_settings
            BEGIN
                UPDATE automod_settings SET updated_at = CURRENT_TIMESTAMP
                WHERE guild_id = NEW.guild_id;
            END;
            """)
        ]

        for trigger in triggers:
            cursor.execute(trigger)

        # Create indexes
        indexes = [
            ("idx_user_growid_discord", "user_growid(discord_id)"),
            ("idx_user_growid_growid", "user_growid(growid)"),
            ("idx_stock_product_code", "stock(product_code)"),
            ("idx_stock_status", "stock(status)"),
            ("idx_stock_content", "stock(content)"),
            ("idx_transactions_growid", "transactions(growid)"),
            ("idx_transactions_created", "transactions(created_at)"),
            ("idx_blacklist_growid", "blacklist(growid)"),
            ("idx_admin_logs_admin", "admin_logs(admin_id)"),
            ("idx_admin_logs_created", "admin_logs(created_at)"),
            ("idx_user_activity_discord", "user_activity(discord_id)"),
            ("idx_user_activity_type", "user_activity(activity_type)"),
            ("idx_role_permissions_role", "role_permissions(role_id)"),
            ("idx_cache_expires", "cache_table(expires_at)"),
            ("idx_levels_user", "levels(user_id)"),
            ("idx_levels_guild", "levels(guild_id)"),
            ("idx_reputation_user", "reputation(user_id)"),
            ("idx_reputation_guild", "reputation(guild_id)"),
            ("idx_rep_logs_guild", "rep_logs(guild_id)"),
            ("idx_audit_logs_guild", "audit_logs(guild_id)"),
            ("idx_audit_logs_user", "audit_logs(user_id)"),
            # Music system indexes
            ("idx_music_settings_guild", "music_settings(guild_id)"),
            ("idx_playlists_guild", "playlists(guild_id)"),
            ("idx_playlists_owner", "playlists(owner_id)"),
            ("idx_playlist_songs_playlist", "playlist_songs(playlist_id)"),
            # AutoMod system indexes
            ("idx_warnings_user", "warnings(user_id)"),
            ("idx_warnings_guild", "warnings(guild_id)"),
            ("idx_automod_settings_guild", "automod_settings(guild_id)")
        ]

        for idx_name, idx_cols in indexes:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_cols}")

        # Insert default world info if not exists
        cursor.execute("""
            INSERT OR IGNORE INTO world_info (id, world, owner, bot)
            VALUES (1, 'YOURWORLD', 'OWNER', 'BOT')
        """)

        # Insert default role permissions if not exists
        cursor.execute("""
            INSERT OR IGNORE INTO role_permissions (role_id, permissions)
            VALUES ('admin', 'all')
        """)

        conn.commit()
        logger.info("Database setup completed successfully")

    except sqlite3.Error as e:
        logger.error(f"Database setup error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def verify_database():
    """Verify database integrity and tables existence"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Check all tables exist
        tables = [
            # Admin tables
            'users', 'user_growid', 'products', 'stock', 
            'transactions', 'world_info', 'bot_settings', 'blacklist',
            'admin_logs', 'role_permissions', 'user_activity', 'cache_table',
            # Leveling tables
            'levels', 'level_rewards',
            # Reputation tables
            'reputation', 'rep_logs',
            # Logging tables
            'logging_settings', 'audit_logs',
            # Music system tables
            'music_settings', 'playlists', 'playlist_songs',
            # AutoMod system tables
            'automod_settings', 'warnings'
        ]

        missing_tables = []
        for table in tables:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cursor.fetchone():
                missing_tables.append(table)

        if missing_tables:
            logger.error(f"Missing tables: {', '.join(missing_tables)}")
            raise sqlite3.Error(f"Database verification failed: missing tables")

        # Check database integrity
        cursor.execute("PRAGMA integrity_check")
        if cursor.fetchone()['integrity_check'] != 'ok':
            raise sqlite3.Error("Database integrity check failed")

        # Clean expired cache entries
        cursor.execute("DELETE FROM cache_table WHERE expires_at < CURRENT_TIMESTAMP")
        conn.commit()

        logger.info("Database verification completed successfully")
        return True

    except sqlite3.Error as e:
        logger.error(f"Database verification error: {e}")
        return False
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('database.log')
        ]
    )
    
    try:
        setup_database()
        if not verify_database():
            logger.error("Database verification failed. Attempting to recreate database...")
            # Backup existing database if it exists
            import shutil
            from pathlib import Path
            if Path('shop.db').exists():
                backup_path = f"shop.db.backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
                shutil.copy2('shop.db', backup_path)
                logger.info(f"Created database backup: {backup_path}")
            
            # Recreate database
            Path('shop.db').unlink(missing_ok=True)
            setup_database()
            if verify_database():
                logger.info("Database successfully recreated")
            else:
                logger.error("Failed to recreate database")
        else:
            logger.info("Database initialization complete")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)