import json
import logging
from io import BytesIO
from typing import List, Optional, Union

import colorgram
import discord
import requests
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps

from common import dataio

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

DEFAULT_GUILD_SETTINGS = {
    'enabled': 1, # Activation des rôles de couleur personnalisés sur le serveur
    'boundary_role_id': 0 # ID du rôle utilisé pour délimiter les rôles de couleur et faciliter leur rangement
}

class ChooseColorMenu(discord.ui.View):
    def __init__(self, cog: 'Colorful', initial_interaction: discord.Interaction, colors: List[colorgram.Color], previews: List[Image.Image]):
        super().__init__(timeout=60)
        self._cog = cog
        self.colors = colors
        
        self.previews = previews
        self.index = 0
        
        self.initial_interaction = initial_interaction
        self.menu_interaction : Optional[discord.WebhookMessage] = None
        self.result = None
        
    @property
    def color_choice(self) -> colorgram.Color:
        """Renvoie la couleur sélectionnée"""
        return self.colors[self.index]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie que l'utilisateur est bien le même que celui qui a lancé la commande"""
        if interaction.user != self.initial_interaction.user:
            await interaction.followup.send("Vous n'êtes pas l'auteur de cette commande.", ephemeral=True)
            return False
        return True
    
    def get_embed(self) -> discord.Embed:
        """Renvoie l'embed de la couleur sélectionnée"""
        color = self.color_choice.rgb
        hexname = self._cog.rgb_to_hex(color)
        info = self._cog.get_color_info(hexname)
        embed = discord.Embed(title=f'{hexname.upper()}', description="Couleurs extraites de l'avatar (du serveur) demandé", color=discord.Color.from_rgb(*color))
        embed.set_image(url='attachment://color.png')
        pagenb = f'{self.index + 1}/{len(self.colors)}'
        if info:
            embed.set_footer(text=f"{pagenb} · {info['name']['value']}")
        else:
            embed.set_footer(text=pagenb)
        return embed
    
    async def start(self):
        """Affiche le menu de sélection de couleur"""
        with BytesIO() as f:
            self.previews[self.index].save(f, format='png')
            f.seek(0)
            self.menu_interaction = await self.initial_interaction.followup.send(embed=self.get_embed(), file=discord.File(f, 'color.png'), view=self)
            
    async def update(self):
        """Met à jour l'image de la couleur sélectionnée"""
        if not self.menu_interaction:
            return
        with BytesIO() as f:
            self.previews[self.index].save(f, format='png')
            f.seek(0)
            await self.menu_interaction.edit(embed=self.get_embed(), attachments=[discord.File(f, 'color.png')])

    @discord.ui.button(emoji="<:iconLeftArrow:1078124175631339580>", style=discord.ButtonStyle.grey)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Affiche la couleur précédente"""
        await interaction.response.defer()
        self.index -= 1
        if self.index < 0:
            self.index = len(self.colors) - 1
        await self.update()
        
    @discord.ui.button(emoji="<:iconRightArrow:1078124174352076850>", style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Affiche la couleur suivante"""
        await interaction.response.defer()
        self.index += 1
        if self.index >= len(self.colors):
            self.index = 0
        await self.update()
    
    # Valider la couleur
    @discord.ui.button(label='Valider', style=discord.ButtonStyle.green, row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Valide la couleur sélectionnée"""
        self.result = self.color_choice
        self.stop()
        await self.initial_interaction.delete_original_response()
        
    # Annuler
    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.red, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Annule la sélection de couleur"""
        await self.initial_interaction.delete_original_response()
        
    async def on_timeout(self) -> None:
        """Annule la sélection de couleur si le menu a expiré"""
        await self.initial_interaction.delete_original_response()
        

class Colorful(commands.GroupCog, group_name='color', description='Gestion des rôles de couleur'):
    """Gestion des rôles de couleur"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)

    @commands.Cog.listener()
    async def on_ready(self):
        self._init_guilds_db()
        self._init_users_db()
    
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._init_guilds_db(guild)
        
    def cog_unload(self):
        self.data.close_all_databases()
        
    def _init_guilds_db(self, guild: Optional[discord.Guild] = None) -> None:
        guilds = self.bot.guilds if guild is None else [guild]
        for g in guilds:
            self.data.execute(g, """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
            self.data.executemany(g, """INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)""", DEFAULT_GUILD_SETTINGS.items())
            
    def _init_users_db(self) -> None:
        self.data.execute('users', """CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, aac INTEGER CHECK (aac IN (0, 1)))""")
        
    # Userdata
        
    def dataio_list_user_data(self, user_id: int) -> List[dataio.UserDataEntry]:
        data = []
        userdata = self.data.fetchone('users', """SELECT * FROM users WHERE user_id = ?""", (user_id,))
        if userdata:
            data.append(dataio.UserDataEntry(user_id, 'users', "Paramètres personnels (ex. Changement auto. de couleur)", importance_level=1))
        return data
    
    def dataio_wipe_user_data(self, user_id: int, table_name: str) -> bool:
        if table_name == 'users':
            self.data.execute('users', """DELETE FROM users WHERE user_id = ?""", (user_id,))
            return True
        return False
    
    def dataio_extract_user_data(self, user_id: int, table_name: str) -> Optional[dict]:
        if table_name == 'users':
            userdata = self.data.fetchone('users', """SELECT * FROM users WHERE user_id = ?""", (user_id,))
            if userdata:
                return {'aac': bool(userdata['aac'])}
        return None
    
    # Guild settings
            
    def get_guild_settings(self, guild: discord.Guild) -> dict:
        result = self.data.fetchall(guild, """SELECT * FROM settings""")
        return {row['key']: json.loads(row['value']) for row in result}
    
    def set_guild_setting(self, guild: discord.Guild, key: str, value: str) -> None:
        self.data.execute(guild, """INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)""", (key, json.dumps(value)))
        
    def get_boundary_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        role_id = int(self.get_guild_settings(guild)['boundary_role_id'])
        return guild.get_role(role_id) if role_id else None
    
    def set_boundary_role(self, guild: discord.Guild, role: Optional[discord.Role] = None) -> None:
        if role is None:
            self.set_guild_setting(guild, 'boundary_role_id', '0')
        else:
            self.set_guild_setting(guild, 'boundary_role_id', str(role.id))
            
    # Users
    
    def get_user_aac_status(self, user: Union[discord.User, discord.Member]) -> bool:
        result = self.data.fetchone('users', """SELECT aac FROM users WHERE user_id = ?""", (user.id,))
        return bool(result['aac']) if result else False

    def set_user_aac_status(self, user: Union[discord.User, discord.Member], status: bool) -> None:
        self.data.execute('users', """INSERT OR REPLACE INTO users (user_id, aac) VALUES (?, ?)""", (user.id, int(status)))

    async def update_user_color_role(self, user: discord.Member) -> None:
        """Change automatiquement le rôle de couleur d'un utilisateur en fonction de son avatar"""  
        if not self.get_user_aac_status(user):
            return
        
        guild = user.guild
        
        # Récupérer la couleur dominante de l'avatar
        avatar = await user.display_avatar.read()
        avatar = Image.open(BytesIO(avatar))
        color = colorgram.extract(avatar, 1)
        color = color[0].rgb

        # Récupérer le rôle s'il existe sinon le créer
        color = self.rgb_to_hex(color)
        role = await self.create_color_role(guild, user, color)
        if not role:
            return
        await self.organize_color_roles(guild)
        
        # Appliquer le rôle
        if role not in user.roles:
            self_color_role = self.get_user_color_role(user)
            if self_color_role:
                try:
                    await user.remove_roles(self_color_role)
                except discord.Forbidden:
                    return
                except discord.HTTPException:
                    return

            try:
                await user.add_roles(role)
            except discord.Forbidden:
                return
            except discord.HTTPException:
                return
            
        warning = ""
        if not self.is_color_displayed(user):
            warning = "Un autre rôle coloré est plus haut dans la hiérarchie de vos rôles. Vous ne verrez pas la couleur de ce rôle tant que vous ne le retirerez pas."
            
        image = self.create_color_block(color, False)
        embed = self.color_embed(color, "Vous avez désormais le rôle **{}**{}".format(role.name, '\n\n' + warning if warning else ''))
        embed.title = "Changement automatique de couleur (AAC)"
        with BytesIO() as f:
            image.save(f, 'PNG')
            f.seek(0)
            image = discord.File(f, filename='color.png', description=f'Bloc de couleur #{color}')
            try:
                await user.send(embed=embed, file=image)
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                pass

    
    # Couleurs
    
    def normalize_color(self, color: str) -> Optional[str]:
        """Renvoie la couleur hexadécimale normalisée au format RRGGBB"""
        if color.startswith('0x'):
            color = color[2:]
        if color.startswith('#'):
            color = color[1:]
        if len(color) == 3:
            color = ''.join(c * 2 for c in color)
        # Vérifier que la couleur est valide
        try:
            int(color, 16)
        except ValueError:
            return None
        return color.upper()
    
    def rgb_to_hex(self, rgb: tuple) -> str:
        """Renvoie la couleur hexadécimale à partir des valeurs RGB"""
        return f'#{"".join(f"{c:02x}" for c in rgb)}'
        
    def is_recyclable(self, role: discord.Role, request_user: Optional[discord.Member] = None) -> bool:
        """Renvoie True si le rôle n'est possédé par personne ou par le membre faisant la demande, sinon False"""
        if not role.members:
            return True
        elif request_user and role.members == [request_user]:
            return True
        return False
    
    def guild_recyclable_color_roles(self, guild: discord.Guild, request_user: Optional[discord.Member] = None) -> List[discord.Role]:
        """Renvoie la liste des rôles de couleur recyclables"""
        return [role for role in self.get_color_roles(guild) if self.is_recyclable(role, request_user)]
    
    def get_user_color_role(self, member: discord.Member) -> Optional[discord.Role]:
        """Renvoie le rôle de couleur possédé par le membre"""
        roles = [role for role in member.roles if role.name.startswith('#') and len(role.name) == 7]
        if roles:
            return roles[0]
        return None
    
    def get_all_user_color_roles(self, guild: discord.Guild) -> List[discord.Role]:
        """Renvoie la liste de tous les rôles de couleur du serveur"""
        return [role for role in self.get_color_roles(guild) if role.members]
    
    async def create_color_role(self, guild: discord.Guild, request_user: discord.Member, color: str) -> discord.Role:
        """Crée un rôle de couleur (ou en recycle un si possible) et l'ajoute au serveur"""
        color = self.normalize_color(color) #type: ignore
        if not color:
            raise commands.BadArgument('La couleur spécifiée est invalide.')
        guild_color_role = self.get_color_role(guild, color)
        if guild_color_role:
            return guild_color_role
        
        self_color = self.get_user_color_role(request_user)
        if self_color and self.is_recyclable(self_color, request_user):
            role = self_color
            await role.edit(name=f'#{color}', color=discord.Color(int(color, 16)))
        elif self.guild_recyclable_color_roles(guild, request_user):
            role = self.guild_recyclable_color_roles(guild, request_user)[0]
            await role.edit(name=f'#{color}', color=discord.Color(int(color, 16)))
        else:
            role = await guild.create_role(name=f'#{color}', color=discord.Color(int(color, 16)))
        return role
    
    async def organize_color_roles(self, guild: discord.Guild) -> bool:
        """Organise les rôles de couleur du serveur en dessous du rôle balise"""
        roles = self.get_color_roles(guild)
        if not roles:
            return False
        roles = sorted(roles, key=lambda r: r.name)
        beacon_role = self.get_boundary_role(guild)
        if not beacon_role:
            return False
        await guild.edit_role_positions({role: beacon_role.position - 1 for role in roles})
        return True
    
    def is_color_displayed(self, member: discord.Member) -> bool:
        """Renvoie True si la couleur du membre est celle de son rôle de couleur, sinon False"""
        role = self.get_user_color_role(member)
        if role and role.color == member.color:
            return True
        return False
    
    async def add_color_role(self, member: discord.Member, role: discord.Role) -> None:
        """Ajoute le rôle de couleur donné au membre"""
        await member.add_roles(role)
    
    async def delete_color_role(self, member: discord.Member) -> None:
        """Supprime le rôle de couleur du membre"""
        role = self.get_user_color_role(member)
        if role:
            await member.remove_roles(role)
        
    async def clean_guild_color_roles(self, guild: discord.Guild) -> None:
        """Supprime les rôles de couleur inutilisés du serveur"""
        for role in self.guild_recyclable_color_roles(guild):
            await role.delete()

    def get_color_role(self, guild: discord.Guild, hex_color: str) -> Optional[discord.Role]:
        """Renvoie le rôle de couleur correspondant à la couleur hexadécimale donnée"""
        name = f"#{self.normalize_color(hex_color)}"
        return discord.utils.get(guild.roles, name=name)

    def get_color_roles(self, guild: discord.Guild) -> List[discord.Role]:
        """Renvoie la liste des rôles de couleur du serveur"""
        return [role for role in guild.roles if role.name.startswith('#') and len(role.name) == 7]
    
    def create_color_block(self, color: Union[str, tuple], with_text: bool = True) -> Image.Image:
        """Renvoie un bloc de couleur"""
        path = str(self.data.assets_path)
        font_path = f"{path}/gg_sans.ttf"
        if isinstance(color, str):
            color = self.normalize_color(color) #type: ignore
            if not color:
                raise commands.BadArgument('La couleur spécifiée est invalide.')
            color = tuple(int(color[i:i+2], 16) for i in (0, 2, 4)) #type: ignore
        image = Image.new('RGB', (100, 100), color)
        d = ImageDraw.Draw(image)
        if with_text:
            if sum(color) < 382:
                d.text((10, 10), f"#{color}", fill=(255, 255, 255), font=ImageFont.truetype(font_path, 20))
            else:
                d.text((10, 10), f"#{color}", fill=(0, 0, 0), font=ImageFont.truetype(font_path, 20))
        return image
    
    def color_embed(self, color: str, text: str) -> discord.Embed:
        """Renvoie l'embed de la couleur donnée"""
        color = self.normalize_color(color) #type: ignore
        if not color: 
            raise commands.BadArgument('La couleur spécifiée est invalide.')
        info = self.get_color_info(color)
        embed = discord.Embed(description=text, color=discord.Color(int(color, 16)))
        if info:
            embed.set_footer(text=f"{info['name']['value']}")
        embed.set_thumbnail(url="attachment://color.png")
        return embed
    
    async def simulate_discord_display(self, user: Union[discord.User, discord.Member], name_color: tuple) -> Image.Image:
        path = str(self.data.assets_path)
        avatar = await user.display_avatar.read()
        avatar = Image.open(BytesIO(avatar))
        avatar = avatar.resize((128, 128)).convert("RGBA")
        
        # Mettre l'avatar en cercle
        mask = Image.new("L", avatar.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0) + avatar.size, fill=255)
        avatar.putalpha(mask)
        avatar = avatar.resize((50, 50))
        
        images = []
        # Créer une version avec le fond foncé et une version avec le fond clair
        for v in [(54, 57, 63), (255, 255, 255)]:
            bg = Image.new("RGBA", (320, 94), v)
            bg.paste(avatar, (10, 10), avatar)
            d = ImageDraw.Draw(bg)
            avatar_font = ImageFont.truetype(f"{path}/gg_sans.ttf", 18)
            d.text((74, 14), user.display_name, font=avatar_font, fill=name_color)
        
            content_font = ImageFont.truetype(f"{path}/gg_sans_light.ttf", 14)
            text_color = (255, 255, 255) if v == (54, 57, 63) else (0, 0, 0)
            d.text((74, 40), "Ceci est une représentation de\nla couleur qu'aurait votre pseudo", font=content_font, fill=text_color)
            images.append(bg)
        
        # On met les deux images une en dessous de l'autre
        full = Image.new("RGBA", (320, 188), (54, 57, 63))
        full.paste(images[0], (0, 0), images[0])
        full.paste(images[1], (0, 94), images[1])
        return full
            
    def get_color_info(self, color: str) -> Optional[dict]:
        """Renvoie les informations de la couleur donnée"""
        color = self.normalize_color(color) #type: ignore
        url = f"https://www.thecolorapi.com/id?hex={color}"
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
        return None
    
    def draw_image_palette(self, img: Union[str, BytesIO], n_colors: int = 5) -> Image.Image:
        """Ajoute la palette de 5 couleur extraite de l'image sur le côté de celle-ci avec leurs codes hexadécimaux"""
        path = str(self.data.assets_path)
        colors : List[colorgram.Color] = colorgram.extract(img, n_colors)
        image = Image.open(img).convert("RGBA")
        image = ImageOps.contain(image, (500, 500))
        iw, ih = image.size
        w, h = (iw + 100, ih)
        font = ImageFont.truetype(f'{path}/RobotoRegular.ttf', 18)   
        palette = Image.new('RGBA', (w, h), color='white')
        maxcolors = h // 30
        if len(colors) > maxcolors:
            colors = colors[:maxcolors]
        blockheight = h // len(colors)
        for i, color in enumerate(colors):
            # On veut que le dernier block occupe tout l'espace restant
            if i == len(colors) - 1:
                palette.paste(color.rgb, (iw, i * blockheight, iw + 100, h))
            else:
                palette.paste(color.rgb, (iw, i * blockheight, iw + 100, i * blockheight + blockheight))
            draw = ImageDraw.Draw(palette)
            hex_color = f'#{color.rgb[0]:02x}{color.rgb[1]:02x}{color.rgb[2]:02x}'.upper()
            if color.rgb[0] + color.rgb[1] + color.rgb[2] < 382:
                draw.text((iw + 10, i * blockheight + 10), f'{hex_color}', fill='white', font=font)
            else:
                draw.text((iw + 10, i * blockheight + 10), f'{hex_color}', fill='black', font=font)
        palette.paste(image, (0, 0))
        return palette
    
    @app_commands.command(name='palette')
    async def show_palette(self, interaction: discord.Interaction, colors: app_commands.Range[int, 3, 10] = 5, file: Optional[discord.Attachment] = None, url: Optional[str] = None, user: Optional[discord.User] = None):
        """Génère une palette de 5 couleurs (les plus dominantes) à partir d'une image. Si aucune image n'est fournie, la palette est générée à partir de la dernière image envoyée dans le salon.
        
        :param colors: Nombre de couleurs à extraire de l'image (entre 3 et 10)
        :param file: Image dont on veut extraire la palette
        :param url: URL directe d'une image dont on veut extraire la palette
        :param user: Utilisateur dont on veut extraire la palette de la photo de profil
        """
        await interaction.response.defer()
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread, discord.DMChannel, discord.GroupChannel)):
            return await interaction.response.send_message("**Erreur · ** Vous ne pouvez pas utiliser cette commande ici.", ephemeral=True)
        if file:
            img = BytesIO(await file.read())
            palette = self.draw_image_palette(img, colors)
        else:
            if user:
                url = user.display_avatar.url
                
            if not url:
                async for message in interaction.channel.history(limit=20): # On cherche dans les 20 derniers messages
                    if message.attachments:
                        type = message.attachments[0].content_type
                        if type not in ['image/png', 'image/jpeg', 'image/gif']:
                            continue
                        url = message.attachments[0].url
                        break
                else:
                    return await interaction.response.send_message("**Erreur ·** Aucune image valable n'a été trouvée dans l'historique récent de ce salon.", ephemeral=True)
                
            with requests.get(url) as r:
                if r.status_code != 200:
                    return await interaction.response.send_message("**Erreur ·** L'image n'a pas pu être téléchargée. Vérifiez que l'URL est correcte et que l'image n'est pas trop volumineuse (max. 8 Mo).", ephemeral=True)
                elif len(r.content) > 8388608:
                    return await interaction.response.send_message("**Erreur ·** L'image est trop volumineuse (max. 8 Mo).", ephemeral=True)
                img = BytesIO(r.content)
                
            palette = self.draw_image_palette(img, colors)
        
        if not palette:
            return await interaction.response.send_message("**Erreur ·** Une erreur s'est produite lors de la génération de la palette.", ephemeral=True)
        
        with BytesIO() as f:
            palette.save(f, 'PNG')
            f.seek(0)
            palette = discord.File(f, filename='palette.png', description='Palette de couleurs extraite de l\'image')
            await interaction.followup.send(file=palette)
        
    @app_commands.command(name="get")
    @app_commands.guild_only()
    async def get_color(self, interaction: discord.Interaction, color: str):
        """Obtenir un rôle de la couleur donnée
        
        :param color: Code hexadécimal de la couleur (ex. #FF0000)
        """
        member = interaction.user
        guild = interaction.guild
        if not isinstance(member, discord.Member) or not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("**Erreur ·** Vous devez être membre d'un serveur pour utiliser cette commande.", ephemeral=True)
        
        # Est-ce que la fonctionnalité est activée sur le serveur
        settings = self.get_guild_settings(guild)
        if int(settings['enabled']) == 0:
            return await interaction.response.send_message("**Erreur ·** Cette fonctionnalité est désactivée sur ce serveur.", ephemeral=True)
        
        # Vérifier si la couleur est valide
        
        await interaction.response.defer()
        color = self.normalize_color(color) #type: ignore
        if not color:
            return await interaction.followup.send("**Erreur ·** Le code hexadécimal de la couleur est invalide.", ephemeral=True)
        
        role = await self.create_color_role(guild, member, color)
        if not role:
            return await interaction.followup.send("**Erreur ·** Une erreur s'est produite lors de la création du rôle.", ephemeral=True)

        await self.organize_color_roles(guild)

        if role not in member.roles:
            self_color_role = self.get_user_color_role(member)
            if self_color_role:
                try:
                    await member.remove_roles(self_color_role)
                except discord.Forbidden:
                    return await interaction.followup.send("**Erreur ·** Je n'ai pas la permission de vous retirer le rôle **{}**.".format(self_color_role.name), ephemeral=True)
                except discord.HTTPException:
                    return await interaction.followup.send("**Erreur ·** Une erreur s'est produite lors du retrait du rôle **{}**.".format(self_color_role.name), ephemeral=True)

            try:
                await member.add_roles(role)
            except discord.Forbidden:
                return await interaction.followup.send("**Erreur ·** Je n'ai pas la permission de vous attribuer ce rôle.", ephemeral=True)
            except discord.HTTPException:
                return await interaction.followup.send("**Erreur ·** Une erreur s'est produite lors de l'attribution du rôle.", ephemeral=True)
            
        warning = ""
        if not self.is_color_displayed(member):
            warning = "Un autre rôle coloré est plus haut dans la hiérarchie de vos rôles. Vous ne verrez pas la couleur de ce rôle tant que vous ne le retirerez pas."
            
        image = self.create_color_block(color, False)
        embed = self.color_embed(color, "Vous avez désormais le rôle **{}**{}".format(role.name, '\n\n' + warning if warning else ''))
        with BytesIO() as f:
            image.save(f, 'PNG')
            f.seek(0)
            image = discord.File(f, filename='color.png', description=f'Bloc de couleur #{color}')
            await interaction.followup.send(file=image, embed=embed)

    @app_commands.command(name="remove")
    @app_commands.guild_only()
    async def remove_color(self, interaction: discord.Interaction):
        """Retire votre rôle de couleur sur ce serveur"""
        member = interaction.user
        guild = interaction.guild
        if not isinstance(member, discord.Member) or not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("**Erreur ·** Vous devez être membre d'un serveur pour utiliser cette commande.", ephemeral=True)
        
        # Est-ce que la fonctionnalité est activée sur le serveur
        settings = self.get_guild_settings(guild)
        if int(settings['enabled']) == 0:
            return await interaction.response.send_message("**Erreur ·** Cette fonctionnalité est désactivée sur ce serveur.", ephemeral=True)
        
        await interaction.response.defer()
        roles = self.get_all_user_color_roles(member)
        if not roles:
            return await interaction.followup.send("**Erreur ·** Vous n'avez pas de rôle de couleur.", ephemeral=True)
        
        removed_roles = []
        for role in roles:
            try:
                await member.remove_roles(role)
            except discord.Forbidden:
                return await interaction.followup.send("**Erreur ·** Je n'ai pas la permission de vous retirer ce rôle.", ephemeral=True)
            except discord.HTTPException:
                return await interaction.followup.send("**Erreur ·** Une erreur s'est produite lors du retrait du rôle.", ephemeral=True)
        
            if len(role.members) == 0:
                try:
                    await role.delete()
                except discord.Forbidden:
                    return await interaction.followup.send("**Erreur ·** Je n'ai pas la permission de supprimer ce rôle.", ephemeral=True)
                except discord.HTTPException:
                    return await interaction.followup.send("**Erreur ·** Une erreur s'est produite lors de la suppression du rôle.", ephemeral=True)
                
            removed_roles.append(role)

        await interaction.followup.send("**Succès · ** Vous n'avez plus les rôles *{}*".format(', '.join(r.name for r in removed_roles)))
        
    @app_commands.command(name="avatar")
    @app_commands.guild_only()
    async def avatar_color(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        """Attribue un rôle de couleur en fonction d'un avatar
        
        :param member: Membre dont vous voulez obtenir la couleur d'avatar (optionnel)
        """
        member = member or interaction.user # type: ignore
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message("**Erreur ·** Vous devez être membre d'un serveur pour utiliser cette commande.", ephemeral=True)
        request = interaction.user
        guild = interaction.guild
        if not isinstance(request, discord.Member) or not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("**Erreur ·** Vous devez être membre d'un serveur pour utiliser cette commande.", ephemeral=True)
        
        # Est-ce que la fonctionnalité est activée sur le serveur
        settings = self.get_guild_settings(guild)
        if int(settings['enabled']) == 0:
            return await interaction.response.send_message("**Erreur ·** Cette fonctionnalité est désactivée sur ce serveur.", ephemeral=True)
        
        await interaction.response.defer()
        avatar = await member.display_avatar.read()
        avatar = Image.open(BytesIO(avatar))
        colors = colorgram.extract(avatar, 5)
        previews = []
        for color in colors:
            previews.append(await self.simulate_discord_display(member, color.rgb)) # type: ignore
        view = ChooseColorMenu(self, interaction, colors, previews)
        await view.start()
        r = await view.wait()
        if r:
            return await interaction.followup.send("**Temps écoulé ·** Aucune couleur n'a été choisie.", ephemeral=True)
        if not view.result:
            return await interaction.followup.send("**Annulée ·** Aucune couleur n'a été choisie.", ephemeral=True)

        color = self.rgb_to_hex(view.result.rgb)
        role = await self.create_color_role(guild, request, color)
        if not role:
            return await interaction.followup.send("**Erreur ·** Une erreur s'est produite lors de la création du rôle.", ephemeral=True)

        await self.organize_color_roles(guild)

        if role not in request.roles:
            self_color_role = self.get_user_color_role(request)
            if self_color_role:
                try:
                    await request.remove_roles(self_color_role)
                except discord.Forbidden:
                    return await interaction.followup.send("**Erreur ·** Je n'ai pas la permission de vous retirer le rôle **{}**.".format(self_color_role.name), ephemeral=True)
                except discord.HTTPException:
                    return await interaction.followup.send("**Erreur ·** Une erreur s'est produite lors du retrait du rôle **{}**.".format(self_color_role.name), ephemeral=True)

            try:
                await request.add_roles(role)
            except discord.Forbidden:
                return await interaction.followup.send("**Erreur ·** Je n'ai pas la permission de vous attribuer ce rôle.", ephemeral=True)
            except discord.HTTPException:
                return await interaction.followup.send("**Erreur ·** Une erreur s'est produite lors de l'attribution du rôle.", ephemeral=True)
            
        warning = ""
        if not self.is_color_displayed(request):
            warning = "Un autre rôle coloré est plus haut dans la hiérarchie de vos rôles. Vous ne verrez pas la couleur de ce rôle tant que vous ne le retirerez pas."
            
        image = self.create_color_block(color, False)
        embed = self.color_embed(color, "Vous avez désormais le rôle **{}**{}".format(role.name, '\n\n' + warning if warning else ''))
        with BytesIO() as f:
            image.save(f, 'PNG')
            f.seek(0)
            image = discord.File(f, filename='color.png', description=f'Bloc de couleur #{color}')
            await interaction.followup.send(file=image, embed=embed)

    @app_commands.command(name="clear")
    @app_commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def clear_colors(self, interaction: discord.Interaction):
        """Efface tous les rôles qui ne sont pas attribués à un membre et réorganise les rôles restants"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("**Erreur ·** Vous devez être membre d'un serveur pour utiliser cette commande.", ephemeral=True)
        
        await interaction.response.defer()
        roles = self.get_color_roles(guild)
        if not roles:
            return await interaction.followup.send("**Erreur ·** Aucun rôle de couleur n'a été trouvé sur ce serveur.", ephemeral=True)
        
        deleted = 0
        for role in roles:
            if len(role.members) == 0:
                try:
                    await role.delete()
                except discord.Forbidden:
                    return await interaction.followup.send("**Erreur ·** Je n'ai pas la permission de supprimer le rôle **{}**.".format(role.name), ephemeral=True)
                except discord.HTTPException:
                    return await interaction.followup.send("**Erreur ·** Une erreur s'est produite lors de la suppression du rôle **{}**.".format(role.name), ephemeral=True)
                deleted += 1
                
        # Faire du rangement
        await self.organize_color_roles(guild)
        
        if deleted == 0:
            return await interaction.followup.send("**Succès ·** Aucun rôle n'a été supprimé.", ephemeral=True)
        
        await interaction.followup.send("**Succès ·** {} rôles ont été supprimés.".format(deleted))
        
    @app_commands.command(name="auto")
    @app_commands.guild_only()
    async def toggle_aac(self, interaction: discord.Interaction):
        """Activer/désactiver le changement automatique de la couleur du rôle lorsque vous changez votre avatar (sur tous les serveurs)"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("**Erreur ·** Vous devez être membre d'un serveur pour utiliser cette commande.", ephemeral=True)
        
        # Est-ce que la fonctionnalité est activée sur le serveur
        settings = self.get_guild_settings(guild)
        if int(settings['enabled']) == 0:
            return await interaction.response.send_message("**Erreur ·** Cette fonctionnalité est désactivée sur ce serveur.", ephemeral=True)
        
        current = self.get_user_aac_status(interaction.user)
        if current:
            self.set_user_aac_status(interaction.user, False)
            return await interaction.response.send_message("**Succès ·** Le changement automatique de la couleur du rôle a été __désactivé__.", ephemeral=True)
        else:
            self.set_user_aac_status(interaction.user, True)
            return await interaction.response.send_message("**Succès ·** Le changement automatique de la couleur du rôle a été __activé__.\nVotre couleur sera ajustée automatiquement quand vous changerez votre avatar avec la couleur dominante de celui-ci. Vous pouvez toujours changer votre couleur manuellement si elle ne vous convient pas.", ephemeral=True)
        
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.display_avatar == after.display_avatar:
            return
        
        settings = self.get_guild_settings(before.guild)
        if int(settings['enabled']) == 0:
            return
        
        if not before.bot:
            await self.update_user_color_role(after)
        
    @app_commands.command(name="setboundary")
    @app_commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def set_boundary(self, interaction: discord.Interaction, role: Optional[discord.Role] = None):
        """Définir un rôle comme étant un rôle balise permettant d'organiser proprement les rôles de couleurs dans la hiérarchie des rôles
        
        :param role: Rôle à définir comme rôle balise, aucun pour désactiver la fonctionnalité
        """
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message("**Erreur ·** Vous devez être membre d'un serveur pour utiliser cette commande.", ephemeral=True)
        
        if not role:
            self.set_boundary_role(guild, None)
            return await interaction.response.send_message("**Succès ·** Le rôle de balise a été désactivé.", ephemeral=True)
        
        self.set_boundary_role(guild, role)
        await interaction.response.send_message("**Succès ·** Le rôle **{}** sert désormais de balise.".format(role.name), ephemeral=True)
        
        
async def setup(bot):
    await bot.add_cog(Colorful(bot))