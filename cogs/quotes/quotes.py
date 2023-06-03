from datetime import datetime
import logging
import random
from io import BytesIO
from typing import Optional, Union, Tuple, List, Literal
import colorgram
import textwrap
import re
import aiohttp
import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from common import dataio

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

EXTRACT_COLOR_LIMIT = 5

# Menu Quotify ----------------------------------------------------------------

class Quotify_SelectBox(discord.ui.Select):
    def __init__(self, editor: 'QuotifyEditor', placeholder: str, options: List[discord.SelectOption]):
        super().__init__(placeholder=placeholder, 
                         min_values=1, 
                         max_values=len(options), 
                         options=options)
        self.editor = editor

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.editor.selected = sorted([self.editor.original_message] + [m for m in self.editor.potential_messages if str(m.id) in self.values], key=lambda m: m.created_at)
        self.options = [discord.SelectOption(label=textwrap.shorten(m.clean_content, 100, placeholder='...'), value=str(m.id), description=f"Posté le {m.created_at.strftime('%d/%m/%Y à %H:%M:%S')}", default=str(m.id) in self.values) for m in self.editor.all_messages]
        await self.editor._send_update()

class QuotifyEditor(discord.ui.View):
    def __init__(self, cog: 'Quotes', selected_message: discord.Message, potential_messages: List[discord.Message], *, timeout: Optional[float] = 180):
        super().__init__(timeout=timeout)
        self._cog = cog
        
        self.original_message = selected_message
        self.selected = [selected_message]
        self.potential_messages = potential_messages
        self.all_messages = sorted([selected_message] + potential_messages, key=lambda m: m.created_at)
        
        self.interaction : Optional[discord.Interaction] = None
        
        self.color_index = 1
        self.text_color : Literal['white', 'black'] = 'white'
        
        if potential_messages:
            self.select_msgs = Quotify_SelectBox(self, "Sélectionnez des messages à ajouter", [discord.SelectOption(label=textwrap.shorten(m.clean_content, 100, placeholder='...'), value=str(m.id), description=f"Posté le {m.created_at.strftime('%d/%m/%Y à %H:%M')}", default=True if m == selected_message else False) for m in self.all_messages])
            self.add_item(self.select_msgs)
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.interaction.user.id: #type: ignore
            await interaction.response.send_message("**Erreur de l'interaction**\nSeul l'auteur de la commande peut utiliser ce menu.", ephemeral=True)
            return False
        return True
        
    async def start(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.gradient_colors = self._cog._get_image_colors(BytesIO(await self.original_message.author.display_avatar.read()), EXTRACT_COLOR_LIMIT)
        # Si la couleur de base du dégradé est trop claire, on inverse le texte en noir
        color = self.gradient_colors[self.color_index].rgb
        if color[0] + color[1] + color[2] > 255 * 1.5:
            self.text_color = 'black'
        try:
            image = await self._cog.create_quote_img(self.selected, self.color_index, self.gradient_colors, self.text_color)
        except Exception as e:
            logger.exception("Error while creating quote image", exc_info=True)
            return await interaction.followup.send(f"Une erreur est survenue dans la génération de l'image : `{e}`")
        await interaction.followup.send(view=self, file=image)
        self.interaction = interaction

    async def on_timeout(self) -> None:
        view = discord.ui.View()
        msgurl = self.selected[0].jump_url
        view.add_item(discord.ui.Button(label="Source", url=msgurl, style=discord.ButtonStyle.link))
        if not self.interaction:
            return
        await self.interaction.edit_original_response(content='', view=view)
        
    async def _send_update(self):
        if not self.interaction:
            return
        try:
            image = await self._cog.create_quote_img(self.selected, self.color_index, self.gradient_colors, self.text_color)
        except Exception as e:
            return await self.interaction.edit_original_response(content=f"Une erreur est survenue dans la génération de l'image : `{e}`")
        await self.interaction.edit_original_response(view=self, attachments=[image])
        
    @discord.ui.button(label="Inverser texte", style=discord.ButtonStyle.grey)
    async def change_text_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.text_color = 'black' if self.text_color == 'white' else 'white'
        await self._send_update()
        
    @discord.ui.button(label="Changer dégradé", style=discord.ButtonStyle.blurple)
    async def change_gradient_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.color_index = self.color_index + 1 if self.color_index < EXTRACT_COLOR_LIMIT else 0
        await self._send_update()
    
    @discord.ui.button(emoji='<:save:1084949130096431225>', style=discord.ButtonStyle.green)
    async def save_quit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.interaction:
            return
        await interaction.response.defer()
        view = discord.ui.View()
        msgurl = self.selected[0].jump_url
        view.add_item(discord.ui.Button(label="Source", url=msgurl, style=discord.ButtonStyle.link))
        await self.interaction.edit_original_response(content='', view=view)
        
# MODULE ======================================================================

class Quotes(commands.Cog):
    """Créez vos propres citations ou obtenez-en des aléatoires depuis Inspirobot !"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
        self.quotify = app_commands.ContextMenu(
            name='Quotifier',
            callback=self.quotify_ctx_menu
        )
        self.bot.tree.add_command(self.quotify)
    
    @app_commands.command(name='quote')
    @app_commands.checks.cooldown(1, 600)
    async def get_inspirobot_quote(self, interaction: discord.Interaction):
        """Obtenez une citation aléatoire depuis Inspirobot"""
        await interaction.response.defer()
    
        async def fetch_inspirobot_quote():
            async with aiohttp.ClientSession() as session:
                async with session.get("http://inspirobot.me/api?generate=true") as page:
                    return await page.text()
                
        img = await fetch_inspirobot_quote()
        if not img:
            return await interaction.followup.send("Impossible d'obtenir une image depuis Inspirobot.me", ephemeral=True)
        
        em = discord.Embed(color=0x2F3136)
        em.set_image(url=img)
        em.set_footer(text="Obtenue depuis Inspirobot.me")
        await interaction.followup.send(embed=em)
        
        
    # QUOTIFY ------------------------------------------------------------------
    
    def _get_image_colors(self, imgbin: BytesIO, n: int) -> List[colorgram.Color]:
        image = Image.open(imgbin)
        return colorgram.extract(image, n)
     
    def _get_quote_img(self, background: Union[str, BytesIO], text: str, author_text: str, date_text: str, *, possible_colors: List[colorgram.Color], gradient_index: int, textcolor: Literal['white', 'black']) -> Image.Image:
        if len(text) > 500:
            raise ValueError("La longueur du texte doit être inférieure à 500 caractères")
        if len(author_text) > 32:
            raise ValueError("La longueur du texte de l'auteur doit être inférieure à 32 caractères")
        if gradient_index > len(possible_colors):
            raise ValueError("L'index de la couleur du dégradé doit être inférieur au nombre de couleurs possibles")
        
        img = Image.open(background)
        w, h = (512, 512)
        bw, bh = (w - 20, h - 74)
        img = img.convert("RGBA").resize((w, h))
        fontname = "NotoBebasNeue.ttf"
        fontfile = str(self.data.assets_path / fontname)

        gradient_color = possible_colors[gradient_index].rgb
        gradient_magnitude = 0.85 + 0.05 * (len(text) / 100)
        img = self._add_gradient(img, gradient_magnitude, gradient_color)
        font = ImageFont.truetype(fontfile, 56, encoding='unic')
        author_font = ImageFont.truetype(fontfile, 26, encoding='unic')
        draw = ImageDraw.Draw(img)
            
        wrapwidth = int(bw / font.getlength(' ') + (0.02 * len(text)))
        wrap = textwrap.fill(text, width=wrapwidth, placeholder='…', replace_whitespace=False, max_lines=8)
        box = draw.multiline_textbbox((0, 0), wrap, font=font, align='center')
        while box[2] > bw or box[3] > bh:
            font = ImageFont.truetype(fontfile, font.size - 2)
            box = draw.multiline_textbbox((0, 0), wrap, font=font, align='center')

        draw.multiline_text((w/2, bh), wrap, font=font, align='center', fill=textcolor, anchor='md') 
        draw.text((w/2, h - 30), author_text, font=author_font, fill=textcolor, anchor='md')
        
        # Ajouter le texte de la date en dessous de l'auteur
        date_font = ImageFont.truetype(fontfile, 17, encoding='unic')
        draw.text((w/2, h - 13), date_text, font=date_font, fill=textcolor, anchor='md')
        
        # Ajouter une fine ligne de largeur fixe entre le texte de citation et l'auteur
        draw.line((w/2 - 64, h - 70, w/2 + 64, h - 70), fill=textcolor, width=1)
        
        return img

    def _add_gradient(self, image: Image.Image, gradient_magnitude=1.0, color: Tuple[int, int, int]=(0, 0, 0)):
        im = image
        if im.mode != 'RGBA':
            im = im.convert('RGBA')
        width, height = im.size
        gradient = Image.new('L', (1, height), color=0xFF)
        for x in range(height):
            gradient.putpixel((0, x), int(255 * (gradient_magnitude * float(x)/(width))))
        
        alpha = gradient.resize(im.size)
        black_im = Image.new('RGBA', (width, height), color=color) # i.e. black
        black_im.putalpha(alpha)
        gradient_im = Image.alpha_composite(im, black_im)
        return gradient_im
    
    async def create_quote_img(self, messages: List[discord.Message], gradient_index: int, gradient_possible_colors: List[colorgram.Color], text_color: Literal['white', 'black']) -> discord.File:
        """Crée une image de citation à partir d'un ou plusieurs message(s) (v2)"""
        messages = sorted(messages, key=lambda m: m.created_at)
        user_avatar = BytesIO(await messages[0].author.display_avatar.read())
        message_date = messages[0].created_at.strftime('%d/%m/%Y')
        content = ' '.join(self.parse_emojis(m.clean_content) for m in messages)
        try:
            image = self._get_quote_img(user_avatar, f"“{content}”", messages[0].author.name, message_date, possible_colors=gradient_possible_colors, gradient_index=gradient_index, textcolor=text_color)
        except:
            raise
        with BytesIO() as buffer:
            image.save(buffer, format='PNG')
            buffer.seek(0)
            desc = f"'{content}'\n{messages[0].author.name}, {message_date}"
            return discord.File(buffer, filename=f"quote_{'_'.join([str(m.id) for m in messages])}.png", description=desc)
        
    async def get_following_messages(self, channel: Union[discord.TextChannel, discord.Thread], message: discord.Message) -> List[discord.Message]:
        """Récupère les 3 messages potentiels suivants à partir du message donné"""
        following = []
        async for m in channel.history(limit=10, after=message.created_at):
            if not m.content or m.content.isspace():
                continue
            if m.author == message.author and len(following) < 3:
                following.append(m)
            elif len(following) >= 3:
                break
        
        return sorted(following, key=lambda m: m.created_at)
    
    def parse_emojis(self, text: str) -> str:
        """Remplace les emojis par leur nom"""
        return re.sub(r'<a?:(\w+):\d+>', r':\1:', text)
        
    async def quotify_ctx_menu(self, interaction: discord.Interaction, message: discord.Message):
        """Menu contextuel permettant de créer une citation imagée à partir d'un ou plusieurs messages"""
        if not message.content or message.content.isspace():
            return await interaction.response.send_message("Le message ne contient pas de texte", ephemeral=True)
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("Le message doit être dans un salon de discussion", ephemeral=True)
        try:
            potential = await self.get_following_messages(message.channel, message)
            await QuotifyEditor(self, message, potential, timeout=30).start(interaction)
        except commands.BadArgument as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        

async def setup(bot):
    await bot.add_cog(Quotes(bot))