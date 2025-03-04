import discord
from discord.ext import commands
import random
from datetime import datetime, timedelta

class Economy(commands.Cog):
    """💰 Sistem Ekonomi"""
    
    def __init__(self, bot):
        self.bot = bot
        self.work_cooldown = {}
        self.daily_cooldown = {}
        self.jobs = {
            "programmer": {"min": 100, "max": 500},
            "designer": {"min": 150, "max": 450},
            "writer": {"min": 120, "max": 400},
            "youtuber": {"min": 200, "max": 600},
            "trader": {"min": 0, "max": 1000}  # High risk high reward
        }
        
    async def get_balance(self, user_id: int) -> int:
        async with self.bot.pool.acquire() as conn:
            result = await conn.fetchone(
                "SELECT balance FROM economy WHERE user_id = ?", 
                (str(user_id),)
            )
            return result['balance'] if result else 0
            
    async def update_balance(self, user_id: int, amount: int):
        async with self.bot.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO economy (user_id, balance) 
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                balance = balance + ?
            """, (str(user_id), amount, amount))
            
    @commands.command(name="balance", aliases=["bal"])
    async def check_balance(self, ctx, member: discord.Member = None):
        """💰 Cek saldo"""
        target = member or ctx.author
        balance = await self.get_balance(target.id)
        
        embed = discord.Embed(
            title="💰 Saldo",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="User", value=target.mention)
        embed.add_field(name="Saldo", value=f"🪙 {balance:,}")
        
        await ctx.send(embed=embed)
        
    @commands.command(name="daily")
    async def daily_reward(self, ctx):
        """💰 Claim hadiah harian"""
        if ctx.author.id in self.daily_cooldown:
            remaining = self.daily_cooldown[ctx.author.id] - datetime.utcnow()
            if remaining.total_seconds() > 0:
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                return await ctx.send(
                    f"❌ Tunggu **{hours}** jam **{minutes}** menit lagi!"
                )
                
        amount = random.randint(100, 1000)
        await self.update_balance(ctx.author.id, amount)
        self.daily_cooldown[ctx.author.id] = datetime.utcnow() + timedelta(days=1)
        
        embed = discord.Embed(
            title="💰 Daily Reward",
            description=f"Anda mendapat 🪙 **{amount:,}**!",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        
    @commands.command(name="work")
    async def work(self, ctx):
        """💼 Bekerja untuk mendapatkan uang"""
        if ctx.author.id in self.work_cooldown:
            remaining = self.work_cooldown[ctx.author.id] - datetime.utcnow()
            if remaining.total_seconds() > 0:
                minutes, seconds = divmod(int(remaining.total_seconds()), 60)
                return await ctx.send(
                    f"❌ Anda masih lelah! Istirahat **{minutes}** menit lagi!"
                )
                
        job = random.choice(list(self.jobs.keys()))
        earnings = random.randint(
            self.jobs[job]["min"],
            self.jobs[job]["max"]
        )
        
        await self.update_balance(ctx.author.id, earnings)
        self.work_cooldown[ctx.author.id] = datetime.utcnow() + timedelta(hours=1)
        
        embed = discord.Embed(
            title="💼 Work Report",
            description=f"Anda bekerja sebagai **{job}**\n"
                      f"dan mendapatkan 🪙 **{earnings:,}**!",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        
    @commands.command(name="give")
    async def give_money(self, ctx, member: discord.Member, amount: int):
        """💸 Berikan uang ke member lain"""
        if amount <= 0:
            return await ctx.send("❌ Jumlah harus lebih dari 0!")
            
        if member.bot:
            return await ctx.send("❌ Tidak bisa memberi uang ke bot!")
            
        sender_balance = await self.get_balance(ctx.author.id)
        if sender_balance < amount:
            return await ctx.send("❌ Saldo tidak cukup!")
            
        await self.update_balance(ctx.author.id, -amount)
        await self.update_balance(member.id, amount)
        
        embed = discord.Embed(
            title="💸 Transfer Berhasil",
            description=f"{ctx.author.mention} memberikan\n"
                      f"🪙 **{amount:,}** ke {member.mention}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        
    @commands.command(name="leaderboard", aliases=["rich"])
    async def show_leaderboard(self, ctx):
        """📊 Tampilkan leaderboard ekonomi"""
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT user_id, balance 
                FROM economy 
                ORDER BY balance DESC 
                LIMIT 10
            """)
            
        if not rows:
            return await ctx.send("❌ Belum ada data ekonomi!")
            
        embed = discord.Embed(
            title="🏆 Richest Users",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )
        
        for idx, row in enumerate(rows, 1):
            user = self.bot.get_user(int(row['user_id']))
            if user:
                embed.add_field(
                    name=f"#{idx} {user.name}",
                    value=f"🪙 {row['balance']:,}",
                    inline=False
                )
                
        await ctx.send(embed=embed)

    @commands.command(name="rob")
    @commands.cooldown(1, 3600, commands.BucketType.user)
    async def rob_user(self, ctx, member: discord.Member):
        """🦹‍♂️ Coba mencuri uang (Beresiko)"""
        if member.bot:
            return await ctx.send("❌ Tidak bisa mencuri dari bot!")
            
        target_balance = await self.get_balance(member.id)
        if target_balance < 100:
            return await ctx.send("❌ Target terlalu miskin!")
            
        # 30% chance to succeed
        if random.random() < 0.3:
            stolen = random.randint(1, min(target_balance, 1000))
            await self.update_balance(ctx.author.id, stolen)
            await self.update_balance(member.id, -stolen)
            
            embed = discord.Embed(
                title="🦹‍♂️ Pencurian Berhasil",
                description=f"Anda mencuri 🪙 **{stolen:,}** dari {member.mention}!",
                color=discord.Color.green()
            )
        else:
            fine = random.randint(100, 500)
            await self.update_balance(ctx.author.id, -fine)
            
            embed = discord.Embed(
                title="🚔 Pencurian Gagal",
                description=f"Anda tertangkap dan didenda 🪙 **{fine:,}**!",
                color=discord.Color.red()
            )
            
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Economy(bot))