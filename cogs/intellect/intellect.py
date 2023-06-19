from code import interact
import logging
from re import S
import time
from discord.interactions import Interaction
import openai
from regex import P

import unidecode
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional, List, Literal, Dict

from common import dataio
from common.utils import fuzzy, pretty

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

DEFAULT_AI_PROFILE = {
    'name': 'WanderlustAI',
    'description': 'Configuration par défaut de WanderlustAI',
    'avatar_url': None,
    'init_prompt': '',
    'temperature': 0.8,
    'author_id': None,
    'created_at': None
}

GPT_COLOR = 0x10a37f
GPT_LOGO = "https://www.edigitalagency.com.au/wp-content/uploads/chatgpt-logo-white-black-background-png-1.png"

class GPTProfile:
    def __init__(self, 
                 id: str, 
                 name: str, 
                 description: str, 
                 init_prompt: str, 
                 *, 
                 avatar_url: str | None = None, 
                 temperature: float = 0.8,
                 context_size: int = 10,
                 author_id: int | None = None, 
                 created_at: float | None = None) -> None:
        self.id = id
        self.name = name
        self.description = description
        self.avatar_url = avatar_url
        self.init_prompt = init_prompt
        self.temperature = temperature
        self.context_size = context_size
        self.author_id = author_id
        self.created_at = created_at
        
    def __repr__(self) -> str:
        return f'<AIProfile id={self.id} name={self.name}>'
    
    def __str__(self) -> str:
        return f"{self.name} [W.IA]"

class GPTSession:
    def __init__(self, cog: 'Intellect', channel: discord.TextChannel | discord.Thread, profile: GPTProfile, resume: bool = True) -> None:
        self.channel = channel
        self.cog = cog
        self.guild = channel.guild
        self.profile = profile
        
        self.messages : List[dict] = []
        self.context : List[dict] = []
        
        self.load_messages()
        if resume:
            self.resume_session()
    
    def load_messages(self):
        """Recharge les messages de la précédente session depuis la base de données"""
        self.messages = self.cog._get_profile_logs(self.guild, self.profile.id)
        
    def save_messages(self):
        """Sauvegarde les messages de la session dans la base de données"""
        self.cog._set_profile_logs(self.guild, self.profile.id, self.messages)
        
    def resume_session(self):
        """Charge les messages de la précédente session dans le contexte de l'IA"""
        # On garde les messages les plus récents (ceux qui ont été envoyés en dernier)
        self.context = self.messages[-self.profile.context_size:]
        
    def load_to_context(self, messages: List[dict]):
        """Ajoute des messages au contexte de l'IA dans la limite de la taille du contexte"""
        self.context = messages + self.context
        self.context = self.context[:self.profile.context_size]
        
    # Utilitaires -----------------------------
        
    def _context_to_query_messages(self) -> List[dict]:
        """Convertit le contexte en requête pour l'IA"""
        querymsg = []
        for ctx in self.context:
            if ctx['username']:
                username = unidecode.unidecode(ctx['username'])
                username = ''.join([c for c in username if c.isalnum()]).rstrip()
                querymsg.append({'type': ctx['type'], 'content': ctx['content'], 'name': username})
            else:
                querymsg.append({'type': ctx['type'], 'content': ctx['content']})
                
        return querymsg
        
    def _get_full_context(self) -> List[dict]:
        """Récupère le contexte complet de l'IA comprenant le message d'initialisation"""
        querymsg = self._context_to_query_messages()
        init_dict = {'type': 'system', 'content': self.profile.init_prompt}
        return [init_dict] + querymsg
        
    # Fonctions de chat ----------------------
    
    def add_message(self, type: Literal['system', 'assistant', 'user'], content: str, *, username: str = '', message_id: int = 0):
        """Ajoute un message au contexte de l'IA"""
        timestamp = time.time()
        msg_dict = {'timestamp': timestamp, 'type': type, 'content': content, 'username': username, 'message_id': message_id}
        self.messages.append(msg_dict)
        self.load_to_context([msg_dict])
        
    async def get_completion(self):
        """Demande une réponse à ChatGPT à partir du contexte actuel"""
        
        
class Intellect(commands.Cog):
    """Ensemble d'outils exploitant l'IA"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        self.api_key = self.bot.config['OPENAI_APIKEY']
        self.gpt_sessions : Dict[int, GPTSession] = {}
        
    @commands.Cog.listener()
    async def on_ready(self):
        self._init_global_db()
        self._init_guild_db()
    
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._init_guild_db(guild)
        
    def _init_global_db(self):
        query = """CREATE TABLE IF NOT EXISTS guilds (
            guild_id INTEGER PRIMARY KEY,
            credits INTEGER,
            month TEXT
        )"""
        self.data.execute('credits', query)
        
    def _init_guild_db(self, guild: discord.Guild | None = None):
        guilds = [guild] if guild else self.bot.guilds
        for g in guilds:
            profiles_query = """CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                avatar_url TEXT,
                init_prompt TEXT,
                temperature REAL CHECK(temperature >= 0 AND temperature <= 2),
                context_size INTEGER CHECK(context_size >= 2 AND context_size <= 10),
                author_id INTEGER,
                created_at REAL
                )"""
            self.data.execute(g, profiles_query)
        
            stats_query = """CREATE TABLE IF NOT EXISTS stats (
                profile_id TEXT PRIMARY KEY,
                uses INTEGER,
                messages INTEGER,
                tokens INTEGER,
                FOREIGN KEY(profile_id) REFERENCES profiles(id)
                )"""
            self.data.execute(g, stats_query)
            
            logs_query = """CREATE TABLE IF NOT EXISTS logs (
                timestamp REAL,
                profile_id TEXT,
                type TEXT,
                content TEXT,
                username TEXT,
                message_id INTEGER PRIMARY KEY
                )"""
            self.data.execute(g, logs_query)
        
    # Gestion des crédits de serveurs --------------------------------------------------------------
    
    def _get_credits(self, guild: discord.Guild) -> int:
        query = """SELECT credits, month FROM guilds WHERE guild_id = ?"""
        r = self.data.fetchone('credits', query, guild.id)
        if r is None:
            return 0
        
        if r['month'] != datetime.now().strftime('%Y-%m'):
            query = """UPDATE guilds SET credits = ? WHERE guild_id = ?"""
            self.data.execute('credits', query, (0, guild.id))
            return 0

        return r['credits']
    
    def _set_credits(self, guild: discord.Guild, credits: int):
        query = """INSERT OR REPLACE INTO guilds (guild_id, credits, month) VALUES (?, ?, ?)"""
        self.data.execute('credits', query, (guild.id, credits, datetime.now().strftime('%Y-%m')))
        
    def _add_credits(self, guild: discord.Guild, credits: int):
        current = self._get_credits(guild)
        self._set_credits(guild, current + credits)
    
    def _remove_credits(self, guild: discord.Guild, credits: int):
        current = self._get_credits(guild)
        self._set_credits(guild, current - credits)
        
    def _check_credits(self, guild: discord.Guild, credits: int) -> bool:
        return self._get_credits(guild) >= credits
        
    # Calcul du coût d'une requête ChatGPT
    def _tokens_to_real(self, tokens: int) -> float | int:
        """Convertit un nombre de tokens en coût réel"""
        # 0.002$ pour 1000 tokens
        return tokens * (0.002 / 1000)

    # Calcul du coût d'une requête Whisper
    def _audio_duration_to_real(self, seconds: int) -> float | int:
        """Convertit une durée en secondes en coût réel"""
        # 0.006$ pour une minute
        return seconds * (0.006 / 60)

    # Coût réel vers coût en crédits virtuels
    def _real_to_credits(self, cost: float | int) -> int:
        """Convertit un coût réel en crédits virtuels"""
        return round(cost * 1e5) # 1 crédit = 0.00001$
    
    # Coût en crédits virtuels vers coût réel
    def _credits_to_real(self, cost: int) -> float | int:
        """Convertit un coût en crédits virtuels en coût réel"""
        return cost / 1e5
    
    # Gestion des sessions GPT ---------------------------------------------------------------------
    
    def _get_gpt_session(self, channel: discord.TextChannel | discord.Thread) -> GPTSession | None:
        return self.gpt_sessions.get(channel.id, None)
    
    def _set_gpt_session(self, channel: discord.TextChannel | discord.Thread, session: GPTSession):
        # On sauvegarde la session précédente si elle existe
        if channel.id in self.gpt_sessions:
            self.gpt_sessions[channel.id].save_messages()
        
        self.gpt_sessions[channel.id] = session
    
    def _remove_gpt_session(self, channel: discord.TextChannel | discord.Thread):
        # On sauvegarde la session précédente si elle existe
        if channel.id in self.gpt_sessions:
            self.gpt_sessions[channel.id].save_messages()
        self.gpt_sessions.pop(channel.id, None)
        
    # Gestion des profils -------------------------------------------------------------------------
    
    def _get_profile(self, guild: discord.Guild, profile_id: str) -> GPTProfile | None:
        query = """SELECT * FROM profiles WHERE id = ?"""
        r = self.data.fetchone(guild, query, profile_id)
        if r is None:
            return None
        
        return GPTProfile(**r)
    
    def _get_profiles(self, guild: discord.Guild) -> list[GPTProfile]:
        query = """SELECT * FROM profiles"""
        r = self.data.fetchall(guild, query)
        return [GPTProfile(**p) for p in r]
    
    def _set_profile(self, guild: discord.Guild, profile: GPTProfile):
        query = """INSERT OR REPLACE INTO profiles (id, name, description, avatar_url, init_prompt, temperature, author_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
        self.data.execute(guild, query, (profile.id, profile.name, profile.description, profile.avatar_url, profile.init_prompt, profile.temperature, profile.author_id, profile.created_at))

    def _remove_profile(self, guild: discord.Guild, profile_id: str):
        query = """DELETE FROM profiles WHERE id = ?"""
        self.data.execute(guild, query, profile_id)

    # Stats des profils 
    
    def _get_profile_stats(self, guild: discord.Guild, profile_id: str) -> dict | None:
        query = """SELECT * FROM stats WHERE profile_id = ?"""
        r = self.data.fetchone(guild, query, profile_id)
        return dict(r) if r else None
    
    def _get_profiles_stats(self, guild: discord.Guild) -> Dict[str, dict]:
        query = """SELECT * FROM stats"""
        r = self.data.fetchall(guild, query)
        return {p['profile_id']: dict(p) for p in r}
    
    def _set_profile_stats(self, guild: discord.Guild, profile_id: str, stats: dict):
        query = """INSERT OR REPLACE INTO stats (profile_id, uses, messages, tokens) VALUES (?, ?, ?, ?)"""
        self.data.execute(guild, query, (profile_id, stats['uses'], stats['messages'], stats['tokens']))
        
    def _update_profile_stats(self, guild: discord.Guild, profile_id: str, stats: dict):
        current = self._get_profile_stats(guild, profile_id)
        if current is None:
            self._set_profile_stats(guild, profile_id, stats)
            return
        
        for k, v in stats.items():
            current[k] += v
        
        self._set_profile_stats(guild, profile_id, current)
    
    # Logs des profils
    
    def _get_profile_logs(self, guild: discord.Guild, profile_id: str) -> List[dict]:
        query = """SELECT * FROM logs WHERE profile_id = ? ORDER BY timestamp ASC"""
        r = self.data.fetchall(guild, query, profile_id)
        return [dict(l) for l in r]
    
    def _set_profile_logs(self, guild: discord.Guild, profile_id: str, logs: List[dict]):
        query = """INSERT OR REPLACE INTO logs (timestamp, profile_id, type, content, username, message_id) VALUES (?, ?, ?, ?, ?, ?)"""
        self.data.executemany(guild, query, [(l['timestamp'], profile_id, l['type'], l['content'], l['username'], l['message_id']) for l in logs])
        
    def _add_profile_log(self, guild: discord.Guild, profile_id: str, log: dict):
        query = """INSERT INTO logs (timestamp, profile_id, type, content, username, message_id) VALUES (?, ?, ?, ?, ?, ?)"""
        self.data.execute(guild, query, (log['timestamp'], profile_id, log['type'], log['content'], log['username'], log['message_id']))
        
    
    # COMMANDES ====================================================================================
    
    # Profils IA personnalisés -----------------------------------------
    
    profiles_group = app_commands.Group(name='aipro', description="Gestion des profils d'IA personnalisés", guild_only=True)
    
    @profiles_group.command(name='create')
    async def profiles_create(self, interaction: discord.Interaction):
        """Créer ou remplacer un nouveau profil d'IA sur ce serveur"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en dehors des serveurs.", ephemeral=True)
        
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel | discord.Thread):
            return await interaction.response.send_message("Cette commande n'est pas disponible dans les messages privés.", ephemeral=True)
        
        if not channel.permissions_for(interaction.user).manage_messages: #type: ignore
            return await interaction.response.send_message("Vous n'avez pas les permissions nécessaires (`manage_messages`) pour éditer un profil.", ephemeral=True)
        
        modal = CreateProfileModal()
        modal.timeout = 600 # 10 minutes
        await interaction.response.send_modal(modal)

        if await modal.wait():
            await interaction.followup.send("**Création annulée**\nVous avez mis trop de temps pour créer le profil d'IA.", ephemeral=True)
            return
        
        data = modal.data
        profile = GPTProfile(
            id=data['profile_id'],
            name=data['name'],
            description=data['description'],
            avatar_url=data['avatar_url'],
            init_prompt=data['init_prompt'],
            temperature=data['temperature'],
            context_size=data['context_size'],
            author_id=interaction.user.id,
            created_at=datetime.now().timestamp()
        )
        
        if self._get_profile(guild, profile.id) is not None:
            await interaction.followup.send(f"**Profil d'IA modifié**\nLe profil d'IA `{profile.id}` a été modifié avec succès.", ephemeral=True)
        else:
            await interaction.followup.send(f"**Profil d'IA créé**\nLe profil d'IA `{profile.id}` a été créé avec succès.", ephemeral=True)
        self._set_profile(guild, profile)
        
    @profiles_group.command(name="delete")
    @app_commands.rename(profile_id="ID du profil")
    async def profiles_delete(self, interaction: discord.Interaction, profile_id: str):
        """Supprimer un profil d'IA"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en dehors des serveurs.", ephemeral=True)
        
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel | discord.Thread):
            return await interaction.response.send_message("Cette commande n'est pas disponible dans les messages privés.", ephemeral=True)
        
        if not channel.permissions_for(interaction.user).manage_messages: #type: ignore
            return await interaction.response.send_message("Vous n'avez pas les permissions nécessaires (`manage_messages`) pour supprimer un profil.", ephemeral=True)
        
        profile = self._get_profile(guild, profile_id)
        if profile is None:
            return await interaction.response.send_message(f"Le profil d'IA `{profile_id}` n'existe pas.", ephemeral=True)
        
        self._remove_profile(guild, profile_id)
        await interaction.response.send_message(f"**Profil d'IA supprimé**\nLe profil d'IA `{profile_id}` a été supprimé avec succès.", ephemeral=True)
        
    @profiles_group.command(name="list")
    async def profiles_list(self, interaction: discord.Interaction):
        """Lister les profils d'IA disponibles sur ce serveur"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en dehors des serveurs.", ephemeral=True)
        
        profiles = self._get_profiles(guild)
        if profiles is None:
            return await interaction.response.send_message("Aucun profil d'IA n'est disponible sur ce serveur.", ephemeral=True)
        
        stats = self._get_profiles_stats(guild)
        await GPTProfilesMenu(self, interaction, profiles, stats).start()
        
    @profiles_group.command(name="load")
    @app_commands.rename(profile_id="ID du profil")
    @app_commands.choices(resume=[app_commands.Choice(name="Oui", value=1), app_commands.Choice(name="Non", value=0)])
    async def profiles_load(self, interaction: discord.Interaction, profile_id: str, resume: bool = True):
        """Charger un profil d'IA pour ce serveur
        
        :param resume: Reprendre la conversation précédente (activé par défaut)
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en dehors des serveurs.", ephemeral=True)
        
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel | discord.Thread):
            return await interaction.response.send_message("Cette commande n'est pas disponible dans les messages privés.", ephemeral=True)
        
        profile = self._get_profile(guild, profile_id)
        if profile is None:
            return await interaction.response.send_message(f"Le profil d'IA `{profile_id}` n'existe pas.", ephemeral=True)
        
        if resume:
            await interaction.response.send_message(f"**Profil d'IA chargé**\nLe profil d'IA `{profile_id}` a été chargé avec succès.\n\n*Vous pouvez reprendre la conversation précédente avec la commande `/ai resume`.*", ephemeral=True)
        else:
            await interaction.response.send_message(f"**Profil d'IA chargé**\nLe profil d'IA `{profile_id}` a été chargé avec succès.", ephemeral=True)
        
        session = GPTSession(self, channel, profile, bool(resume))
        self._set_gpt_session(channel, session)

        embed = discord.Embed(title=f"Session d'IA chargée · **{profile.name}** [`{profile.id}`]", description=f"*{profile.description}*", color=GPT_COLOR)
        embed.add_field(name="Prompt initial", value=f"```{profile.init_prompt}```", inline=False)
        embed.add_field(name="Température", value=f"**{profile.temperature}**")
        
        if profile.avatar_url:
            embed.set_thumbnail(url=profile.avatar_url)
        
        if profile.author_id:
            creator_name = await self.bot.fetch_user(profile.author_id)
        else:
            creator_name = "Inconnu"
        embed.set_footer(text=f"Créé par {creator_name}", icon_url=GPT_LOGO)
        await interaction.response.send_message(embed=embed)
           
    # Crédits ----------------------------------------------------------
        
    credits_group = app_commands.Group(name='aicreds', description="Gestion et statistiques sur les crédits d'IA", guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

    @credits_group.command(name='set')
    @app_commands.rename(amount="Quantité")
    async def guild_credits_set(self, interaction: discord.Interaction, amount: app_commands.Range[int, 0]):
        """Modifier le nombre de crédits que possède un serveur pour utiliser les fonctionnalités d'IA

        :param amount: Quantité de crédits virtuels à définir
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en dehors des serveurs.", ephemeral=True)
        
        user = interaction.user
        if not user.id == int(self.bot.config['OWNER']): # type: ignore
            return await interaction.response.send_message("Seul le propriétaire du bot peut modifier les crédits d'un serveur.", ephemeral=True)
        
        self._set_credits(guild, amount)
        await interaction.response.send_message(f"**Crédits mis à jour**\nLe serveur possède désormais `{amount}` crédits.", ephemeral=True)
        
    @credits_group.command(name='check')
    async def guild_credits_get(self, interaction: discord.Interaction):
        """Afficher le nombre de crédits que possède un serveur pour utiliser les fonctionnalités d'IA"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en dehors des serveurs.", ephemeral=True)
        
        credits = self._get_credits(guild)
        to_real = self._credits_to_real(credits)
        await interaction.response.send_message(f"**Crédits disponibles**\nLe serveur possède `{pretty.humanize_number(credits)}` crédits, équivalent à `{to_real}`$ pour l'API OpenAI.", ephemeral=True)
                    
    # Commandes manuelles ----------------------------------------------------------
    
    general_group = app_commands.Group(name='ai', description="Fonctionnalités liées à l'IA", guild_only=True)
    
    @general_group.command(name='customgpt')
    @app_commands.rename(initial_prompt="Initialisation", temperature="Température")
    async def customize_gpt_session(self, interaction: discord.Interaction, initial_prompt: str, temperature: app_commands.Range[float, 0, 2] = 0.8):
        """Reinitialiser la session du salon actuel avec un nouveau prompt d'initialisation 

        :param initial_prompt: Prompt d'initialisation à utiliser
        :param temperature: Température à utiliser
        """

    
    @profiles_delete.autocomplete('profile_id')
    @profiles_load.autocomplete('profile_id')
    async def profiles_ids_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice]:
        guild = interaction.guild
        profiles = self._get_profiles(guild) # type: ignore
        if profiles is None:
            return []
        f = fuzzy.finder(current, [(p.id, p.name) for p in profiles], key=lambda x: x[0])
        return [app_commands.Choice(name=p[1], value=p[0]) for p in f]
    
class CreateProfileModal(discord.ui.Modal, title="Créer/Modifier un profil d'IA"):
    profile_id = discord.ui.TextInput(label="ID", style=discord.TextStyle.short, required=True, placeholder="Identifiant unique du profil d'IA", min_length=4, max_length=32)
    name = discord.ui.TextInput(label="Nom", style=discord.TextStyle.short, required=True, placeholder="Nom du profil utilisé comme pseudonyme", min_length=4, max_length=28)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.long, required=True, placeholder="Courte description du profil d'IA", min_length=10, max_length=100)
    avatar_url = discord.ui.TextInput(label="Avatar", style=discord.TextStyle.short, required=False, placeholder="URL de l'avatar du profil (Optionel)", min_length=1, max_length=200)
    init_prompt = discord.ui.TextInput(label="Phrase d'initialisation", style=discord.TextStyle.long, required=True, placeholder="Prompt de configuration initiale du profil d'IA", min_length=10, max_length=1000)
    temperature = discord.ui.TextInput(label="Température", style=discord.TextStyle.short, required=False, placeholder="Degré de créativité de votre profil (entre 0.1 et 2.0)", min_length=2, max_length=3, default="0.8")
    context_size = discord.ui.TextInput(label="Taille du contexte", style=discord.TextStyle.short, required=False, placeholder="Nombre max. de messages à garder en mémoire", min_length=1, max_length=2, default="8")
    
    async def on_submit(self, interaction: Interaction):
        try:
            temp = float(self.temperature.value)
        except ValueError:
            await interaction.response.send_message("La température doit être un nombre décimal compris entre 0.1 et 2.0", ephemeral=True)
            return
        if temp < 0.1 or temp > 2.0:
            await interaction.response.send_message("La température doit être un nombre décimal compris entre 0.1 et 2.0", ephemeral=True)
            return
        
        try:
            ctx = int(self.context_size.value)
        except ValueError:
            await interaction.response.send_message("La taille du contexte doit être un nombre entier compris entre 2 et 10", ephemeral=True)
            return
        if ctx < 2 or ctx > 10:
            await interaction.response.send_message("La taille du contexte doit être un nombre entier compris entre 2 et 10", ephemeral=True)
            return
        
        self.data = {
            'id': self.profile_id.value,
            'name': self.name.value,
            'description': self.description.value,
            'avatar_url': self.avatar_url.value,
            'init_prompt': self.init_prompt.value,
            'temperature': temp,
            'context_size': int(self.context_size.value)
        }
    
    async def on_error(self, interaction: Interaction, error: Exception):
        await interaction.response.send_message(f"Une erreur inattendue est survenue lors de la création du profil : `{error}`", ephemeral=True)
        
class GPTProfilesMenu(discord.ui.View):
    def __init__(self, cog: 'Intellect', original_interaction: discord.Interaction, profiles: List[GPTProfile], stats: Optional[Dict[str, dict]] = None):
        super().__init__()
        self.cog = cog
        self.original_interaction = original_interaction
        self.profiles = profiles
        self.stats = stats or {}
        self.selected : GPTProfile | None = None
        
        self.pages = self._get_embeds()
        self.current_page = 0
        
    async def on_timeout(self):
        self.stop()
        
    async def interaction_check(self, interaction: Interaction):
        return interaction.user == self.original_interaction.user
        
    def _get_embeds(self) -> Dict[int, discord.Embed]:
        embeds = {}
        for profile in self.profiles:
            embed = discord.Embed(title=profile.name, description=profile.description, color=GPT_COLOR)
            embed.set_thumbnail(url=profile.avatar_url)
            embed.set_footer(text=f"Page {len(embeds)+1}/{len(self.profiles)}", icon_url=GPT_LOGO)
            embed.add_field(name="ID Unique", value=f"`{profile.id}`")
            if profile.author_id is not None:
                embed.add_field(name="Créateur", value=f"<@{profile.author_id}>")
            if profile.created_at is not None:
                embed.add_field(name="Créé le", value=datetime.fromtimestamp(profile.created_at).strftime("%d/%m/%Y à %H:%M"))
            if profile.id in self.stats:
                if self.stats[profile.id]['uses'] > 0:
                    embed.add_field(name="Utilisations", value=f"**{self.stats[profile.id]['uses']}**")
                if self.stats[profile.id]['messages'] > 0 and self.stats[profile.id]['tokens'] > 0:
                    consom = self.stats[profile.id]['tokens'] / self.stats[profile.id]['messages']
                    in_real = self.cog._tokens_to_real(consom)
                    in_credits = self.cog._real_to_credits(in_real)
                    embed.add_field(name="Consommation (moyenne)", value=f"**{consom:.2f}** tokens par message\n**~{in_real:.2f}**$ par message\n= **{in_credits:.2f}** crédits par message")
            embed.add_field(name="Température", value=f"**{profile.temperature}**")
            embed.add_field(name="Taille du contexte", value=f"**{profile.context_size}** messages")
            embed.add_field(name="Prompt d'initialisation", value=f"```{profile.init_prompt}```", inline=False)
            embeds[len(embeds)] = embed
        return embeds
    
    async def start(self):
        await self.original_interaction.response.send_message(embed=self.pages[self.current_page], view=self)
        
    @discord.ui.button(label="Précédent", style=discord.ButtonStyle.blurple)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page == 0:
            self.current_page = len(self.pages)-1
        self.current_page -= 1
        await interaction.response.edit_message(embed=self.pages[self.current_page])

    @discord.ui.button(label="Suivant", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page == len(self.pages)-1:
            self.current_page = 0
        self.current_page += 1
        await interaction.response.edit_message(embed=self.pages[self.current_page])
        
    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.red)
    async def close_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
    
async def setup(bot):
    await bot.add_cog(Intellect(bot))