import logging

import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional

from common import dataio

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
                repeat_interval INTEGER,
                last_broadcast INTEGER DEFAULT 0
                )"""
            self.data.execute(g, query)
            
    def cog_unload(self):
        self.data.close_all_databases()
        
    def set_announcement(self, guild: discord.Guild, name: str, message: str, channel: discord.TextChannel, repeat_count: int, repeat_interval: int, last_broadcast: int):
        query = """INSERT OR REPLACE INTO announcements (name, message, channel_id, repeat_count, repeat_interval, last_broadcast) VALUES (?, ?, ?, ?, ?, ?)"""
        self.data.execute(guild, query, (name.lower(), message, channel.id, repeat_count, repeat_interval, last_broadcast))
    
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
                if int(announcement['last_broadcast']) + int(announcement['repeat_interval']) > time.time():
                    continue
                await channel.send(announcement['message'])
                self.set_announcement(guild, announcement['name'], announcement['message'], channel, int(announcement['repeat_count']) - 1, int(announcement['repeat_interval']), int(time.time()))
                if int(announcement['repeat_count']) - 1 == 0:
                    self.delete_announcement(guild, announcement['name'])
                    await channel.send(f"**Annonce terminée**\nL'annonce {announcement['name']} a été supprimée car elle a atteint son nombre de répétitions maximum ({announcement['repeat_count']}).", delete_after=60)
    
    @broadcast_loop.before_loop
    async def before_broadcast_loop(self):
        await self.bot.wait_until_ready()
        logger.info('Broadcast loop lancée')
                
    # Commandes ----------------------------------------------------------------
    
    broadgroup = app_commands.Group(name='broadcast', description='Gestionnaire d\'annonces automatisées', guild_only=True, default_permissions=discord.Permissions(manage_channels=True, manage_messages=True))
    
    @broadgroup.command(name='set')
    async def command_set_announcement(self, interaction: discord.Interaction, name: str, channel: discord.TextChannel, repeat_count: app_commands.Range[int, 0], repeat_interval: app_commands.Range[int, 5]):
        """Crée ou modifier annonce automatisée
        
        :param name: Nom de l'annonce
        :param channel: Salon de l'annonce
        :param repeat_count: Nombre de répétition (0 = infini)
        :param repeat_interval: Intervalle de répétition en minutes (min. 5 minutes)
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            await interaction.response.send_message('Cette commande ne peut pas être utilisée en dehors d\'un serveur.', ephemeral=True)
            return
    
        # Attendre une réponse pour le message
        await interaction.response.send_message("**En attente du message à répéter...**\nEnvoyez le message à répéter (Expire dans 5m.)", ephemeral=True)
        try:
            msg = await self.bot.wait_for('message', check=lambda m: m.author == interaction.user and m.channel == interaction.channel, timeout=300)
        except asyncio.TimeoutError:
            await interaction.followup.send('**Temps écoulé**\nVous n\'avez pas envoyé de message à répéter à temps.', ephemeral=True)
            return
        message = msg.content
        try: 
            await msg.delete()
        except discord.HTTPException:
            pass
        
        repeat_seconds = repeat_interval * 60
        if self.get_announcement(guild, name): # Modification	
            self.set_announcement(guild, name, message, channel, repeat_count, repeat_seconds, 0)
            return await interaction.followup.send(f'**Annonce modifiée avec succès**\nElle sera envoyée dans {channel.mention} toutes les {repeat_interval} minutes.', ephemeral=True)
        
        # Création
        if len(self.get_announcements(guild)) >= MAX_ANNOUCEMENTS_PER_CHANNEL:
            await interaction.followup.send(f'Il y a déjà {MAX_ANNOUCEMENTS_PER_CHANNEL} annonces automatisées dans ce serveur. Vous ne pouvez pas en créer plus.', ephemeral=True)
            return
        self.set_announcement(guild, name, message, channel, repeat_count, repeat_seconds, 0)
        await interaction.followup.send(f'**Annonce créée avec succès**\nElle sera envoyée dans {channel.mention} toutes les {repeat_interval} minutes.', ephemeral=True)
        
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
            embed.add_field(name=a['name'].capitalize(), value=f"```{a['message']}```\n**Répétitions restantes :** `{a['repeat_count']}`\n**Intervalle :** `{int(a['repeat_interval']) / 60} minutes`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @broadgroup.command(name='manual')
    async def command_manuam_announcement(self, interaction: discord.Interaction, name: str):
        """Envoie une annonce automatisée immédiatement (décomptera une répétition)

        :param name: Nom de l'annonce à envoyer"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            await interaction.response.send_message('Cette commande ne peut pas être utilisée en dehors d\'un serveur.', ephemeral=True)
            return
        announcement = self.get_announcement(guild, name)
        if not announcement:
            await interaction.response.send_message(f"**Annonce introuvable**\nIl n'y a aucune annonce avec le nom `{name}` d'active sur ce serveur", ephemeral=True)
            return
        channel = guild.get_channel(int(announcement['channel_id']))
        if not channel or not isinstance(channel, discord.TextChannel | discord.Thread):
            await interaction.response.send_message(f"**Annonce introuvable**\nLe salon de l'annonce `{name}` n'existe plus ou son type a changé.", ephemeral=True)
            return
        await channel.send(announcement['message'])
        self.set_announcement(guild, name, announcement['message'], channel, int(announcement['repeat_count']) - 1, int(announcement['repeat_interval']), int(time.time()))
        if int(announcement['repeat_count']) - 1 == 0:
            self.delete_announcement(guild, announcement['name'])
            await channel.send(f"**Annonce terminée**\nL'annonce {announcement['name']} a été supprimée car elle a atteint son nombre de répétitions maximum ({announcement['repeat_count']}).", delete_after=60)
        await interaction.response.send_message(f"**Annonce envoyée**\nL'annonce {announcement['name']} a été envoyée dans {channel.mention}.", ephemeral=True, delete_after=60)
        
async def setup(bot):
    await bot.add_cog(Broadcast(bot))