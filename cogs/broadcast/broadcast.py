import logging

import time
import json
import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional, List, Dict, Any, Tuple

from discord.utils import MISSING

from common import dataio
from common.utils import fuzzy

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

MAX_ANNOUCEMENTS_PER_CHANNEL = 5

class Broadcast(commands.Cog):
    """Créez des annonces automatisées pour votre serveur"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
    @commands.Cog.listener()
    async def on_ready(self):
        self._initialize_guild_db()
        self.broadcast_loop.start()
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._initialize_guild_db(guild)
        
    def _initialize_guild_db(self, guild: Optional[discord.Guild] = None):
        guilds = [guild] if guild else self.bot.guilds
        for g in guilds:
            query = """CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                message TEXT,
                channel_id INTEGER,
                repeat_count INTEGER,
                repeat_interval INTEGER
                )"""
            self.data.execute(g, query)
            
    def cog_unload(self):
        self.data.close_all_databases()
        
    def set_announcement(self, guild: discord.Guild, name: str, message: str, channel: discord.TextChannel, repeat_count: int, repeat_interval: int):
        query = """INSERT OR REPLACE INTO announcements (name, message, channel_id, repeat_count, repeat_interval) VALUES (?, ?, ?, ?, ?)"""
        self.data.execute(guild, query, (name.lower(), message, channel.id, repeat_count, repeat_interval))
    
    def get_announcement(self, guild: discord.Guild, name: str):
        query = """SELECT * FROM announcements WHERE name = ?"""
        return self.data.fetchone(guild, query, (name.lower(),))
    
    def get_announcements(self, guild: discord.Guild):
        query = """SELECT * FROM announcements"""
        return self.data.fetchall(guild, query)
    
    def delete_announcement(self, guild: discord.Guild, name: str):
        query = """DELETE FROM announcements WHERE name = ?"""
        self.data.execute(guild, query, (name.lower(),))
        
    # Broadcast Loop ------------------------------------------------------------
    
    @tasks.loop(seconds=30)
    async def broadcast_loop(self):
        for guild in self.bot.guilds:
            announcements = self.get_announcements(guild)
            for announcement in announcements:
                channel = self.bot.get_channel(int(announcement['channel_id']))
                if not isinstance(channel, discord.TextChannel):
                    continue
                await channel.send(announcement['message'])
                self.set_announcement(guild, announcement['name'], announcement['message'], channel, int(announcement['repeat_count']) - 1, int(announcement['repeat_interval']))
                if announcement['repeat_count'] - 1 == 0:
                    self.delete_announcement(guild, announcement['name'])
                    await channel.send(f"**Annonce terminée**\nL'annonce {announcement['name']} a été supprimée car elle a atteint son nombre de répétitions maximum ({announcement['repeat_count']}).", delete_after=60)
    
    @broadcast_loop.before_loop
    async def before_broadcast_loop(self):
        await self.bot.wait_until_ready()
        logger.info('Broadcast loop lancée')
                
    # Commandes ----------------------------------------------------------------
    
    broadgroup = app_commands.Group(name='broadcast', description='Gestionnaire d\'annonces automatisées', guild_only=True, default_permissions=discord.Permissions(manage_channels=True, manage_messages=True))
    
    @broadgroup.command(name='set')
    async def command_set_announcement(self, interaction: discord.Interaction, name: str, message: str, channel: discord.TextChannel, repeat_count: int, repeat_interval: int):
        """Crée ou modifier annonce automatisée
        
        :param name: Nom de l'annonce
        :param message: Message de l'annonce
        :param channel: Salon de l'annonce
        :param repeat_count: Nombre de répétition
        :param repeat_interval: Intervalle de répétition en minutes
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            await interaction.response.send_message('Cette commande ne peut pas être utilisée en dehors d\'un serveur.', ephemeral=True)
            return
        
        if repeat_count < 0:
            await interaction.response.send_message('Le nombre de répétitions doit être supérieur ou égal à 0.', ephemeral=True)
            return
        if repeat_interval < 10:
            await interaction.response.send_message('L\'intervalle de répétition doit être supérieur ou égal à 10 minutes.', ephemeral=True)
            return
    
        
        repeat_seconds = repeat_interval * 60
        if self.get_announcement(guild, name): # Modification	
            self.set_announcement(guild, name, message, channel, repeat_count, repeat_seconds)
            return await interaction.response.send_message(f'**Annonce modifiée avec succès**\nElle sera envoyée dans {channel.mention} toutes les {repeat_interval} minutes.', ephemeral=True)
        
        # Création
        if len(self.get_announcements(guild)) >= MAX_ANNOUCEMENTS_PER_CHANNEL:
            await interaction.response.send_message(f'Il y a déjà {MAX_ANNOUCEMENTS_PER_CHANNEL} annonces automatisées dans ce serveur. Vous ne pouvez pas en créer plus.', ephemeral=True)
            return
        self.set_announcement(guild, name, message, channel, repeat_count, repeat_seconds)
        await interaction.response.send_message(f'**Annonce créée avec succès**\nElle sera envoyée dans {channel.mention} toutes les {repeat_interval} minutes.', ephemeral=True)
        
    @broadgroup.command(name='delete')
    async def command_delete_announcement(self, interaction: discord.Interaction, name: str):
        """Supprime une annonce automatisée

        :param name: Nom de l'annonce à supprimer
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            await interaction.response.send_message('Cette commande ne peut pas être utilisée en dehors d\'un serveur.', ephemeral=True)
            return

        if not self.get_announcement(guild, name):
            await interaction.response.send_message(f"**Annonce introuvable**\nIl n'y a aucune annonce avec le nom `{name}` d'active sur ce serveur", ephemeral=True)
            return

        self.delete_announcement(guild, name)
        await interaction.response.send_message(f"**Annonce supprimée avec succès**\nL'annonce `{name}` ne sera plus envoyée.", ephemeral=True)

    @broadgroup.command(name='list')
    async def command_list_announcements(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        """Liste toutes les annonces automatisées du salon actuel ou sélectionné
        
        :param channel: Autre salon dont on veut voir les annonces"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            await interaction.response.send_message('Cette commande ne peut pas être utilisée en dehors d\'un serveur.', ephemeral=True)
            return
        chan = channel or interaction.channel
        if not isinstance(chan, discord.TextChannel | discord.Thread):
            await interaction.response.send_message("Cette commande ne peut pas être utilisée en dehors d'un salon écrit ou un thread.", ephemeral=True)
            return
        
        announcements = self.get_announcements(guild)
        if not announcements:
            return await interaction.response.send_message('**Aucune annonce**\nIl n\'y a aucune annonce active sur ce serveur.', ephemeral=True)
        
        # On garde seulement les annonces du salon demandé
        announcements = [a for a in announcements if a['channel_id'] == chan.id]
        if not announcements:
            return await interaction.response.send_message(f'**Aucune annonce sur ce salon**\nIl n\'y a aucune annonce active dans {chan.mention}.', ephemeral=True)

        embed = discord.Embed(title=f'Annonces automatisées sur `#{chan.name}`', color=0x2F3136)
        for a in announcements:
            embed.add_field(name=a['name'].capitalize(), value=f"**Message :** `{a['message']}`\n**Répétitions :** `{a['repeat_count']}`\n**Intervalle :** `{a['repeat_interval']} minutes`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

        
        
async def setup(bot):
    await bot.add_cog(Broadcast(bot))