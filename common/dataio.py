import sqlite3
import discord
from discord.ext import commands
from pathlib import Path
from typing import Union, Dict, List, Literal

OBJECT_PATH_STRUCTURE = {
        discord.User: "usr_{obj.id}",
        discord.Member: "mbr_{obj.id}_{obj.guild.id}",
        discord.Guild: "gld_{obj.id}",
        discord.TextChannel: "tch_{obj.id}",
        discord.Thread: "thr_{obj.id}",
    }

DB_TYPES = Union[discord.User, discord.Member, discord.Guild, discord.TextChannel, discord.Thread, str, int]

class CogData:
    """Représente l'ensemble des données d'un Cog"""
    def __init__(self, cog_name: str) -> None:
        self.cog_name = cog_name
        self.data_path = Path(f"cogs/{self.cog_name}")
        
        self._db_cache = {}
        
    def __repr__(self) -> str:
        return f"<CogData {self.cog_name}>"
    
    def __del__(self) -> None:
        self.close_all_databases()
    
    # Assets -------------------
    
    def _get_assets(self) -> Path:
        path = self.data_path / "assets"
        return path
    
    @property
    def assets_path(self) -> Path:
        """Retourne le chemin vers le dossier assets du Cog

        :return: Chemin (Path) vers le dossier assets du Cog
        """
        return self._get_assets()
    
    # Databases ----------------

    def _get_sqlite_conn(self, obj: DB_TYPES) -> sqlite3.Connection:
        folder = self.data_path / "data"
        folder.mkdir(parents=True, exist_ok=True)
        db_path = folder / f"{__get_object_db_name(obj)}.db"
        return sqlite3.connect(db_path)
    
    def _load_database(self, obj: DB_TYPES, enable_row_factory: bool = True) -> sqlite3.Connection:
        """Charge une base de données pour un objet discord ou en crée une si elle n'existe pas encore"""
        if obj in self._db_cache:
            return self._db_cache[obj]
        conn = self._get_sqlite_conn(obj)
        if enable_row_factory:
            conn.row_factory = sqlite3.Row
        self._db_cache[obj] = conn
        return conn
    
    def _load_existing_databases(self, enable_row_factory: bool = True) -> Dict[str, sqlite3.Connection]:
        """Charge toutes les bases de données du Cog déjà existantes"""
        dbs = {}
        for db in self.data_path.glob("data/*.db"):
            conn = sqlite3.connect(db)
            if enable_row_factory:
                conn.row_factory = sqlite3.Row
            dbs[db.stem] = conn
        return dbs

    def get_database(self, obj: DB_TYPES) -> sqlite3.Connection:
        """Retourne une base de données pour un objet discord

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :return: Connexion à la base de données du Cog
        """
        return self._load_database(obj)
    
    def get_all_databases(self) -> Dict[str, sqlite3.Connection]:
        """Retourne toutes les bases de données du Cog

        :return: Dictionnaire contenant toutes les connexions aux bases de données du Cog
        """
        return self._load_existing_databases()
    
    def close_database(self, obj: DB_TYPES) -> None:
        """Ferme une connexion à une base de données

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        """
        if obj in self._db_cache:
            self._db_cache[obj].close()
            del self._db_cache[obj]
            
    def close_all_databases(self) -> None:
        """Ferme toutes les connexions aux bases de données du Cog"""
        for conn in self._db_cache.values():
            conn.close()
        self._db_cache = {}
        
    # Operations ---------------------

    def fetchone(self, obj: DB_TYPES, query: str, *args) -> sqlite3.Row:
        """Exécute une requête SQL et retourne la première ligne du résultat

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :param query: Requête SQL à exécuter
        :param *args: Arguments de la requête SQL
        :return: Première ligne du résultat
        """
        conn = self.get_database(obj)
        cursor = conn.cursor()
        cursor.execute(query, args)
        result = cursor.fetchone()
        cursor.close()
        return result
    
    def fetchall(self, obj: DB_TYPES, query: str, *args) -> List[sqlite3.Row]:
        """Exécute une requête SQL et retourne toutes les lignes du résultat

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :param query: Requête SQL à exécuter
        :param *args: Arguments de la requête SQL
        :return: Liste des lignes du résultat
        """
        conn = self.get_database(obj)
        cursor = conn.cursor()
        cursor.execute(query, args)
        result = cursor.fetchall()
        cursor.close()
        return result
        
    def execute(self, obj: DB_TYPES, query: str, *args) -> None:
        """Exécute une requête SQL

        :param obj: Objet discord (User, Member, Guild, TextChannel) ou ID de l'objet
        :param query: Requête SQL à exécuter
        :param *args: Arguments de la requête SQL
        """
        conn = self.get_database(obj)
        cursor = conn.cursor()
        cursor.execute(query, args)
        conn.commit()
        cursor.close()
    
        
def get_cog_data(cog: Union[commands.Cog, str]) -> CogData:
    """Retourne les données d'un Cog

    :param cog: Cog ou nom du Cog
    :return: Données du Cog
    """
    name = cog if isinstance(cog, str) else cog.qualified_name
    return CogData(name.lower())

# Utils -----------------------
        
def __get_object_db_name(obj: DB_TYPES) -> str:
    """Retourne un nom de base de données normalisé à partir d'un objet discord

    :param obj: Objet Discord commun (User, Member, Guild, TextChannel) ou ID brut de l'objet
    :return: Chemin vers l'objet discord
    """
    if isinstance(obj, (str, int)):
        return str(obj)
    return OBJECT_PATH_STRUCTURE[type(obj)].format(obj=obj)
    
