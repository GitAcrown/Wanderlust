import logging
from typing import Dict, List, Union, Literal

import discord
import json
import yaml
import io
from discord import app_commands
from discord.ext import commands

from common import dataio
from common.utils import fuzzy

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

class DataListMenu(discord.ui.View):
    def __init__(self, cog: 'DataManager', user: Union[discord.User, discord.Member], data_entries: Dict[str, List[dataio.UserDataEntry]], initial_interaction: discord.Interaction):
        super().__init__(timeout=60)
        self._cog = cog
        self.user = user
        self.data_entries = data_entries
        self.cogs_names = sorted(list(data_entries.keys()))
        self.current_page = 0
        self.max_page = len(data_entries) # Une page par cog
        
        self.initial_interaction = initial_interaction
        
    def get_embed(self):
        cog_name = self.cogs_names[self.current_page]
        em = discord.Embed(title=f"Données enregistrées pour **`{cog_name}`**", color=self.user.color)
        cog = self._cog.bot.get_cog(cog_name)
        if cog:
            em.set_footer(text=f"{cog.description} • Page {self.current_page+1}/{self.max_page}", icon_url=self.user.display_avatar.url)
        else:
            em.set_footer(text=f"Page {self.current_page+1}/{self.max_page}", icon_url=self.user.display_avatar.url)
        
        entries = self.data_entries[cog_name]
        
        em.description = "\n".join([f"{str(entry)}" for entry in entries])
        
        return em
    
    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.user.id
    
    async def on_timeout(self):
        await self.initial_interaction.edit_original_response(view=None)
        self.stop()
        
    async def start(self):
        await self.initial_interaction.followup.send(embed=self.get_embed(), view=self)
        
    @discord.ui.button(emoji='<:iconLeftArrow:1078124175631339580>', style=discord.ButtonStyle.blurple)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
        else:
            self.current_page = self.max_page - 1
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(emoji='<:iconRightArrow:1078124174352076850>', style=discord.ButtonStyle.blurple)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_page - 1:
            self.current_page += 1
        else:
            self.current_page = 0
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
        
    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Close"""
        await interaction.response.edit_message(view=None)
        await interaction.delete_original_response()
        self.stop()

class DataManager(commands.GroupCog, group_name='mydata', description="Gestion centralisée de vos données utilisateurs"):
    """Gestion centralisée de vos données utilisateurs"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
    
    @app_commands.command(name='list')
    async def _list_mydata(self, interaction: discord.Interaction):
        """Liste vos données enregistrées dans les différents modules (cogs) du bot"""
        user_id = interaction.user.id
        all_cogs = self.bot.cogs
        data_entries = dataio.get_user_data(user_id, list(all_cogs.values()))

        if not data_entries:
            return await interaction.response.send_message("**Aucune donnée locale enregistrée**\nVous n'avez aucune donnée enregistrée dans les modules du bot.", ephemeral=True)

        await interaction.response.defer()
        menu = DataListMenu(self, interaction.user, data_entries, interaction)
        await menu.start()

    @app_commands.command(name='wipe')
    async def _wipe_mydata(self, interaction: discord.Interaction, cog_name: str, table_name: str):
        """Efface vos données personnelles enregistrées dans un module (cog) du bot

        :param cog_name: Nom du module  du bot
        :param table_name: Nom de la table de données du module
        """
        user_id = interaction.user.id
        cog = self.bot.get_cog(cog_name)
        if not cog:
            return await interaction.response.send_message(f"**Module `{cog_name}` introuvable**\nLe module `{cog_name}` n'existe pas.", ephemeral=True)
        
        cogs_entries = dataio.get_user_data(user_id, [cog])
        if not cogs_entries:
            return await interaction.response.send_message(f"**Aucune donnée locale enregistrée**\nVous n'avez aucune donnée enregistrée dans le module `{cog_name}`.", ephemeral=True)

        if table_name not in [entry.table_name for entry in cogs_entries[cog_name]]:
            return await interaction.response.send_message(f"**Table `{table_name}` introuvable**\nLa table `{table_name}` n'existe pas dans le module `{cog_name}`.", ephemeral=True)
    
        # Pourquoi ça me fait une erreur de type ici alors que j'ai exclu l'absence de cog ? La c*n de ses morts
        r = dataio.wipe_user_data(user_id, cog, [table_name]) #type: ignore
        if r:
            await interaction.response.send_message(f"**Données effacées**\nLes données de la table `{table_name}` du module `{cog_name}` ont été effacées avec succès.", ephemeral=True)
        else:
            await interaction.response.send_message(f"**Erreur**\nUne erreur est survenue lors de l'effacement des données de la table `{table_name}` du module `{cog_name}`.", ephemeral=True)
        
    @app_commands.command(name='extract')
    async def _extract_mydata(self, interaction: discord.Interaction, cog_name: str, format: Literal['json', 'yaml'] = 'yaml'):
        """Extrait vos données personnelles (toutes tables confondues) enregistrées dans un module du bot au format JSON ou YAML

        :param cog_name: Nom du module du bot
        :param format: Format de sortie (JSON ou YAML), par défaut YAML
        :return: Fichier JSON/YAML contenant vos données
        """
        user_id = interaction.user.id
        cog = self.bot.get_cog(cog_name)
        if not cog:
            return await interaction.response.send_message(f"**Module `{cog_name}` introuvable**\nLe module `{cog_name}` n'existe pas.", ephemeral=True)
        
        cogs_entries = dataio.get_user_data(user_id, [cog])
        if not cogs_entries:
            return await interaction.response.send_message(f"**Aucune donnée locale enregistrée**\nVous n'avez aucune donnée enregistrée dans le module `{cog_name}`.", ephemeral=True)

        all_tables = [entry.table_name for entry in cogs_entries[cog_name]]
        data = dataio.extract_user_data(user_id, cog, all_tables)
        if not data:
            return await interaction.response.send_message(f"**Erreur**\nUne erreur est survenue lors de l'extraction des données du module `{cog_name}`.", ephemeral=True)

        # On envoie les données en tant que fichier JSON ou YAML
        if format == 'json':
            with io.BytesIO(json.dumps(data, indent=4).encode('utf-8')) as fp:
                text = f"**Données extraites**\nLes données du module `{cog_name}` ont été extraites avec succès (format JSON)."
                await interaction.response.send_message(content=text, file=discord.File(fp, filename=f"{cog_name}.json"), ephemeral=True)
        elif format == 'yaml':
            with io.BytesIO(yaml.dump(data, indent=4).encode('utf-8')) as fp:
                text = f"**Données extraites**\nLes données du module `{cog_name}` ont été extraites avec succès (format YAML)."
                await interaction.response.send_message(content=text, file=discord.File(fp, filename=f"{cog_name}.yaml"), ephemeral=True)
        
        
    @_extract_mydata.autocomplete('cog_name')
    @_wipe_mydata.autocomplete('cog_name')
    async def _cog_name_autocomplete(self, interaction: discord.Interaction, current: str):
        all_cogs = self.bot.cogs
        cogs_with_data = dataio.get_user_data(interaction.user.id, list(all_cogs.values()))
        cogs_with_data = list(cogs_with_data.keys())
        r = fuzzy.finder(current, cogs_with_data)
        return [app_commands.Choice(name=cog_name, value=cog_name) for cog_name in r]
    
    @_wipe_mydata.autocomplete('table_name')
    async def _table_name_autocomplete(self, interaction: discord.Interaction, current: str):
        current_cog = interaction.namespace['cog_name']
        cog = self.bot.get_cog(current_cog)
        if cog is None:
            return []
        cogs_tables = dataio.get_user_data(interaction.user.id, [cog])
        if not cogs_tables:
            return []
        local_tables = cogs_tables[current_cog]
        r = fuzzy.finder(current, [t.table_name for t in local_tables])
        return [app_commands.Choice(name=table_name.capitalize(), value=table_name) for table_name in r]
        
    @app_commands.command(name='info')
    async def _info_mydata(self, interaction: discord.Interaction):
        """Affiche des informations concernant vos données enregistrées"""
        user = interaction.user
        expl_txt = "Les données stockées dans les modules sont utilisées pour vous fournir des fonctionnalités personnalisées et/ou dans le cadre du fonctionnement de certaines commandes.\nCes données sont liées à votre identifiant Discord et ne sont accessibles directement que par le propriétaire du bot, sauf mention contraire."
        expl_em = discord.Embed(title="Informations importantes concernant vos données", description=expl_txt, color=user.color)
        expl_em.add_field(name="Effacer vos données", value=f"Afin d'effacer vos données, utilisez `/mydata wipe <module> <table>` :\n• **<module>** représente le module du bot utilisant vos données (ex. `colorful`)\n• **<table>** représente la table de données interne au module (ex. `users`)", inline=False)
        expl_em.add_field(name="Classement des données", value="Certaines données sont essentielles au fonctionnement du bot, veuillez prêter attention à cette notation :\n• `[0]` **Données insignifiantes** dont la suppression n'affectera pas votre expérience (données de cache, métadonnées secondaires etc.)\n• `[1]` **Données importantes** mais pas essentielles (ex. paramètres réversibles, préférences etc.)\n• `[2]` **Données critiques** avec des conséquences irréversibles (ex. données de progression, économiques etc.)", inline=False)
        expl_em.set_footer(text=f"Pour plus d'informations, contactez le propriétaire du bot.")
        await interaction.response.send_message(embed=expl_em)

async def setup(bot):
    await bot.add_cog(DataManager(bot))