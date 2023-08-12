import logging
from typing import Dict, List, Union, Optional

import discord
import json
import random
from datetime import datetime, timezone
from discord import app_commands
from discord.ext import commands, tasks

from common import dataio

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

class Storage(commands.Cog):
    """Conserver fichiers et médias dans le bot"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
    @commands.Cog.listener()
    async def on_ready(self):
        self._init_guilds_db()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._init_guilds_db(guild)
        
    def cog_unload(self):
        self.data.close_all_databases()
        
    def _init_guilds_db(self, guild: Optional[discord.Guild] = None):
        guilds = [guild] if guild else self.bot.guilds
        for g in guilds:
            metadata_query = """CREATE TABLE IF NOT EXISTS metadata (
                id TEXT PRIMARY KEY,
                name TEXT,
                type TEXT,
                path TEXT,
                url TEXT,
                added_by INTEGER,
                created_at INTEGER
                )"""
            self.data.execute(g, metadata_query)
    
    
async def setup(bot):
    await bot.add_cog(Storage(bot))
