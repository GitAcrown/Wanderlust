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

DEFAULT_GUILD_SETTINGS = {
    'bday_role_id': 0,
    'bday_channel_id': 0
}

class Birthdays(commands.GroupCog, group_name='bday', description="Inventaire des anniversaires et rôle automatique"):
    """Inventaire des anniversaires et rôle automatique"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
        self.last_check = None
        
    @commands.Cog.listener()
    async def on_ready(self):
        self._init_guilds_db()
        self._init_users_db()
        self.check_birthdays.start()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._init_guilds_db(guild)
        
    def cog_unload(self):
        self.data.close_all_databases()
        
    def _init_guilds_db(self, guild: Optional[discord.Guild] = None):
        guilds = [guild] if guild else self.bot.guilds
        for g in guilds:
            self.data.execute(g, """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
            self.data.executemany(g, """INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)""", DEFAULT_GUILD_SETTINGS.items())
            
    def _init_users_db(self):
        self.data.execute('users', """CREATE TABLE IF NOT EXISTS birthdays (user_id INTEGER PRIMARY KEY, date TEXT)""")
    
    @tasks.loop(minutes=1)
    async def check_birthdays(self):
        if self.last_check and self.last_check.day == datetime.today().day:
            return
        
        logger.info("Checking birthdays...")
        self.last_check = datetime.today()
        for guild in self.bot.guilds:
            birthdays = self.get_guild_birthdays_today(guild)
            if birthdays:
                settings = self.get_guild_settings(guild)
                if settings['bday_role_id']:
                    role = guild.get_role(int(settings['bday_role_id']))
                    if not role:
                        return
                    
                    # Retirer le rôle aux membres qui ne sont plus dans la liste
                    for member in role.members:
                        if member not in birthdays:
                            await member.remove_roles(role)
                    
                    # Ajouter le rôle aux membres qui ne l'ont pas encore
                    for member in birthdays:
                        if member not in role.members:
                            await member.add_roles(role)
            
                if settings['bday_channel_id']:
                    channel = guild.get_channel(int(settings['bday_channel_id']))
                    if not channel or not isinstance(channel, discord.TextChannel):
                        return
                    
                    today = datetime.today()
                    astro = self.get_zodiac_sign(today)
                    astro = f" · {astro[1]}" if astro else ''
                    # Envoyer un message dans le channel
                    
                    rdm = random.choice(("Aujourd'hui c'est l'anniversaire de", "Nous fêtons aujourd'hui l'anniversaire de", "C'est l'ANNIVERSAIRE de", "Bon anniversaire à", "Joyeux anniversaire à"))
                    if len(birthdays) == 1:
                        msg = f"### {rdm} {birthdays[0].mention} !"
                    else:
                        msg = f"### {rdm} {', '.join([m.mention for m in birthdays[:-1]])} et {birthdays[-1].mention} !"
                    
                    msg += f"\n{today.strftime('%d/%m')}{astro}"
                    
                    await channel.send(msg)
                    
    @check_birthdays.before_loop
    async def before_check_birthdays(self):
        await self.bot.wait_until_ready()
        logger.info("Birthdays loop lancée")
                                    
    # Userdata
        
    def dataio_list_user_data(self, user_id: int) -> List[dataio.UserDataEntry]:
        data = []
        userdata = self.data.fetchone('users', """SELECT * FROM birthdays WHERE user_id = ?""", (user_id,))
        if userdata:
            data.append(dataio.UserDataEntry(user_id, 'birthdays', "Date d'anniversaire", importance_level=1))
        return data
    
    def dataio_wipe_user_data(self, user_id: int, table_name: str) -> bool:
        if table_name == 'birthdays':
            self.data.execute('users', """DELETE FROM birthdays WHERE user_id = ?""", (user_id,))
            return True
        return False
    
    def dataio_extract_user_data(self, user_id: int, table_name: str) -> Optional[dict]:
        if table_name == 'birthdays':
            userdata = self.data.fetchone('users', """SELECT * FROM birthdays WHERE user_id = ?""", (user_id,))
            if userdata:
                return {'date': userdata['date']}
        return None
    
    # Settings
    
    def get_guild_settings(self, guild: discord.Guild) -> Dict[str, Union[str, int]]:
        r = self.data.fetchall(guild, """SELECT * FROM settings""")
        return {row['key']: json.loads(row['value']) for row in r}
    
    def set_guild_setting(self, guild: discord.Guild, key: str, value: Union[str, int]):
        self.data.execute(guild, """INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)""", (key, json.dumps(value)))
    
    # Users
    
    def get_user_birthday(self, user: Union[discord.Member, discord.User]) -> Optional[datetime]:
        r = self.data.fetchone('users', """SELECT * FROM birthdays WHERE user_id = ?""", (user.id,))
        if r:
            return datetime.strptime(r['date'], '%d/%m')
        return None
    
    def get_guild_birthdays(self, guild: discord.Guild):
        r = self.data.fetchall('users', """SELECT * FROM birthdays""")
        members = guild.members
        return {m: datetime.strptime(b['date'], '%d/%m') for b in r if (m := discord.utils.get(members, id=b['user_id']))}
    
    def get_guild_birthdays_today(self, guild: discord.Guild) -> List[discord.Member]:
        return  [m for m, d in self.get_guild_birthdays(guild).items() if d.month == datetime.today().month and d.day == datetime.today().day]
    
    def set_user_birthday(self, user: Union[discord.Member, discord.User], date: datetime):
        self.data.execute('users', """INSERT OR REPLACE INTO birthdays (user_id, date) VALUES (?, ?)""", (user.id, date.strftime('%d/%m')))
        
    def remove_user_birthday(self, user: Union[discord.Member, discord.User]):
        self.data.execute('users', """DELETE FROM birthdays WHERE user_id = ?""", (user.id,))
        
    def is_valid_date(self, date: str) -> bool:
        try:
            datetime.strptime(date, '%d/%m')
            return True
        except ValueError:
            return False
        
    def get_zodiac_sign(self, date: datetime) -> Optional[tuple[str, str]]:
        zodiacs = [(120, 'Capricorne', '♑'), (218, 'Verseau', '♒'), (320, 'Poisson', '♓'), (420, 'Bélier', '♈'), (521, 'Taureau', '♉'),
           (621, 'Gémeaux', '♊'), (722, 'Cancer', '♋'), (823, 'Lion', '♌'), (923, 'Vierge', '♍'), (1023, 'Balance', '♎'),
           (1122, 'Scorpion', '♏'), (1222, 'Sagittaire', '♐'), (1231, 'Capricorne', '♑')]
        date_number = int("".join((str(date.month), '%02d' % date.day)))
        for z in zodiacs:
            if date_number <= z[0]:
                return z[1], z[2]
    
    # Commands
    
    @app_commands.command(name="set")
    async def _set_bday(self, interaction: discord.Interaction, date: Optional[str] = None):
        """Définir sa date d'anniversaire

        :param date: Date au format JJ/MM, si vide supprime la date d'anniversaire
        """
        if not date:
            self.remove_user_birthday(interaction.user)
            await interaction.response.send_message("**Données supprimées**\nVotre date d'anniversaire a été supprimée.", ephemeral=True)
            return

        if not self.is_valid_date(date):
            await interaction.response.send_message("**Erreur de format**\nLa date doit être au format JJ/MM.", ephemeral=True)
            return
        
        dt = datetime.strptime(date, '%d/%m')
        self.set_user_birthday(interaction.user, dt)
        await interaction.response.send_message(f"**Date d'anniversaire définie**\nVotre date d'anniversaire a été définie au `{dt.strftime('%d/%m')}`.", ephemeral=True)

    @app_commands.command(name="get")
    async def _get_bday(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """Afficher la date d'anniversaire d'un utilisateur
        
        :param user: Membre dont on veut afficher la date d'anniversaire, si vide affiche la date de l'utilisateur
        """
        u = user or interaction.user
        dt = self.get_user_birthday(u)
        if not dt:
            await interaction.response.send_message(f"**Aucune date d'anniversaire définie**\nL'utilisateur visé n'a pas défini de date d'anniversaire.", ephemeral=True)
            return
        
        today = datetime.now()
        dt = dt.replace(year=datetime.now().year)

        msg = f"**Anniversaire ·** {dt.strftime('%d/%m')}\n"

        if today >= dt:
            next_date = dt.replace(year=today.year + 1)
        else:
            next_date = dt
        msg += f"**Prochain ·** <t:{int(next_date.timestamp())}:D>\n"
    
        astro = self.get_zodiac_sign(dt)
        if astro:
            msg += f"**Signe Astrologique ·** {' '.join(astro)}"
    
        em = discord.Embed(title=f"Anniversaire de **{u.display_name}**", description=msg, color=0x2F3136)
        em.set_thumbnail(url=u.display_avatar.url)
        await interaction.response.send_message(embed=em, ephemeral=True)
    
    @app_commands.command(name="list")
    @app_commands.guild_only()
    async def _list_bday(self, interaction: discord.Interaction):
        """Afficher la liste des 10 prochains anniversaires du serveur"""
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("**Erreur**\nCette commande ne peut être utilisée que sur un serveur.", ephemeral=True)
            return
        bdays = self.get_guild_birthdays(guild)
        if not bdays:
            await interaction.response.send_message("**Aucune date d'anniversaire définie**\nAucun membre du serveur n'a défini de date d'anniversaire.", ephemeral=True)
            return

        today = datetime.now()
        bdays = {k: v.replace(year=today.year) for k, v in bdays.items()}
        # Garder que les futurs anniversaires
        bdays = {k: v for k, v in sorted(bdays.items(), key=lambda item: item[1]) if k in guild.members and v >= today}
        bdays = list(bdays.items())[:10]
        msg = "\n".join([f"{u.mention} · <t:{int(dt.timestamp())}:D>" for u, dt in bdays])
        em = discord.Embed(title=f"Prochains anniversaires du serveur", description=msg, color=0x2F3136)
        await interaction.response.send_message(embed=em)
        
    @app_commands.command(name="setuser")
    @app_commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def _set_user_bday(self, interaction: discord.Interaction, user: discord.Member, date: Optional[str] = None):
        """Définir la date d'anniversaire d'un utilisateur

        :param user: Membre dont on veut définir la date d'anniversaire
        :param date: Date au format JJ/MM, si vide supprime la date d'anniversaire
        """
        if not date:
            self.remove_user_birthday(user)
            await interaction.response.send_message(f"**Données supprimées**\nLa date d'anniversaire de {user.mention} a été supprimée.", ephemeral=True)
            return

        if not self.is_valid_date(date):
            await interaction.response.send_message("**Erreur de format**\nLa date doit être au format JJ/MM.", ephemeral=True)
            return
        
        dt = datetime.strptime(date, '%d/%m')
        self.set_user_birthday(user, dt)
        await interaction.response.send_message(f"**Date d'anniversaire définie**\nLa date d'anniversaire de {user.mention} a été définie au `{dt.strftime('%d/%m')}`.", ephemeral=True)
        
    # Rôles et salons de notification d'anniversaire
        
    @app_commands.command(name="setrole")
    @app_commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def _set_bday_role(self, interaction: discord.Interaction, role: Optional[discord.Role] = None):
        """Définir le rôle à attribuer aux membres dont c'est l'anniversaire le jour J

        :param role: Rôle à attribuer, si vide désactive cette fonctionnalité
        """
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("**Erreur**\nCette commande ne peut être utilisée que sur un serveur.", ephemeral=True)
        
        if not role:
            role_id = 0
        else:
            role_id = role.id
            
        self.set_guild_setting(guild, "bday_role_id", role_id)
        if not role:
            return await interaction.response.send_message("**Rôle supprimé**\nLe rôle à attribuer aux membres dont c'est l'anniversaire a été supprimé.", ephemeral=True)
        await interaction.response.send_message(f"**Rôle défini**\nLe rôle `{role.name}` sera attribué automatiquement aux membres dont c'est l'anniversaire.", ephemeral=True)
        
    @app_commands.command(name="setchannel")
    @app_commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def _set_bday_channel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        """Définir le salon dans lequel envoyer les notifications d'anniversaire le jour J

        :param channel: Salon écrit dans lequel envoyer les notifications, si vide désactive cette fonctionnalité
        """
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("**Erreur**\nCette commande ne peut être utilisée que sur un serveur.", ephemeral=True)
        
        if not channel:
            channel_id = 0
        else:
            channel_id = channel.id
            
        # Vérifier que le bot a les permissions d'envoyer des messages dans le salon
        if channel and not channel.permissions_for(guild.me).send_messages:
            return await interaction.response.send_message("**Erreur**\nJe n'ai pas les permissions d'envoyer des messages dans ce salon.", ephemeral=True)
            
        self.set_guild_setting(guild, "bday_channel_id", channel_id)
        if not channel:
            return await interaction.response.send_message("**Salon supprimé**\nLe salon dans lequel envoyer les notifications d'anniversaire a été supprimé.", ephemeral=True)
        await interaction.response.send_message(f"**Salon défini**\nLes notifications d'anniversaire seront envoyées dans le salon {channel.mention}.", ephemeral=True)
    
async def setup(bot):
    await bot.add_cog(Birthdays(bot))
