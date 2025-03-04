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

        # Create users table first (parent table)
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

        # Create user_discord mapping table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_growid (
                discord_id TEXT PRIMARY KEY,
                growid TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (growid) REFERENCES users(growid) ON DELETE CASCADE
            )
        """)

        # Create products table
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

        # Create stock table
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

        # Create transactions table
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

        # Create world_info table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS world_info (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                world TEXT NOT NULL,
                owner TEXT NOT NULL,
                bot TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create bot_settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create blacklist table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                growid TEXT PRIMARY KEY,
                added_by TEXT NOT NULL,
                reason TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (growid) REFERENCES users(growid) ON DELETE CASCADE
            )
        """)

        # Create admin_logs table (NEW)
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

        # Create role_permissions table (NEW)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS role_permissions (
                role_id TEXT PRIMARY KEY,
                permissions TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create user_activity table (NEW)
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

        # Create cache_table (NEW)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_table (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            # New trigger for role_permissions
            ("""
            CREATE TRIGGER IF NOT EXISTS update_role_permissions_timestamp 
            AFTER UPDATE ON role_permissions
            BEGIN
                UPDATE role_permissions SET updated_at = CURRENT_TIMESTAMP
                WHERE role_id = NEW.role_id;
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
            # New indexes
            ("idx_admin_logs_admin", "admin_logs(admin_id)"),
            ("idx_admin_logs_created", "admin_logs(created_at)"),
            ("idx_user_activity_discord", "user_activity(discord_id)"),
            ("idx_user_activity_type", "user_activity(activity_type)"),
            ("idx_role_permissions_role", "role_permissions(role_id)"),
            ("idx_cache_expires", "cache_table(expires_at)")
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
            'users', 'user_growid', 'products', 'stock', 
            'transactions', 'world_info', 'bot_settings', 'blacklist',
            'admin_logs', 'role_permissions', 'user_activity', 'cache_table'
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