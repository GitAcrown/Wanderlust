import json
import logging
from collections import namedtuple
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands
from numpy import isin
from tabulate import tabulate

from common import dataio
from common.utils import fuzzy, pretty

logger = logging.getLogger(f'Wanderlust.{__name__.capitalize()}')

DEFAULT_SETTINGS = {
    'CurrencyString': '✦',
    'AllowanceAmount': 200,
    'AllowanceLimit': 5000,
    'DefaultBalance':  100
}

TRANSACTION_EXPIRATION_DELAY = 86400 * 7 # 7 days
TRANSACTION_CLEANUP_INTERVAL = 3600 # 1 hour

class TrsHistoryView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, account: 'Account'):
        super().__init__(timeout=60)
        self.initial_interaction = interaction
        self.account = account
        
        self.transactions : List[Transaction] = account.get_transactions()
        self.current_page = 0
        self.pages : List[discord.Embed] = self.get_pages()
        
        self.previous.disabled = True
        if len(self.pages) <= 1:
            self.next.disabled = True
        
        self.message : discord.InteractionMessage
        
    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user == self.initial_interaction.user
    
    async def on_timeout(self) -> None:
        await self.message.edit(view=self.clear_items())
        
    def get_pages(self):
        embeds = []
        tabl = []
        for trs in self.transactions:
            if len(tabl) < 20:
                tabl.append((f"{trs.ftime} {trs.fdate}", f"{trs.delta:+}", f"{pretty.troncate_text(trs.description, 50)}"))
            else:
                em = discord.Embed(color=0x2b2d31, description=pretty.codeblock(tabulate(tabl, headers=("Date", "Delta", "Desc."))))
                em.set_author(name=f"Historique des transactions · {self.account.member.display_name}", icon_url=self.account.member.display_avatar.url)
                em.set_footer(text=f"{len(self.transactions)} transactions sur les {int(TRANSACTION_EXPIRATION_DELAY / 86400)} derniers jours")
                embeds.append(em)
                tabl = []
        
        if tabl:
            em = discord.Embed(color=0x2b2d31, description=pretty.codeblock(tabulate(tabl, headers=("Date", "Delta", "Desc.")))) 
            em.set_author(name=f"Historique des transactions · {self.account.member.display_name}", icon_url=self.account.member.display_avatar.url)
            em.set_footer(text=f"{len(self.transactions)} transactions sur les {int(TRANSACTION_EXPIRATION_DELAY / 86400)} derniers jours")
            embeds.append(em)
            
        return embeds
    
    async def start(self):
        if self.pages:
            await self.initial_interaction.response.send_message(embed=self.pages[self.current_page], view=self)
        else:
            await self.initial_interaction.response.send_message("Cet historique de transactions est vide.")
            self.stop()
            return self.clear_items()
        self.message = await self.initial_interaction.original_response()
        
    async def buttons_logic(self, interaction: discord.Interaction):
        self.previous.disabled = self.current_page == 0
        self.next.disabled = self.current_page + 1 >= len(self.pages)
        await interaction.message.edit(view=self) #type: ignore
        
    @discord.ui.button(label="Précédent", style=discord.ButtonStyle.blurple)
    async def previous(
        self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        await self.buttons_logic(interaction)
        await interaction.response.edit_message(embed=self.pages[self.current_page])

    @discord.ui.button(label="Suivant", style=discord.ButtonStyle.blurple)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        await self.buttons_logic(interaction)
        await interaction.response.edit_message(embed=self.pages[self.current_page])
    
    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.message.delete()

class Transaction:
    """Représente une transaction bancaire"""
    def __init__(self, cog: 'Economy', account: 'Account', delta: int, description: str, **extras) -> None:
        self.cog = cog
        self.account = account
        self.delta = delta
        self.description = description
        self.timestamp = datetime.now().timestamp()
        self.extras : Dict[str, Any] = extras
        
        self.id = self.__generate_id()
        self.expire_at = self.timestamp + TRANSACTION_EXPIRATION_DELAY
        
    def __repr__(self) -> str:
        return f'<Transaction {self.id!r}>'
    
    def __str__(self) -> str:
        return f"{self.delta:+} {self.description}"
    
    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Transaction):
            return self.id == other.id
        return NotImplemented
        
    def __generate_id(self) -> str:
        raw_id = int(f'{self.account.member.id}{int(self.timestamp)}')
        return hex(raw_id)
    
    def save(self) -> None:
        query = """INSERT OR REPLACE INTO transactions (id, timestamp, delta, description, member_id, extras) VALUES (?, ?, ?, ?, ?, ?)"""
        self.cog.data.execute(self.account.member.guild, query, (self.id, self.timestamp, self.delta, self.description, self.account.member.id, json.dumps(self.extras, ensure_ascii=False)))
        
        # Remove expired transactions
        self.cog.delete_expired_transactions(self.account.member.guild)

    @classmethod
    def load(cls, cog: 'Economy', guild: discord.Guild, data: Dict[str, Any]) -> 'Transaction':
        member = guild.get_member(data['member_id'])
        if not member:
            raise ValueError(f"Member {data['member_id']} not found in guild {guild}")
        account = Account(cog, member)
        if type(data.get('extras')) == str:
            data['extras'] = json.loads(data['extras'])
        return cls(cog, account, data['delta'], data['description'], **data['extras'])
    
    # Properties --------------------------------------------------------------
    
    @property
    def ftime(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime('%H:%M:%S')
    
    @property
    def fdate(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime('%d/%m/%Y')
    
    @property
    def expired(self) -> bool:
        return self.timestamp >= self.expire_at
    

class Account:
    """Représente un compte bancaire"""
    def __init__(self, cog: 'Economy', member: discord.Member) -> None:
        self.cog = cog
        self.member = member
        self.guild = member.guild
        
        self.__initiate_account()
        
    def __repr__(self) -> str:
        return f'<Account {self.member!r}>'
    
    def __str__(self) -> str:   
        return str(self.member)
    
    def __int__(self) -> int:
        return self.balance
    
    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Account):
            return self.member == other.member
        return NotImplemented
        
    def __initiate_account(self) -> None:
        guild_default = self.cog._get_settings(self.guild, 'DefaultBalance')
        self.cog.data.execute(self.member.guild, """INSERT OR IGNORE INTO accounts VALUES (?, ?)""", (self.member.id, int(guild_default)))
    
    # Balance -----------------------------------------------------------------
    def _get_balance(self) -> int:
        return int(self.cog.data.fetchone(self.member.guild, """SELECT balance FROM accounts WHERE member_id = ?""", (self.member.id,))['balance'])
    
    def _set_balance(self, value: int, *, description: str, **extras) -> Transaction:
        current_balance = self.balance
        if value < 0:
            raise ValueError("Cannot set balance to a negative value")

        delta = value - current_balance
        self.cog.data.execute(self.member.guild, """UPDATE accounts SET balance = ? WHERE member_id = ?""", (value, self.member.id))
        return Transaction(self.cog, self, delta, description, **extras)
        
    @property
    def balance(self) -> int:
        return self._get_balance()
        
    def set(self, value: int, *, reason: str, **extras) -> Transaction:
        """Définir le solde du compte"""
        return self._set_balance(value, description=reason, **extras)
    
    def deposit(self, value: int, *, reason: str, **extras) -> Transaction:
        """Ajouter des crédits au compte"""
        return self._set_balance(self.balance + value, description=reason, **extras)
    
    def withdraw(self, value: int, *, reason: str, **extras) -> Transaction:
        """Retirer des crédits du compte"""
        return self._set_balance(self.balance - value, description=reason, **extras)
    
    def reset(self, *, reason: str, **extras) -> Transaction:
        """Réinitialiser le solde du compte"""
        default = self.cog._get_settings(self.guild, 'DefaultBalance')
        return self._set_balance(default, description=reason, **extras) # type: ignore
    
    def check(self, value: int) -> bool:
        """Vérifier si le compte a assez de crédits"""
        return self.balance >= value
    
    # Transactions ------------------------------------------------------------
    
    def cancel_transaction(self, transaction: Transaction, *, reason: str, **extras) -> Transaction:
        """Annule la transaction, restaure le solde précédent et renvoie une nouvelle transaction correspondant à la correction"""
        current_balance = self.balance
        new_trs = self._set_balance(current_balance - transaction.delta, description=reason, **extras)
        new_trs.extras['original_transaction'] = transaction.id
        return new_trs
    
    def get_transactions(self, limit: int = 10) -> List[Transaction]:
        """Renvoie les dernières transactions du compte"""
        query = """SELECT * FROM transactions WHERE member_id = ? ORDER BY timestamp DESC LIMIT ?"""
        return [Transaction.load(self.cog, self.member.guild, dict(data)) for data in self.cog.data.fetchall(self.member.guild, query, (self.member.id, limit))]

    # Utils -------------------------------------------------------------------
    
    def balance_variation(self, since: datetime | float) -> int:
        """Renvoie la variation du solde depuis une date"""
        if isinstance(since, datetime):
            since = since.timestamp()
        query = """SELECT SUM(delta) FROM transactions WHERE member_id = ? AND timestamp >= ?"""
        r = self.cog.data.fetchone(self.member.guild, query, (self.member.id, since))['SUM(delta)']
        return int(r) if r else 0

    def embed(self) -> discord.Embed:
        """Renvoie un embed représentant le compte"""
        em = discord.Embed(title=f"Compte Bancaire · *{self.member.display_name}*", color=0x2b2d31)
        em.add_field(name="Solde", value=pretty.codeblock(str(self.__int__())))

        balancevar = self.balance_variation(datetime.now() - timedelta(hours=24))
        em.add_field(name="Var. 24h", value=pretty.codeblock(f'{balancevar:+}', lang='diff'))
        
        lb = self.cog.data.fetchone(self.member.guild, """SELECT COUNT(*) FROM accounts WHERE balance > ?""", (self.balance,))['COUNT(*)']
        if not lb:
            lb = 1
        em.add_field(name="Rang", value=pretty.codeblock(f'#{lb}'))
        
        transactions = self.get_transactions()
        if transactions:
            txt = '\n'.join([f'{tr.delta:+} · {pretty.troncate_text(tr.description, 50)}' for tr in transactions][:5])
            em.add_field(name="Dernières transactions", value=pretty.codeblock(txt, lang='diff'), inline=False)
    
        em.set_thumbnail(url=self.member.display_avatar.url)
        return em

class Rule:
    """Représente une règle ou limitation d'action"""
    def __init__(self, cog: 'Economy', target: Union[discord.Member, discord.TextChannel, discord.Thread], name: str, value: Any) -> None:
        self.cog = cog
        self.target = target
        self.name = name
        self.value = value
        
        self.id = f'{self.target.id}@{self.name}'
        self.__initiate_rule()

    def __repr__(self) -> str:
        return f'<Rule {self.id}>'
    
    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Rule):
            return self.id == other.id
        return NotImplemented
    
    def __hash__(self) -> int:
        return hash(self.id)
    
    def __initiate_rule(self):
        self.cog.data.execute(self.target.guild, """INSERT OR IGNORE INTO rules (id, value) VALUES (?, ?)""", (self.id, self.value))

    def save(self):
        """Met à jour la règle dans la base de données"""
        self.cog.data.execute(self.target.guild, """UPDATE rules SET value = ? WHERE id = ?""", (self.value, self.id))
        
    def delete(self):
        """Supprime la règle de la base de données"""
        self.cog.data.execute(self.target.guild, """DELETE FROM rules WHERE id = ?""", (self.id,))
        
    @classmethod
    def load(cls, cog: 'Economy', target: Union[discord.Member, discord.TextChannel, discord.Thread], name: str, default_value: Any) -> 'Rule':
        r = cog.data.fetchone(target.guild, """SELECT * FROM rules WHERE id = ?""", (f'{target.id}@{name}',))
        if r:
            # Convertir automatiquement la valeur en fonction du type de la valeur par défaut
            value = type(default_value)(r['value'])
            return cls(cog, target, name, value)
        return cls(cog, target, name, default_value)
        
class Economy(commands.Cog):
    """Module central de gestion de l'économie"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
        self.bankaccount_menu = app_commands.ContextMenu(
            name='Compte bancaire',
            callback=self.ctx_account_info
        )
        self.bot.tree.add_command(self.bankaccount_menu)
        
        self.last_cleanup = 0
        
    def _init_guilds_db(self, guild: discord.Guild | None = None):
        guilds = [guild] if guild else self.bot.guilds
        accounts = """CREATE TABLE IF NOT EXISTS accounts (
            member_id INTEGER PRIMARY KEY,
            balance INTEGER CHECK (balance >= 0)
            )"""
        transactions = """CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            timestamp REAL,
            delta INTEGER,
            description TEXT,
            member_id INTEGER,
            extras TEXT,
            FOREIGN KEY (member_id) REFERENCES accounts (member_id)
            )"""
        rules = """CREATE TABLE IF NOT EXISTS rules (
            id TEXT PRIMARY KEY,
            value TEXT
            )"""
        settings = """CREATE TABLE IF NOT EXISTS settings (
            name TEXT PRIMARY KEY,
            value TEXT
            )"""
        for g in guilds:
            self.data.execute(g, accounts, commit=False)
            self.data.execute(g, transactions, commit=False)
            self.data.execute(g, rules, commit=False)
            self.data.execute(g, settings, commit=False)
            self.data.commit(g)
            
            self.data.executemany(g, """INSERT OR IGNORE INTO settings (name, value) VALUES (?, ?)""", DEFAULT_SETTINGS.items())
        
    @commands.Cog.listener()
    async def on_ready(self):
        self._init_guilds_db()
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._init_guilds_db(guild)
        
    # DataIO functions --------------------------------------------------------
    
    def cog_unload(self):
        self.data.close_all_databases()
        
    def dataio_list_user_data(self, user_id: int) -> List[dataio.UserDataEntry]:
        data = []
        guilds = self.bot.guilds
        for guild in guilds:
            userdata = self.data.fetchone(guild, """SELECT * FROM accounts WHERE member_id = ?""", (user_id,))
            if userdata:
                data.append(dataio.UserDataEntry(user_id, f'accounts:{guild.id}', f"Compte bancaire sur '{guild.name}'", importance_level=2))
        return data
    
    def dataio_wipe_user_data(self, user_id: int, table_name: str) -> bool:
        if table_name.startswith('accounts:'):
            guild_id = int(table_name.split(':')[1])
            self.data.execute(f'gld_{guild_id}', """DELETE FROM accounts WHERE member_id = ?""", (user_id,))
            return True
        return False
    
    def dataio_extract_user_data(self, user_id: int, table_name: str) -> Optional[dict]:
        if table_name.startswith('accounts:'):
            guild_id = int(table_name.split(':')[1])
            userdata = self.data.fetchone(f'gld_{guild_id}', """SELECT * FROM accounts WHERE member_id = ?""", (user_id,))
            if userdata:
                return {'balance': userdata['balance']}
        return None
            
    # Settings -----------------------------------------------------------------
    
    def _get_settings(self, guild: discord.Guild, name: str | None = None) -> Union[dict, Any]:
        """Renvoie la valeur d'un paramètre de configuration ou tous les paramètres de configuration"""
        if name is None:
            r = self.data.fetchall(guild, "SELECT * FROM settings")
            return {row['name']: row['value'] for row in r}
        else:
            return self.data.fetchone(guild, "SELECT value FROM settings WHERE name = ?", (name,))['value']
    
    def _set_settings(self, guild: discord.Guild, name: str, value: Any) -> None:
        """Modifie la valeur d'un paramètre de configuration"""
        self.data.execute(guild, "INSERT OR REPLACE INTO settings VALUES (?, ?)", (name, value))
    
    def get_currency_string(self, guild: discord.Guild) -> str:
        """Renvoie le symbole de la monnaie"""
        return str(self._get_settings(guild, 'CurrencyString'))
    
    # Accounts -----------------------------------------------------------------
    
    def get_account(self, member: discord.Member) -> Account:
        """Renvoie le compte bancaire d'un membre"""
        return Account(self, member)
    
    def get_accounts(self, guild: discord.Guild) -> List[Account]:
        """Renvoie les comptes bancaires de tous les membres"""
        query = """SELECT * FROM accounts"""
        members = {m.id: m for m in guild.members}
        datam = self.data.fetchall(guild, query)
        return [Account(self, members[data['member_id']]) for data in datam]
    
    # Guild economy ------------------------------------------------------------
    
    def get_guild_total_balance(self, guild: discord.Guild) -> int:
        """Renvoie la somme des soldes de tous les membres"""
        accounts = self.get_accounts(guild)
        return sum([a.balance for a in accounts])
    
    def get_guild_richest(self, guild: discord.Guild) -> Account:
        """Renvoie le membre le plus riche"""
        accounts = self.get_accounts(guild)
        return max(accounts, key=lambda a: a.balance)
    
    def get_guild_poorest(self, guild: discord.Guild) -> Account:
        """Renvoie le membre le plus pauvre"""
        accounts = self.get_accounts(guild)
        return min(accounts, key=lambda a: a.balance)
    
    def get_guild_average(self, guild: discord.Guild) -> int:
        """Renvoie la moyenne des soldes"""
        accounts = self.get_accounts(guild)
        return sum([a.balance for a in accounts]) // len(accounts)
    
    def get_guild_median(self, guild: discord.Guild) -> int:
        """Renvoie la médiane des soldes"""
        accounts = self.get_accounts(guild)
        balances = [a.balance for a in accounts]
        balances.sort()
        return balances[len(balances) // 2]
    
    def get_guild_leaderboard(self, guild: discord.Guild) -> List[Account]:
        """Renvoie la liste des comptes triés par solde décroissant"""
        accounts = self.get_accounts(guild)
        return sorted(accounts, key=lambda a: a.balance, reverse=True)
    
    def guild_currency(self, guild: discord.Guild) -> str:
        """Renvoie le symbole de la monnaie du serveur"""
        return self._get_settings(guild, 'CurrencyString')
    
    # Cleanups -----------------------------------------------------------------
    
    def delete_expired_transactions(self, guild: discord.Guild) -> None:
        """Supprime les transactions expirées"""
        if self.last_cleanup + TRANSACTION_CLEANUP_INTERVAL > datetime.now().timestamp():
            return
        
        expire_threshold = datetime.now().timestamp() - TRANSACTION_EXPIRATION_DELAY
        self.data.execute(guild, """DELETE FROM transactions WHERE timestamp < ?""", (expire_threshold,))
        self.last_cleanup = datetime.now().timestamp()
    
    # COMMANDES ================================================================
    
    bank_commands = app_commands.Group(name='bank', description="Gestion de votre compte bancaire", guild_only=True)
    
    @bank_commands.command(name='account')
    @app_commands.rename(member='membre')
    async def _bank_account(self, interaction: discord.Interaction, member: discord.Member | None = None):
        """Affiche votre compte bancaire
        
        :param member: Membre à afficher (vous par défaut)
        """
        user = member or interaction.user
        account = self.get_account(user)
        await interaction.response.send_message(embed=account.embed())
        
    async def ctx_account_info(self, interaction: discord.Interaction, member: discord.Member):
        """Menu contextuel permettant l'affichage du compte bancaire virtuel d'un membre

        :param member: Utilisateur visé par la commande
        """
        account = self.get_account(member)
        await interaction.response.send_message(embed=account.embed(), ephemeral=True)
    
    @bank_commands.command(name='history')
    @app_commands.rename(member='membre')
    async def _bank_history(self, interaction: discord.Interaction, member: discord.Member | None = None):
        """Affiche l'historique de votre compte bancaire

        :param member: Membre à afficher (vous par défaut)
        """
        user = member or interaction.user
        account = self.get_account(user)
        await TrsHistoryView(interaction, account).start()
        
    @bank_commands.command(name='transfer')
    @app_commands.rename(member='membre', amount='montant', reason='raison')
    async def _bank_transfer(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1], reason: str | None = ''):
        """Transférer des crédits virtuels à un membre

        :param member: Membre à créditer
        :param amount: Montant à créditer
        :param reason: Raison du crédit (facultatif)
        """
        receiver = self.get_account(member)
        sender = self.get_account(interaction.user)
        if receiver == sender:
            return await interaction.response.send_message("Vous ne pouvez pas vous donner de l'argent à vous-même !", ephemeral=True)
        
        currency = self.guild_currency(interaction.guild)
        try:
            strs = sender.withdraw(amount, reason=f"Don à {member.display_name}")
        except ValueError:
            return await interaction.response.send_message(f"**Solde insuffisant.**\nVous n'avez pas assez de crédits pour effectuer ce don", ephemeral=True)
        else:
            rtrs = receiver.deposit(amount, reason=f"Don de {interaction.user.display_name}" if not reason else f"{sender.member} » {reason}")
            rtrs.extras['linked_transaction'] = strs.id
            strs.extras['linked_transaction'] = rtrs.id
            rtrs.save()
            strs.save()
            
            await interaction.response.send_message(f"**Don effectué !**\nVous avez donné {pretty.humanize_number(amount)}{currency} à {member.mention}" + (f"\n**Raison :** *{reason}*" if reason else ""))

    @bank_commands.command(name='allowance')
    async def _bank_allowance(self, interaction: discord.Interaction):
        """Obtenir votre allocation quotidienne si vous êtes éligible"""
        account = self.get_account(interaction.user)
        settings = self._get_settings(interaction.guild)
        currency = self.guild_currency(interaction.guild)
        
        # Calcul de l'allocation
        amount, limit = int(settings['AllowanceAmount']), int(settings['AllowanceLimit'])
        if amount <= 0 or limit <= 0:
            return await interaction.response.send_message("**Fonctionnalité désactivée.**\nL'allocation quotidienne est désactivée sur ce serveur", ephemeral=True)
        
        if account.balance >= limit:
            return await interaction.response.send_message(f"**Solde trop élevé !**\nVous avez déjà atteint le montant maximum pour recevoir une allocation quotidienne ({pretty.humanize_number(limit)}{currency})", ephemeral=True)
        
        # Vérification de la dernière allocation
        today = datetime.now().strftime('%Y-%m-%d')
        last = Rule.load(self, interaction.user, 'LastAllowance', default_value='')
        if last.value == today:
            return await interaction.response.send_message(f"**Allocation déjà reçue.**\nVous avez déjà reçu votre allocation quotidienne aujourd'hui", ephemeral=True)
            
        redux = account.balance / limit
        amount = round(amount * (1 - redux))
        if amount <= 0:
            return await interaction.response.send_message(f"**Solde trop élevé !**\nL'aide auquel vous avez le droit est trop petite pour vous être versée", ephemeral=True)
        
        account.deposit(amount, reason="Allocation quotidienne").save()
        last.value = today
        last.save()
        await interaction.response.send_message(f"**Allocation quotidienne versée.**\nVous avez reçu {pretty.humanize_number(amount)}{currency} d'aide au titre de l'allocation quotidienne du serveur !")
        
    bankmod = app_commands.Group(name='bankmod', description="Commandes de modération bancaire", guild_only=True, default_permissions=discord.Permissions(manage_messages=True))
    
    @bankmod.command(name='setbalance')
    @app_commands.rename(member='membre', amount='montant')
    async def _bankmod_setbalance(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        """Modifie manuellement le solde d'un membre

        :param member: Membre à modifier
        :param amount: Nouveau solde
        """
        account = self.get_account(member)
        account.set(amount, reason="Modification manuelle").save()
        await interaction.response.send_message(f"**Solde modifié.**\nLe solde de {member.mention} a été modifié à {pretty.humanize_number(amount)}{self.guild_currency(interaction.guild)}")
    
    @bankmod.command(name='reset')
    @app_commands.rename(member='membre')
    async def _bankmod_reset(self, interaction: discord.Interaction, member: discord.Member):
        """Réinitialise le solde d'un membre

        :param member: Membre à réinitialiser
        """
        account = self.get_account(member)
        account.reset(reason="Réinitialisation manuelle").save()
        await interaction.response.send_message(f"**Solde réinitialisé.**\nLe solde de {member.mention} a été réinitialisé")
        
    @bankmod.command(name='settings')
    @app_commands.rename(name='nom', value='valeur')
    async def _bankmod_settings(self, interaction: discord.Interaction, name: str, value: str):
        """Modifier les paramètres de la banque
        
        :param name: Nom du paramètre à modifier
        :param value: Nouvelle valeur du paramètre
        """
        if name not in self._get_settings(interaction.guild):
            return await interaction.response.send_message(f"**Paramètre inconnu.**\nLe paramètre `{name}` n'existe pas", ephemeral=True)
        
        try:
            self._set_settings(interaction.guild, name, value)
        except ValueError as e:
            return await interaction.response.send_message(f"**Erreur lors de la modification de la valeur :**\n`{e}`", ephemeral=True)
        
        await interaction.response.send_message(f"**Paramètre modifié.**\nLe paramètre `{name}` a été modifié par `{value}`")
        
    @_bankmod_settings.autocomplete('name')
    async def autocomplete_callback(self, interaction: discord.Interaction, current: str):
        settings = self._get_settings(interaction.guild).items()
        stgs = fuzzy.finder(current, settings, key=lambda s: s[0])
        return [app_commands.Choice(name=f'{s[0]} [={s[1]}]', value=s[0]) for s in stgs]
        
    @app_commands.command(name='leaderboard')
    @app_commands.guild_only()
    async def _leaderboard(self, interaction: discord.Interaction):
        """Affiche le classement des membres les plus riches et d'autres statistiques"""
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en DM", ephemeral=True)
        
        lb = self.get_guild_leaderboard(interaction.guild)
        if not lb:
            return await interaction.response.send_message("Aucun membre n'a de compte bancaire sur ce serveur", ephemeral=True)
        
        average = self.get_guild_average(interaction.guild)
        median = self.get_guild_median(interaction.guild)
        currency = self.guild_currency(interaction.guild)
        
        embed = discord.Embed(title="Classement des membres les plus riches", color=0x2b2d31)
        chunks = []
        for i in enumerate(lb[:20], start=1):
            chunks.append((i[0], i[1].member.name, i[1].balance))
        embed.description = pretty.codeblock(tabulate(chunks, headers=['#', 'Membre', 'Solde']))
        embed.set_footer(text=f"Solde moyen : {pretty.humanize_number(average)}{currency} | Solde médian : {pretty.humanize_number(median)}{currency}\nTotal en circulation : {pretty.humanize_number(self.get_guild_total_balance(interaction.guild))}{currency}")
        
        await interaction.response.send_message(embed=embed)
        
async def setup(bot):
    cog = Economy(bot)
    await bot.add_cog(cog)