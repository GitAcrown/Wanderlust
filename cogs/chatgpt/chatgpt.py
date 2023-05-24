import logging
import openai_async as openai

import unidecode
import time
import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional, List, Literal, Dict

from common import dataio
from common.utils import fuzzy

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

COG_VERSION = '2.2.0'

DEFAULT_SYSTEM_PROMPT = """Tu es un assistant appelé Wanderlust, qui répond aux demandes d'un groupe d'utilisateurs.
N'hésite pas à t'exprimer de manière familière, comme tu le ferais avec tes amis, mais sans les saluer en début de réponse. 
Tu peux utiliser des abréviations, des raccourcis, le formattage markdown etc. Tu peux aussi utiliser des mots vulgaires et t'es libre d'être créatif et inventif. 
Essaye d'être concis et de ne pas dépasser 2000 caractères par réponse."""

class Prompt:
    def __init__(self, cog: 'ChatGPT', role: Literal['system', 'assistant', 'user'], content: str, username: Optional[str] = None) -> None:
        self._cog = cog
        self.role = role
        self.content = content
        self.username = username
        
    def __repr__(self) -> str:
        return f"<Prompt role={self.role} content={self.content}>"
        
    def to_dict(self):
        if self.role == 'user' and self.username:
            username = unidecode.unidecode(self.username)
            username = ''.join([c for c in username if c.isalnum()]).rstrip()
            return {'role': self.role, 'content': self.content, 'name': username}
        return {'role': self.role, 'content': self.content}

class UserPrompt(Prompt):
    def __init__(self, cog: 'ChatGPT', content: str, username: Optional[str] = None, context: Optional[discord.Message] = None) -> None:
        super().__init__(cog, 'user', content, username)
        self.context = context
    
    def to_dicts(self):
        dicts = []
        if self.context:
            if self.context.author == self._cog.bot.user:
                dicts.append({'role': 'assistant', 'content': self.context.content})
            else:
                dicts.append({'role': 'user', 'content': self.context.content, 'name': self.context.author.display_name})
        dicts.append(self.to_dict())
        return dicts
        
class ChatGPT(commands.Cog):
    """Chatter avec GPT-3"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        self.api_key = self.bot.config['OPENAI_APIKEY']
        self.sessions : dict = {}
        
    @commands.Cog.listener()
    async def on_ready(self):
        self._init_guild_db()
    
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._init_guild_db(guild)
        
    def _init_guild_db(self, guild: Optional[discord.Guild] = None):
        guilds = [guild] if guild else self.bot.guilds
        for g in guilds:
            query = """CREATE TABLE IF NOT EXISTS presets (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                system_prompt TEXT,
                temperature REAL,
                author_id INTEGER,
                guild_id INTEGER,
                authorized_users TEXT,
                cog_version TEXT
                )"""
            self.data.execute(g, query)
            
    def cog_unload(self):
        self.data.close_all_databases()
    
    # Gestion des sessions -----------------------------------------------------
    
    def get_session(self, channel_id: int, system_prompt: str = DEFAULT_SYSTEM_PROMPT, temperature: float = 1.0):
        if channel_id not in self.sessions:
            self.sessions[channel_id] = {
                'prompts': [Prompt(self, 'system', system_prompt)],
                'temperature': temperature
            }
        return self.sessions[channel_id]
    
    def delete_session(self, channel_id: int):
        if channel_id in self.sessions:
            del self.sessions[channel_id]
    
    def add_prompt(self, channel_id: int, prompt: UserPrompt):
        if channel_id not in self.sessions:
            self.get_session(channel_id)
        self.sessions[channel_id]['prompts'].append(prompt)
        
        # On retire les prompts après les 8 derniers sauf le tout premier (system prompt)
        if len(self.sessions[channel_id]['prompts']) > 8:
            self.sessions[channel_id]['prompts'] = [self.sessions[channel_id]['prompts'][0]] + self.sessions[channel_id]['prompts'][-7:]
    
    async def get_response(self, channel_id: int):
        session = self.get_session(channel_id)
        temp = session['temperature']
        messages = []
        
        def exclude_duplicates(d: dict):
            if d not in messages:
                messages.append(d)
        
        for prompt in session['prompts']:
            if isinstance(prompt, UserPrompt):
                for d in prompt.to_dicts():
                    exclude_duplicates(d)
            else:
                exclude_duplicates(prompt.to_dict())
                
        try:
            response = await openai.chat_complete(
                    self.api_key,
                    timeout=60,
                    payload={
                        'model':'gpt-3.5-turbo',
                        'temperature': temp,
                        'max_tokens': 500,
                        'messages': messages,
                        'user': str(channel_id)
                    }
                )
        except Exception as e:
            logger.exception(e)
            return f"Erreur lors de la requête à l'API OpenAI : `Délai de réponse dépassé`\nCe problème est courant lorsque plusieurs requêtes sont effectuées en même temps. Veuillez réessayer dans quelques secondes."
    
        if not response.json():
            return "Erreur lors de la requête à l'API OpenAI : `Réponse vide`"
        
        if 'choices' not in response.json():
            logger.error(response.json())
            return "Erreur lors de la requête à l'API OpenAI : `Réponse des serveurs inattendue`"

        respdict = response.json()['choices'][0]['message']
        text = respdict['content']
        session['prompts'].append(Prompt(self, 'assistant', text))
        return text
    
    # Gestion des presets -----------------------------------------------------
    
    def get_preset(self, guild: discord.Guild, preset_id: str) -> Optional[dict]:
        query = "SELECT * FROM presets WHERE id=?"
        result = self.data.fetchone(guild, query, (preset_id,))
        return dict(result) if result else None

    
    def get_all_presets(self, guild: discord.Guild) -> List[dict]:
        query = "SELECT * FROM presets"
        result = self.data.fetchall(guild, query)
        return [dict(preset) for preset in result]
    
    def set_preset(self, 
                      guild: discord.Guild, 
                      preset_id: str, 
                      name: str, 
                      description: str,
                      system_prompt: str, 
                      temperature: float, 
                      author_id: int, 
                      authorized_users: Optional[str] = None):
        if temperature < 0.1 or temperature > 2.0:
            raise ValueError("La température doit être comprise entre 0.1 et 2.0")
        
        if not authorized_users:
            authorized_users = 'all'
        if authorized_users not in ['all', 'premium', 'owner']:
            raise ValueError("Le paramètre `authorized_users` doit être 'all', 'premium' ou 'owner'")

        query = "INSERT OR REPLACE INTO presets VALUES (?,?,?,?,?,?,?,?,?)"
        self.data.execute(guild, query, (preset_id, name, description, system_prompt, temperature, author_id, guild.id, authorized_users, COG_VERSION))
        
    def delete_preset(self, guild: discord.Guild, preset_id: str):
        query = "DELETE FROM presets WHERE id=?"
        self.data.execute(guild, query, (preset_id,))
        
 
    # COMMANDES =======================================================
    
    preset_group = app_commands.Group(name='preset', description="Commandes de gestion des presets")
    
    @preset_group.command(name='list')
    @app_commands.guild_only()
    async def list_presets(self, interaction: discord.Interaction):
        """Liste les presets de configuration disponibles sur ce serveur"""
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Cette commande n'est pas disponible en MP", ephemeral=True)
            return
        presets = self.get_all_presets(guild)
        if not presets:
            await interaction.response.send_message(f"**Aucun preset disponible**\nAucun preset n'a été ajouté sur ce serveur.", ephemeral=True)
            return
        
        embed = discord.Embed(title="Liste des presets disponibles", color=0x2F3136)
        txt = []
        for preset in presets:
            txt.append(f"**{preset['name']} `{preset['id']}` ·** *{preset['description']}*")
        embed.description = '\n'.join(txt)
        await interaction.response.send_message(embed=embed)
        
    @preset_group.command(name='add')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.choices(authorized_users=[
        app_commands.Choice(name="Tous", value="all"),
        app_commands.Choice(name="Premium (et modérateurs)", value="premium"),
        app_commands.Choice(name="Propriétaire du bot seulement", value="owner")
    ])
    async def add_preset(self, interaction: discord.Interaction, preset_id: str, name: str, description: str, system_prompt: str, temperature: float, authorized_users: Optional[str] = None):
        """Ajoute un preset de configuration d'IA sur ce serveur

        :param preset_id: Identifiant unique du preset (sans espace ni caractères spéciaux)
        :param name: Nom du preset (32 caractères max)
        :param description: Courte description du preset (100 caractères max)
        :param system_prompt: Prompt de configuration initiale (500 caractères max)
        :param temperature: Température de l'IA (entre 0.1 et 2.0) représentant le degré de créativité
        :param authorized_users: Autorisation d'utiliser ce preset (par défaut : tous)
        """
        preset_id = unidecode.unidecode(preset_id)
        preset_id = ''.join([c for c in preset_id if c.isalnum() or c in ['-', '_']])
        preset_id = preset_id.lower().strip()
        if len(preset_id) > 32:
            await interaction.response.send_message(f"**Identifiant trop long**\nL'identifiant du preset ne peut pas dépasser 32 caractères.", ephemeral=True)
            return
        if len(name) > 32:
            await interaction.response.send_message(f"**Nom trop long**\nLe nom du preset ne peut pas dépasser 32 caractères.", ephemeral=True)
            return
        if len(description) > 100:
            await interaction.response.send_message(f"**Description trop longue**\nLa description du preset ne peut pas dépasser 100 caractères.", ephemeral=True)
            return
        
        if len(system_prompt) > 500:
            await interaction.response.send_message(f"**Prompt trop long**\nLe prompt de configuration initial ne peut pas dépasser 500 caractères.", ephemeral=True)
            return
        if temperature < 0.1 or temperature > 2.0:
            await interaction.response.send_message(f"**Température invalide**\nLa température doit être comprise entre 0.1 et 2.0.", ephemeral=True)
            return
        
        if not authorized_users:
            authorized_users = 'all'
        if authorized_users not in ['all', 'premium', 'owner']:
            await interaction.response.send_message(f"**Utilisateurs autorisés invalides**\nLe paramètre `authorized_users` doit être 'all', 'premium' ou 'owner'.", ephemeral=True)
            return

        author = interaction.user
        if not isinstance(author, discord.Member):
            await interaction.response.send_message(f"**Commande inaccessible**\nVous devez utiliser cette commande sur un serveur.", ephemeral=True)
            return

        if not author.guild_permissions.manage_messages:
            await interaction.response.send_message(f"**Commande inaccessible**\nVous devez avoir la permission `Gérer les messages` pour utiliser cette commande.", ephemeral=True)
            return
        
        self.set_preset(interaction.guild, preset_id, name, description, system_prompt, temperature, authorized_users) #type: ignore
        await interaction.response.send_message(f"**Preset ajouté**\nLe preset `{preset_id}` a été ajouté sur ce serveur.\nFaîtes </preset list:1105184790472314970> pour voir les presets disponibles.", ephemeral=True)
        
    @preset_group.command(name='remove')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def remove_preset(self, interaction: discord.Interaction, preset_id: str):
        """Supprime un preset de configuration d'IA sur ce serveur

        :param preset_id: Identifiant unique du preset
        """
        author = interaction.user
        if not isinstance(author, discord.Member):
            await interaction.response.send_message(f"**Commande inaccessible**\nVous devez utiliser cette commande sur un serveur.", ephemeral=True)
            return

        if not author.guild_permissions.manage_messages:
            await interaction.response.send_message(f"**Commande inaccessible**\nVous devez avoir la permission `Gérer les messages` pour utiliser cette commande.", ephemeral=True)
            return
        
        if not self.get_preset(interaction.guild, preset_id): #type: ignore
            await interaction.response.send_message(f"**Preset introuvable**\nLe preset `{preset_id}` n'existe pas sur ce serveur.", ephemeral=True)
            return
        
        self.delete_preset(interaction.guild, preset_id) #type: ignore
        await interaction.response.send_message(f"**Preset supprimé**\nLe preset `{preset_id}` a été supprimé sur ce serveur.", ephemeral=True)
        
    @preset_group.command(name='use')
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 60)
    async def use_preset(self, interaction: discord.Interaction, preset_id: str):
        """Lancer une session d'IA avec un preset de configuration sur ce serveur"""
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(f"**Commande inaccessible**\nVous devez utiliser cette commande sur un serveur.", ephemeral=True)
            return
        author = interaction.user
        is_premium = author.premium_since is not None #type: ignore
        
        if author.guild_permissions.manage_messages:
            is_premium = True
            
        presets = self.get_all_presets(guild) #type: ignore
        if not presets:
            await interaction.response.send_message(f"**Aucun preset disponible**\nIl n'y a aucun preset disponible sur ce serveur.", ephemeral=True)
            return
    
        preset = self.get_preset(guild, preset_id) #type: ignore
        if not preset:
            await interaction.followup.send(f"**Preset introuvable**\nLe preset `{preset_id}` n'existe pas sur ce serveur.", ephemeral=True)
            return

        if preset['authorized_users'] == 'premium' and not is_premium:
            await interaction.followup.send(f"**Preset réservée aux membres premium et aux modérateurs**\nSeul les membres premium (boostant le serveur) peuvent démarrer une session avec ce preset.", ephemeral=True)
            return
        elif preset['authorized_users'] == 'owner' and not self.bot.owner_id == author.id:
            await interaction.followup.send(f"**Preset réservée au propriétaire du bot**\nSeul le propriétaire du bot peut démarrer une session avec ce preset.", ephemeral=True)
            return

        prompt = preset['system_prompt']
        temp = preset['temperature']
        channel_id = interaction.channel_id
        if not isinstance(channel_id, int):
            await interaction.response.send_message(f"**Commande réservée aux channels textuels**\nCette commande ne peut être utilisée que dans un channel textuel.", ephemeral=True)
            return
        self.delete_session(channel_id)
        self.get_session(channel_id, prompt, temp)
        await interaction.response.send_message(f"**Session customisée démarrée**\nLa session a été réinitialisée et le prompt de configuration initial a été modifié avec les instructions suivantes, tirées du preset `{preset['name']}` :\n```{prompt}```Et avec une **température** de {temp}")
    
    @use_preset.autocomplete('preset_id')
    @remove_preset.autocomplete('preset_id')
    async def autocomplete_preset_id(self, interaction: discord.Interaction, current: str):
        presets = self.get_all_presets(interaction.guild)
        if not presets:
            return []
        r = fuzzy.finder(current, [(preset['id'], preset['name']) for preset in presets], key=lambda x: x[1])
        return [app_commands.Choice(name=choice[1], value=choice[0]) for choice in r]
    
    # COMMANDES BRUTES ==================================================================================================
    
    chat_group = app_commands.Group(name='chat', description="Commandes de chat avec l'IA")
    
    @chat_group.command(name='custom')
    async def customize_sysprompt(self, interaction: discord.Interaction, system_prompt: str, temperature: float = 1.0):
        """Réinitialise la session en cours sur ce channel et modifie le prompt de configuration initiale
    
        :param system_prompt: Prompt de configuration initiale
        :param temperature: Température de créativité de l'IA (entre 0.1 et 2.0)"""
        author = interaction.user
        if not isinstance(author, discord.Member):
            is_premium = True
        elif author.guild_permissions.manage_messages:
            is_premium = True
        else:
            is_premium = author.premium_since is not None
            
        if len(system_prompt) > 500:
            await interaction.response.send_message(f"**Prompt trop long**\nLe prompt de configuration initial ne peut pas dépasser 500 caractères.", ephemeral=True)
            return
        
        if temperature < 0.1 or temperature > 2.0:
            await interaction.response.send_message(f"**Température invalide**\nLa température doit être comprise entre 0.1 et 2.0.", ephemeral=True)
            return
            
        if not is_premium:
            await interaction.response.send_message(f"**Commande réservée aux membres premium (ou aux modérateurs)**\nSeul les membres premium (boostant le serveur) et modérateurs peuvent démarrer une session customisée, mais tout le monde peut l'utiliser ensuite.", ephemeral=True)
            return

        channel_id = interaction.channel_id
        if not isinstance(channel_id, int):
            await interaction.response.send_message(f"**Commande réservée aux channels textuels**\nCette commande ne peut être utilisée que dans un channel textuel.", ephemeral=True)
            return
        self.delete_session(channel_id)
        self.get_session(channel_id, system_prompt, temperature)
        await interaction.response.send_message(f"**Session customisée démarrée**\nLa session a été réinitialisée et le prompt de configuration initial a été modifié avec les instructions suivantes :\n```{system_prompt}```Et avec une **température** de {temperature}")

    @chat_group.command(name='current')
    async def check_current_config(self, interaction: discord.Interaction):
        """Affiche le prompt de configuration actuellement utilisé sur ce channel"""
        channel_id = interaction.channel_id
        if not isinstance(channel_id, int):
            await interaction.response.send_message(f"**Commande réservée aux channels textuels**\nCette commande ne peut être utilisée que dans un channel textuel.", ephemeral=True)
            return
        session = self.get_session(channel_id)
        if not session:
            await interaction.response.send_message(f"**Aucune session en cours**\nIl n'y a aucune session en cours sur ce channel.", ephemeral=True)
            return
        first_prompt = session['prompts'][0]
        await interaction.response.send_message(f"**Prompt de configuration actuel**\nVoici le prompt de configuration actuellement utilisé sur ce channel :\n```{first_prompt.content}```Et avec une **température** de {session['temperature']}")

    @chat_group.command(name='reset')
    @app_commands.checks.cooldown(1, 300)
    async def reset_channel_session(self, interaction: discord.Interaction):
        """Réinitialise la session en cours sur ce channel en utilisant le prompt de configuration par défaut"""
        channel_id = interaction.channel_id
        if not isinstance(channel_id, int):
            await interaction.response.send_message(f"**Commande réservée aux channels textuels**\nCette commande ne peut être utilisée que dans un channel textuel.", ephemeral=True)
            return
        self.delete_session(channel_id)
        await interaction.response.send_message(f"**Session réinitialisée**\nLa session a été réinitialisée avec le prompt de configuration initial par défaut.")

    @chat_group.command(name='private')
    @app_commands.checks.cooldown(1, 20)
    async def send_prompt(self, interaction: discord.Interaction, content: str):
        """Parler avec GPT-3 (Modèle GPT-3.5 Turbo) en privé sur la session en cours"""
        channel_id = interaction.channel_id
        if not isinstance(channel_id, int):
            await interaction.response.send_message(f"**Commande réservée aux channels textuels**\nCette commande ne peut être utilisée que dans un channel textuel.", ephemeral=True)
            return
        
        self.add_prompt(channel_id, UserPrompt(self, content, interaction.user.display_name))
        await interaction.response.defer(ephemeral=True)
        response = await self.get_response(channel_id)
        await interaction.followup.send(response, ephemeral=True)
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.bot.user:
                return
        if message.author.bot:
            return  
    
        if message.reference and message.reference.resolved: # Si le message est une réponse à un autre message
            reply = message.reference.resolved
            if isinstance(reply, discord.DeletedReferencedMessage):
                return
            
            if reply.author.id != self.bot.user.id:
                if not self.bot.user.mentioned_in(message):
                    return
            
            context = None
            if self.bot.user.mentioned_in(message) or reply.author.id == self.bot.user.id:
                context = reply
            
            prompt = UserPrompt(self, message.content, message.author.display_name, context)
            self.add_prompt(message.channel.id, prompt)
            async with message.channel.typing():
                response = await self.get_response(message.channel.id)
            await message.reply(response)
            
        elif self.bot.user.mentioned_in(message):
            content = message.content.replace(f'<@{self.bot.user.id}>', '')
            prompt = UserPrompt(self, content, message.author.display_name)
            self.add_prompt(message.channel.id, prompt)
            async with message.channel.typing():
                response = await self.get_response(message.channel.id)
            await message.reply(response)

async def setup(bot):
    await bot.add_cog(ChatGPT(bot))