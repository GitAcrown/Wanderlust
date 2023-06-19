# Ce module ne doit être chargé que si l'hébergement est sur RaspberryPi
# Vous pouvez désactiver ce module ou le supprimer si vous n'avez pas de RaspberryPi où héberger Wanderlust

import logging
import math
import platform
from typing import Optional

import adafruit_dht
import board
import discord
from discord import app_commands
from discord.ext import commands, tasks
from gpiozero import CPUTemperature, DiskUsage, LoadAverage

from common import dataio

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

class IntegPi(commands.GroupCog, group_name="pi", description="Intégrations réservées à l'hébergement sur RaspberryPi"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
        self.dhtDevice = adafruit_dht.DHT22(board.D4, use_pulseio=False)
        
    @app_commands.command(name='info')
    async def _get_bot_info(self, interaction: discord.Interaction):
        """Obtenir des informations sur le bot et son hébergement"""
        if not isinstance(self.bot.user, discord.ClientUser):
            return await interaction.response.send_message("Impossible d'obtenir des informations sur le bot.", ephemeral=True)
        
        temp_colors = {
            30: discord.Color.green(),
            40: discord.Color.gold(),
            50: discord.Color.orange(),
            60: discord.Color.red()
        }
        
        embed = discord.Embed(title=f"**Informations concernant `{self.bot.user}`**")
        embed.description = f"***{self.bot.user.name}*** est un bot développé et maintenu par *{self.bot.get_user(int(self.bot.config['OWNER']))}* disponible depuis le 4 Mai 2023."
        
        cpu = CPUTemperature()
        load = LoadAverage()
        disk = DiskUsage()
        col = [v for k, v in temp_colors.items() if cpu.temperature < k][0]
        inforasp = f"**Modèle** : `Raspberry Pi 4 Model B`\n**Temp. CPU** : `{cpu.temperature:.2f}°C`\n**Charge moy. CPU** : `{load.load_average:.2f}%`\n**Espace disque** : `{disk.usage:.2f}%`"
        embed.add_field(name="Hébergement", value=inforasp)

        sysinfo = f"**OS** : `{platform.system()} {platform.release()}`\n**Python** : `{platform.python_version()}`\n**discord.py** : `{discord.__version__}`\n**SQLite** : `{dataio.sqlite3.sqlite_version}`"
        embed.add_field(name="Système", value=sysinfo)
        
        # Calcul de la place occupée par les données du bot
        total_size = dataio.get_total_db_size()
        total_size = total_size / 1024 / 1024
        total_count = dataio.get_total_db_count()
        embed.add_field(name="Données", value=f"**Taille** : `{total_size:.2f} Mo`\n**Nb. fichiers** : `{total_count}`")
        
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.color = col
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name='stats')
    async def _get_rasp_stats(self, interaction: discord.Interaction, reload_dht: Optional[bool] = False):
        """Affiche diverses informations sur le Raspberry Pi
        
        :param reload_dht: Force le rechargement du capteur DHT22 en cas d'erreur de lecture"""
        await interaction.response.defer()
        while True:
            try:
                temperature = self.dhtDevice.temperature
                humidite = self.dhtDevice.humidity
                if not temperature and not humidite:
                    continue
                
                # calcul du point de rosée  (formule de Heinrich Gustav Magnus-Tetens)
                alpha = math.log(humidite / 100.0) + (17.27 * temperature) / (237.3 + temperature) #type: ignore
                rosee = (237.3 * alpha) / (17.27 - alpha)

                #calcul de l'humidex 
                humidex = temperature + 0.5555 * (6.11 * math.exp(5417.753 * (1 / 273.16 - 1 / (273.15 + rosee))) - 10) #type: ignore

                embed = discord.Embed(title=f"**Informations concernant le Raspberry Pi**", color=0x2b2d31)
                owner = self.bot.get_user(int(self.bot.config['OWNER']))
                embed.description = f"Ces informations proviennent d'un capteur DHT22 intégré au Raspberry Pi hébergeant ce bot, chez {owner}."
                embed.add_field(name="Température", value=f"`{temperature:.2f}°C`\n(CPU `{CPUTemperature().temperature:.2f}°C`)")
                embed.add_field(name="Humidité", value=f"`{humidite:.2f}%`")
                embed.add_field(name="Point de rosée¹", value=f"`{rosee:.2f}°C`")
                embed.add_field(name="Temp. ressentie (Humidex)²", value=f"`{humidex:.2f}°C`")
                embed.set_footer(text="¹ : Formule de Heinrich Gustav Magnus-Tetens\n² : Formule de Maurice Richard")
                return await interaction.followup.send(embed=embed)
            except RuntimeError as error:
                #print(error.args[0])
                continue
            except Exception as error:
                self.dhtDevice.exit()
                if reload_dht:
                    self.dhtDevice = adafruit_dht.DHT22(board.D4, use_pulseio=False)
                raise error
    
async def setup(bot):
    await bot.add_cog(IntegPi(bot))
