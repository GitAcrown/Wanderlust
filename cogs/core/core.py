import io
import logging
import platform
import textwrap
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from gpiozero import CPUTemperature, DiskUsage, LoadAverage

from common import dataio

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

class Core(commands.Cog):
    """Module central du bot, contenant des commandes de base."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_result: Optional[Any] = None

    # ---- Gestion des commandes et modules ----

    @commands.command(name="load", hidden=True)
    @commands.is_owner()
    async def load(self, ctx, *, cog: str):
        """Command which Loads a Module.
        Remember to use dot path. e.g: cogs.owner"""

        try:
            await self.bot.load_extension(cog)
        except Exception as exc:
            await ctx.send(f"**`ERROR:`** {type(exc).__name__} - {exc}")
        else:
            await ctx.send("**`SUCCESS`**")

    @commands.command(name="unload", hidden=True)
    @commands.is_owner()
    async def unload(self, ctx, *, cog: str):
        """Command which Unloads a Module.
        Remember to use dot path. e.g: cogs.owner"""

        try:
            await self.bot.unload_extension(cog)
        except Exception as exc:
            await ctx.send(f"**`ERROR:`** {type(exc).__name__} - {exc}")
        else:
            await ctx.send("**`SUCCESS`**")

    @commands.command(name="reload", hidden=True)
    @commands.is_owner()
    async def reload(self, ctx, *, cog: str):
        """Command which Reloads a Module.
        Remember to use dot path. e.g: cogs.owner"""

        try:
            await self.bot.reload_extension(cog)
        except Exception as exc:
            await ctx.send(f"**`ERROR:`** {type(exc).__name__} - {exc}")
        else:
            await ctx.send("**`SUCCESS`**")

    @commands.command(name="extensions", hidden=True)
    @commands.is_owner()
    async def extensions(self, ctx):
        for ext_name, _ext in self.bot.extensions.items():
            await ctx.send(ext_name)

    @commands.command(name="cogs", hidden=True)
    @commands.is_owner()
    async def cogs(self, ctx):
        for cog_name, _cog in self.bot.cogs.items():
            await ctx.send(cog_name)

            
    # ---- Commandes d'évaluation de code ----
            
    def cleanup_code(self, content: str) -> str:
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith('```') and content.endswith('```'):
            return '\n'.join(content.split('\n')[1:-1])

        # remove `foo`
        return content.strip('` \n')
            
    @commands.command(name='eval', hidden=True)
    @commands.is_owner()
    async def eval_code(self, ctx: commands.Context, *, body: str):
        """Evalue du code"""

        env = {
            'bot': self.bot,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
            '_': self._last_result,
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f'```py\n{e.__class__.__name__}: {e}\n```')

        func = env['func']
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception as e:
            value = stdout.getvalue()
            await ctx.send(f'```py\n{value}{traceback.format_exc()}\n```')
        else:
            value = stdout.getvalue()
            try:
                await ctx.message.add_reaction('\u2705')
            except:
                pass

            if ret is None:
                if value:
                    await ctx.send(f'```py\n{value}\n```')
            else:
                self._last_result = ret
                await ctx.send(f'```py\n{value}{ret}\n```')
                
    # ---- Commandes Outils ----
    
    @app_commands.command(name='hostinfo')
    async def _get_rasp_temp(self, interaction: discord.Interaction):
        """Renvoie des informations sur l'hébergement du bot"""
        cpu = CPUTemperature()
        load = LoadAverage()
        disk = DiskUsage()
        platform_info = f"`{platform.system()} {platform.release()}`"
    
        # Couleur de l'embed en fonction de la température du CPU
        temp_colors = {
            30: discord.Color.green(),
            40: discord.Color.gold(),
            50: discord.Color.orange(),
            60: discord.Color.red()
        }
        col = [v for k, v in temp_colors.items() if cpu.temperature < k][0]
        embed = discord.Embed(title="**Informations** concernant l'hébergement", color=col)
        embed.description = f"***{self.bot.user.name}*** est hébergé bénévolement par *{self.bot.get_user(int(self.bot.config['OWNER']))}* depuis le 27/05/2023." #type: ignore
        embed.add_field(name="Modèle", value="RaspberryPi 4B 4Go")
        embed.add_field(name="OS", value=platform_info)
        embed.add_field(name="Température (CPU)", value=f"{cpu.temperature:.2f}°C")
        embed.add_field(name="Charge moyenne (CPU)", value=f"{load.load_average:.2f}%")
        embed.add_field(name="Espace disque utilisé", value=f"{disk.usage:.2f}%")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='info')
    async def _get_bot_info(self, interaction: discord.Interaction):
        """Obtenir des informations sur le bot"""
        if not isinstance(self.bot.user, discord.User):
            return await interaction.response.send_message("Impossible d'obtenir des informations sur le bot.", ephemeral=True)
        
        temp_colors = {
            30: discord.Color.green(),
            40: discord.Color.gold(),
            50: discord.Color.orange(),
            60: discord.Color.red()
        }
        
        embed = discord.Embed(title=f"**Informations concernant `{self.bot.user}`**")
        embed.description = f"***{self.bot.user.name}*** est un bot Discord développé par *{self.bot.get_user(int(self.bot.config['OWNER']))}* disponible depuis 4 Mai 2023."
        
        cpu = CPUTemperature()
        load = LoadAverage()
        disk = DiskUsage()
        col = [v for k, v in temp_colors.items() if cpu.temperature < k][0]
        inforasp = f"**Modèle** : `Raspberry Pi 4 Model B`\n**Temp. CPU** : `{cpu.temperature:.2f}°C`\n**Charge moy. CPU** : `{load.load_average:.2f}%`\n**Espace disque** : `{disk.usage:.2f}%`"
        embed.add_field(name="Hébergement", value=inforasp)

        sysinfo = f"**OS** : `{platform.system()} {platform.release()}`\n**Python** : `{platform.python_version()}`\n**discord.py** : `{discord.__version__}`\n**SQLite** : `{dataio.sqlite3.sqlite_version}`"
        embed.add_field(name="Système", value=sysinfo)
        
        # Calcul de la place occupée par les données du bot
        total_size = 0
        for path in Path("data").rglob("*"):
            total_size += path.stat().st_size
        total_size = total_size / 1024 / 1024
        embed.add_field(name="Données", value=f"**Taille** : `{total_size:.2f} Mo`\n**Nb. fichiers** : `{len(list(Path('data').rglob('*')))} fichiers`")
        
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.color = col
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Core(bot))
