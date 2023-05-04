import sqlite3
import discord
from discord.ext import commands
from pathlib import Path
from typing import Union, Dict, List

OBJECT_PATH_STRUCTURE = {
        discord.User: "usr_{obj.id}",
        discord.Member: "mbr_{obj.id}_{obj.guild.id}",
        discord.Guild: "gld_{obj.id}",
        discord.TextChannel: "tch_{obj.id}"
    }

class CogData:
    """Représente l'ensemble des données d'un Cog pour faciliter leur accès"""
    def __init__(self, cog_name: str) -> None:
        self.cog_name = cog_name
        self.data_path = Path(f"cogs/{self.cog_name}")
        
    def __repr__(self) -> str:
        return f"<CogData {self.cog_name}>"
    
    def _get_assets(self) -> Path:
        path = self.data_path / "assets"
        return path
    
    @property
    def assets_path(self) -> Path:
        """Retourne le chemin vers le dossier assets du Cog

        :return: Chemin (Path) vers le dossier assets du Cog
        """
        return self._get_assets()
    
    def get_database(self, obj: Union[discord.User, discord.Member, discord.Guild, discord.TextChannel, str, int]) -> sqlite3.Connection:
        """Retourne une connexion à la base de données du Cog

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :return: Connexion à la base de données du Cog
        """
        folder_path = self.data_path / "data"
        folder_path.mkdir(parents=True, exist_ok=True)
        db_path = folder_path / f"{_get_object_db_name(obj)}.db"
        return sqlite3.connect(db_path)
    
    def get_all_databases(self) -> Dict[str, sqlite3.Connection]:
        """Retourne un dictionnaire contenant toutes les connexions aux bases de données existantes du Cog

        :return: Dictionnaire contenant toutes les connexions aux bases de données du Cog
        """
        dbs = {}
        for db in self.data_path.glob("data/*.db"):
            dbs[db.stem] = sqlite3.connect(db)
        return dbs
    
# Fonctions utilitaires

def get_cog_data(cog: commands.Cog) -> CogData:
    """Retourne un objet CogData contenant toutes les données du Cog

    :param cog: Cog dont on veut récupérer les données
    :return: Objet CogData contenant toutes les données du Cog
    """
    return CogData(cog.qualified_name)

def get_cog_data_by_name(cog_name: str) -> CogData:
    """Retourne un objet CogData contenant toutes les données du Cog

    :param cog_name: Nom du Cog dont on veut récupérer les données
    :return: Objet CogData contenant toutes les données du Cog
    """
    return CogData(cog_name)
        
def _get_object_db_name(obj: Union[discord.User, discord.Member, discord.Guild, discord.TextChannel, str, int]) -> str:
    """Retourne le nom uniformisé de la base de données liée à l'objet Discord

    :param obj: Objet Discord commun (User, Member, Guild, TextChannel) ou nom brut de l'objet
    :return: Chemin vers l'objet discord
    """
    if isinstance(obj, (str, int)):
        return str(obj)
    return OBJECT_PATH_STRUCTURE[type(obj)].format(obj=obj)
