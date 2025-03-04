import discord
from discord.ext import commands
from datetime import datetime, timedelta
import json
from .utils import Embed, Permissions, event_dispatcher, db

class AutoMod(commands.Cog):
    """🛡️ Sistem Moderasi Otomatis"""
    
    def __init__(self, bot):
        self.bot = bot
        self.spam_check = {}
        self.config = self.load_config()
        self.register_handlers()

    async def setup_tables(self):
        """Setup necessary database tables"""
        async with db.pool.cursor() as cursor:
            # Warnings table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    warning_type TEXT NOT NULL,
                    reason TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.pool.commit()

    def register_handlers(self):
        """Register event handlers with dispatcher"""
        event_dispatcher.register('message', self.handle_message, priority=1)
        event_dispatcher.register('automod_violation', self.handle_violation, priority=1)

    def load_config(self) -> dict:
        """Load automod configuration"""
        try:
            with open('config/automod.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            default = {
                "enabled": True,
                "spam": {
                    "enabled": True,
                    "threshold": 5,
                    "timeframe": 5  # seconds
                },
                "caps": {
                    "enabled": True,
                    "threshold": 0.7,
                    "min_length": 10
                },
                "banned_words": {
                    "enabled": True,
                    "words": [],
                    "wildcards": []
                },
                "punishments": {
                    "warn_threshold": 3,
                    "mute_duration": 10  # minutes
                }
            }
            self.save_config(default)
            return default

    def save_config(self, config: dict = None):
        """Save automod configuration"""
        if config is None:
            config = self.config
        with open('config/automod.json', 'w') as f:
            json.dump(config, f, indent=4)

    async def handle_message(self, message: discord.Message):
        """Main message handler for automod"""
        if not self.config["enabled"] or message.author.bot:
            return

        violations = []

        # Check for spam
        if self.config["spam"]["enabled"]:
            if await self.check_spam(message):
                violations.append(("spam", "Sending messages too quickly"))

        # Check for excessive caps
        if self.config["caps"]["enabled"]:
            if await self.check_caps(message):
                violations.append(("caps", "Excessive use of caps"))

        # Check for banned words
        if self.config["banned_words"]["enabled"]:
            if word := await self.check_banned_words(message):
                violations.append(("banned_word", f"Used banned word: {word}"))

        # Handle any violations
        for violation_type, reason in violations:
            await event_dispatcher.dispatch('automod_violation', message, violation_type, reason)

    async def check_spam(self, message: discord.Message) -> bool:
        """Check for spam messages"""
        author_id = str(message.author.id)
        current_time = datetime.utcnow()
        threshold = self.config["spam"]["threshold"]
        timeframe = self.config["spam"]["timeframe"]

        # Initialize or clean up old messages
        if author_id not in self.spam_check:
            self.spam_check[author_id] = []

        # Remove old messages
        self.spam_check[author_id] = [
            msg_time for msg_time in self.spam_check[author_id]
            if current_time - msg_time < timedelta(seconds=timeframe)
        ]

        # Add new message
        self.spam_check[author_id].append(current_time)

        # Check if threshold is exceeded
        return len(self.spam_check[author_id]) >= threshold

    async def check_caps(self, message: discord.Message) -> bool:
        """Check for excessive caps usage"""
        if len(message.content) < self.config["caps"]["min_length"]:
            return False

        caps_count = sum(1 for c in message.content if c.isupper())
        caps_ratio = caps_count / len(message.content)

        return caps_ratio > self.config["caps"]["threshold"]

    async def check_banned_words(self, message: discord.Message) -> str:
        """Check for banned words"""
        content = message.content.lower()
        
        # Check exact matches
        for word in self.config["banned_words"]["words"]:
            if word.lower() in content:
                return word

        # Check wildcards
        for pattern in self.config["banned_words"]["wildcards"]:
            if any(part.lower() in content for part in pattern.split('*')):
                return pattern

        return ""

    async def handle_violation(self, message: discord.Message, violation_type: str, reason: str):
        """Handle automod violations"""
        try:
            # Create warning embed
            embed = Embed.create(
                title="⚠️ AutoMod Warning",
                description=f"Violation detected in {message.channel.mention}",
                color=discord.Color.orange(),
                field_User=message.author.mention,
                field_Type=violation_type.title(),
                field_Reason=reason
            )

            # Delete violating message
            await message.delete()

            # Send warning
            warning_msg = await message.channel.send(embed=embed)
            await warning_msg.delete(delay=5)

            # Log warning to database
            async with db.pool.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO warnings (user_id, guild_id, warning_type, reason)
                    VALUES (?, ?, ?, ?)
                """, (str(message.author.id), str(message.guild.id), violation_type, reason))
                await db.pool.commit()

            # Check warning threshold
            async with db.pool.cursor() as cursor:
                await cursor.execute("""
                    SELECT COUNT(*) FROM warnings
                    WHERE user_id = ? AND guild_id = ?
                    AND timestamp > datetime('now', '-1 day')
                """, (str(message.author.id), str(message.guild.id)))
                warning_count = (await cursor.fetchone())[0]

                if warning_count >= self.config["punishments"]["warn_threshold"]:
                    await self.mute_user(message.author)

        except discord.Forbidden:
            pass  # Bot lacks permissions
        except Exception as e:
            await event_dispatcher.dispatch('error', None, e)

    async def mute_user(self, member: discord.Member):
        """Mute a user for the configured duration"""
        muted_role = discord.utils.get(member.guild.roles, name="Muted")
        if not muted_role:
            # Create muted role if it doesn't exist
            try:
                muted_role = await member.guild.create_role(
                    name="Muted",
                    reason="AutoMod: Created muted role"
                )
                # Set permissions for all channels
                for channel in member.guild.channels:
                    await channel.set_permissions(muted_role, send_messages=False)
            except discord.Forbidden:
                return

        try:
            # Apply mute
            await member.add_roles(muted_role, reason="AutoMod: Exceeded warning threshold")
            
            # Create notification embed
            embed = Embed.create(
                title="🔇 User Muted",
                description=f"{member.mention} has been muted for {self.config['punishments']['mute_duration']} minutes",
                color=discord.Color.red()
            )
            
            # Send notification
            log_channel = member.guild.system_channel
            if log_channel:
                await log_channel.send(embed=embed)

            # Schedule unmute
            await asyncio.sleep(self.config["punishments"]["mute_duration"] * 60)
            await member.remove_roles(muted_role, reason="AutoMod: Mute duration expired")

        except discord.Forbidden:
            pass

    @commands.group(name="automod")
    @commands.has_permissions(administrator=True)
    async def automod(self, ctx):
        """Automod configuration commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @automod.command(name="toggle")
    async def toggle_automod(self, ctx, state: bool):
        """Toggle AutoMod on/off"""
        self.config["enabled"] = state
        self.save_config()
        await ctx.send(f"✅ AutoMod has been {'enabled' if state else 'disabled'}")

    @automod.command(name="addword")
    async def add_banned_word(self, ctx, *, word: str):
        """Add a word to the banned list"""
        self.config["banned_words"]["words"].append(word.lower())
        self.save_config()
        await ctx.send(f"✅ Added '{word}' to banned words")

    @automod.command(name="removeword")
    async def remove_banned_word(self, ctx, *, word: str):
        """Remove a word from the banned list"""
        try:
            self.config["banned_words"]["words"].remove(word.lower())
            self.save_config()
            await ctx.send(f"✅ Removed '{word}' from banned words")
        except ValueError:
            await ctx.send("❌ Word not found in banned words list")

async def setup(bot):
    """Setup the AutoMod cog"""
    cog = AutoMod(bot)
    await cog.setup_tables()
    await bot.add_cog(cog)