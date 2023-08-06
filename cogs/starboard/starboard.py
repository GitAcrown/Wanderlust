import json
import logging
import time
from datetime import datetime
from typing import Any, Optional, List

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import dataio

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

DEFAULT_SETTINGS = {
    'channel_id': None,
    'threshold': 5,
    'send_reminder': True,
    'bot_star': False
}

class Starboard(commands.GroupCog, group_name="starboard", description="Gestion et maintenance d'un salon de messages favoris"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        self.task_message_expire.start()
    
    def __init_guilds_db(self, guilds: List[discord.Guild] | None = None):
        guilds = guilds or list(self.bot.guilds)
        for guild in guilds:
            query = """CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                votes TEXT,
                embed_id INTEGER,
                added_at REAL
                )"""
            self.data.execute(guild, query)
            
            query = """CREATE TABLE IF NOT EXISTS settings (
                name TEXT PRIMARY KEY,
                value TEXT
                )"""
            self.data.execute(guild, query)
            self.data.executemany(guild, "INSERT OR IGNORE INTO settings VALUES (?, ?)", DEFAULT_SETTINGS.items())
            
    @commands.Cog.listener()
    async def on_ready(self):
        self.__init_guilds_db()
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self.__init_guilds_db([guild])
        
    def cog_unload(self):
        self.data.close_all_databases()
        self.task_message_expire.cancel()
        
    @tasks.loop(hours=1)
    async def task_message_expire(self):
        expiration = datetime.utcnow().timestamp() - 86400
        for guild in self.bot.guilds:
            self.delete_expired_messages_metadata(guild, expiration)
            
            # Envoie des rappels
            settings = self.get_settings(guild)
            if settings['send_reminder'] and settings['channel_id']: # Si le salon est défini et que les rappels sont activés
                half_threshold = settings['threshold'] // 2
                # On veut l'envoyer qu'une seule fois donc on prend les messages qui ont été ajoutés il y a moins de 1h30
                reminder_limit = datetime.utcnow().timestamp() - 5400
                messages = self.data.fetchall(guild, """SELECT * FROM messages WHERE added_at > ? AND embed_id IS NULL AND votes >= ?""", (reminder_limit, half_threshold))
                if messages:
                    for message in messages:
                        channel = guild.get_channel(message['channel_id'])
                        if not isinstance(channel, discord.TextChannel):
                            continue
                        try:
                            message = await channel.fetch_message(message['message_id'])
                        except discord.NotFound:
                            continue
                        
                        starboard_channel = guild.get_channel(settings['channel_id'])
                        if not isinstance(starboard_channel, discord.TextChannel):
                            continue
                        
                        text = f"Ce message a reçu plus de 50% des votes nécessaire mais n'a pas encore été ajouté au salon {starboard_channel.mention} !"
                        await message.reply(text, delete_after=300)
                
        logger.info("Suppression des messages expirés Starboard effectuée")
        
    # Fonctions
    
    def get_settings(self, guild: discord.Guild) -> dict[str, Any]:
        query = """SELECT * FROM settings"""
        settings = self.data.fetchall(guild, query)
        return {name: json.loads(value) for name, value in settings}
    
    def get_starboard_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        settings = self.get_settings(guild)
        channel_id = settings['channel_id']
        if channel_id is None:
            return None
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        return channel
    
    def get_message_metadata(self, guild: discord.Guild, message_id: int) -> Optional[dict[str, Any]]:
        query = """SELECT * FROM messages WHERE message_id = ?"""
        metadata = self.data.fetchone(guild, query, (message_id,))
        if metadata is None:
            return None
        return {
            'votes': list(map(int, metadata['votes'].split(';'))) if metadata['votes'] else [],
            'channel_id': int(metadata['channel_id']),
            'votes': [int(vote) for vote in metadata['votes'].split(';') if vote],
            'embed_id': int(metadata['embed_id']) if metadata['embed_id'] else None,
            'added_at': float(metadata['added_at'])
        }
        
    def set_message_metadata(self, guild: discord.Guild, message_id: int, metadata: dict[str, Any]):
        query = """INSERT OR REPLACE INTO messages VALUES (?, ?, ?, ?, ?)"""
        self.data.execute(guild, query, (message_id, metadata['channel_id'], ';'.join(map(str, metadata['votes'])), metadata['embed_id'], metadata['added_at']))
        
    def delete_message_metadata(self, guild: discord.Guild, message_id: int):
        query = """DELETE FROM messages WHERE message_id = ?"""
        self.data.execute(guild, query, (message_id,))
        
    def delete_expired_messages_metadata(self, guild: discord.Guild, expiration: float):
        query = """DELETE FROM messages WHERE added_at < ?"""
        self.data.execute(guild, query, (expiration,))
        
    async def get_embed(self, message: discord.Message) -> discord.Embed:
        guild = message.guild
        if not isinstance(guild, discord.Guild):
            raise ValueError("Le message n'est pas dans une guilde")
        
        metadata = self.get_message_metadata(guild, message.id)
        if not metadata:
            raise KeyError(f"Le message '{message.id}' n'a pas de données liées")
        
        reply_text = ''
        reply_thumb = None
        if message.reference and message.reference.message_id:
            try:
                reference_msg : discord.Message = await message.channel.fetch_message(message.reference.message_id)
                reply_text = f"> **{reference_msg.author.name}** · <t:{int(reference_msg.created_at.timestamp())}>\n> {reference_msg.content if reference_msg.content else '[Média affiché]'}\n\n"
                _reply_img = [a for a in reference_msg.attachments if a.content_type in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']]
                if _reply_img:
                    reply_thumb = _reply_img[0]
            except Exception as e:
                logger.info(e, exc_info=True)
        
        message_content = message.content
        # message_content += f"\n[→ Aller au message]({message.jump_url})"
        
        content = reply_text + message_content
        votes = len(metadata['votes'])
        footxt = f"⭐ {votes}"
        
        em = discord.Embed(description=content, timestamp=message.created_at, color=0x2b2d31)
        em.set_author(name=message.author.name, icon_url=message.author.display_avatar.url)
        em.set_footer(text=footxt)
        
        image_preview = None
        media_links = []
        for a in message.attachments:
            if a.content_type in ['image/jpeg', 'image/png', 'image/gif', 'image/webp'] and not image_preview:
                image_preview = a.url
            else:
                media_links.append(a.url)
        for msge in message.embeds:
            if msge.image and not image_preview:
                image_preview = msge.image.url
            elif msge.thumbnail and not image_preview:
                image_preview = msge.thumbnail.url
        
        if image_preview:
            em.set_image(url=image_preview)
        if reply_thumb:
            em.set_thumbnail(url=reply_thumb)
        if media_links:
            linkstxt = [f"[[{l.split('/')[-1]}]]({l})" for l in media_links]
            em.add_field(name="Pièce(s) jointe(s)", value='\n'.join(linkstxt))
            
        return em
            
    async def post_starboard_message(self, message: discord.Message):
        guild = message.guild
        if not isinstance(guild, discord.Guild):
            raise ValueError("Le message n'est pas dans une guilde")
        
        post_channel = self.get_starboard_channel(guild)
        if not post_channel:
            raise ValueError("Channel Starboard non configuré")

        try:
            embed = await self.get_embed(message)
        except KeyError as e:
            logger.error(e, exc_info=True)
            raise
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Aller au message", url=message.jump_url))
        
        try:
            embed_msg = await post_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(e, exc_info=True)
            return
        
        metadata = {
            'message_id': message.id,
            'channel_id': message.channel.id,
            'votes': [],
            'embed_id': embed_msg.id,
            'added_at': message.created_at.timestamp()
        }
        self.set_message_metadata(guild, message.id, metadata)
    
    async def edit_starboard_message(self, original_message: discord.Message):
        guild = original_message.guild
        
        if not isinstance(guild, discord.Guild):
            raise ValueError("Le message n'est pas dans une guilde")
        
        post_channel = self.get_starboard_channel(guild)
        if not post_channel:
            raise ValueError("Channel Starboard non configuré")
    
        metadata = self.get_message_metadata(guild, original_message.id)
        if not metadata:
            raise KeyError(f"Le message '{original_message.id}' n'a pas de données liées")
        
        try:
            embed_msg = await post_channel.fetch_message(metadata['embed_id'])
        except:
            logger.info(f"Impossible d'accéder à {metadata['embed_message']} : données supprimées")
            self.delete_message_metadata(guild, original_message.id)
        else:
            embed = await self.get_embed(original_message)
            await embed_msg.edit(embed=embed)
        
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        channel = self.bot.get_channel(payload.channel_id)
        emoji = payload.emoji
        if isinstance(channel, discord.TextChannel):
            guild = channel.guild
            if emoji.name == '⭐':
                settings = self.get_settings(guild)
                if settings['channel_id']:
                    message = await channel.fetch_message(payload.message_id)
                    if message.created_at.timestamp() + 86400 >= datetime.utcnow().timestamp():
                        user = guild.get_member(payload.user_id)
                        if not user:
                            return
                        
                        post_channel = guild.get_channel(int(settings['channel_id']))
                        if not post_channel:
                            return
                        
                        metadata = self.get_message_metadata(guild, message.id)
                        if not metadata:
                            created_at = datetime.utcnow().timestamp()
                            metadata = {
                                'message_id': message.id,
                                'channel_id': message.channel.id,
                                'votes': [],
                                'embed_id': None,
                                'added_at': created_at
                            }
                            self.set_message_metadata(guild, message.id, metadata)
                        
                        if user.id not in metadata['votes']:
                            metadata['votes'].append(user.id)
                            self.set_message_metadata(guild, message.id, metadata)
                            
                            if len(metadata['votes']) >= settings['threshold']:
                                if not metadata['embed_id']:
                                    await self.post_starboard_message(message)
                                    try:
                                        notif = await message.reply(f"## `⭐` Ce message a été enregistré sur {post_channel.mention} !", mention_author=False)
                                        await notif.delete(delay=90)
                                    except:
                                        raise
                                else:
                                    await self.edit_starboard_message(message)
    
    # Commandes
    
    @app_commands.command(name='channel')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.rename(channel='salon')
    async def set_channel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        """Définir le salon des messages favoris

        :param channel: Salon des messages favoris, laisser vide pour désactiver
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés", ephemeral=True)

        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("**Salon invalide ·** Seul les salons textuels classiques peuvent héberger les messages favoris", ephemeral=True)

        if channel is None:
            self.data.execute(guild, "UPDATE settings SET value = NULL WHERE name = 'channel_id'")
            await interaction.response.send_message("Salon des messages favoris désactivé", ephemeral=True)
        else:
            self.data.execute(guild, "UPDATE settings SET value = ? WHERE name = 'channel_id'", (channel.id,))
            await interaction.response.send_message(f"Salon des messages favoris défini sur {channel.mention}", ephemeral=True)

    @app_commands.command(name='threshold')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.rename(threshold='seuil')
    async def set_threshold(self, interaction: discord.Interaction, threshold: app_commands.Range[int, 1]):
        """Définir le seuil de votes pour qu'un message soit ajouté au salon des messages favoris

        :param threshold: Seuil de votes
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés", ephemeral=True)

        self.data.execute(guild, "UPDATE settings SET value = ? WHERE name = 'threshold'", (threshold,))
        await interaction.response.send_message(f"Seuil de votes défini sur {threshold}", ephemeral=True)
        
    @app_commands.command(name='reminder')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.rename(reminder='rappel')
    async def set_reminder(self, interaction: discord.Interaction, reminder: bool):
        """Définir si un rappel doit être envoyé lorsqu'un message est proche du seuil

        :param reminder: Activer ou désactiver le rappel
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés", ephemeral=True)

        self.data.execute(guild, "UPDATE settings SET value = ? WHERE name = 'send_reminder'", (reminder,))
        await interaction.response.send_message(f"Rappel {'activé' if reminder else 'désactivé'}", ephemeral=True)
        
    @app_commands.command(name='botstar')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.rename(bot_star='botétoile')
    async def set_bot_star(self, interaction: discord.Interaction, bot_star: bool):
        """Définir si le bot peut lui-même ajouter une étoile s'il détecte qu'un message est populaire
        
        :param bot_star: Activer ou désactiver l'étoile du bot
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés", ephemeral=True)

        self.data.execute(guild, "UPDATE settings SET value = ? WHERE name = 'bot_star'", (bot_star,))
        await interaction.response.send_message(f"{'Activation' if bot_star else 'Désactivation'} de l'étoile du bot", ephemeral=True)
        
    
async def setup(bot):
    await bot.add_cog(Starboard(bot))
