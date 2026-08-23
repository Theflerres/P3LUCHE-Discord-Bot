"""
Comandos administrativos exclusivos do dono do bot (Criador).

Fase 7 (camada admin completa): expande a base da etapa anterior (só
/admin economia consultar|corrigir) com dar/remover/resetar economia,
reset global de jogadores, migração dos 5 comandos de debug que estavam
soltos em sistema.py (corrigindo a falta de checagem de permissão do
antigo /admin_quest e a lista de IDs hardcoded do antigo /admin_fix_time)
e uma ferramenta genérica de inspeção somente-leitura.

Autorização: is_bot_owner() (permissions.py) é a ÚNICA fonte de verdade —
nenhum comando aqui deve reimplementar checagem de dono inline. Decisão de
design confirmada: "dono do bot" para toda a camada admin é SOMENTE o
Criador (CREATOR_ID) — nada de lista estendida de IDs.
default_permissions(administrator=True) no grupo é só redução de ruído na
UI do Discord (escondido de quem não é admin do servidor); não substitui
a checagem de backend.
"""
import asyncio
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from cogs.backup import _upload_db_sync
from config import (
    CATCHES_LOCK,
    CATCHES_SINCE_RESTART,
    CATCHES_TTL_SECONDS,
    DB_PATH,
    get_bot_instance,
)
from economy_db import (
    get_wallet,
    has_account,
    modify_wallet,
    reset_all_players,
    reset_player_progress,
    set_inventory_item,
)
from permissions import is_bot_owner
from utils import log_to_gui

# Mesmo canal de auditoria já usado por cogs/logs.py (SistemaLogs) — reaproveita
# o padrão, não duplica um segundo canal para o mesmo propósito.
AUDIT_CHANNEL_ID = 1489330740876284206

# Tabelas liberadas para /admin debug inspecionar. Nome de tabela nunca pode
# vir direto do usuário para dentro de SQL (não dá pra parametrizar
# identificador) — por isso só o que está nesta lista (via app_commands.Choice,
# que já restringe a entrada) pode ser interpolado na query.
_INSPECTABLE_TABLES = [
    "users",
    "user_inventory",
    "user_rods",
    "rod_upgrades",
    "user_trap",
    "user_cooldowns",
    "quest_progress",
    "user_islands",
    "user_island_structures",
    "achievements",
    "economy",
]


def _cleanup_stale_catches() -> None:
    now = time.time()
    expired = [uid for uid, (_, ts) in CATCHES_SINCE_RESTART.items() if now - ts > CATCHES_TTL_SECONDS]
    for uid in expired:
        CATCHES_SINCE_RESTART.pop(uid, None)


async def _send_audit_log(bot, embed: discord.Embed) -> None:
    """Envia um embed de auditoria para AUDIT_CHANNEL_ID, com o mesmo
    fallback gracioso (cache -> fetch_channel -> desiste) já usado em
    SistemaLogs.enviar_log (cogs/logs.py)."""
    canal = bot.get_channel(AUDIT_CHANNEL_ID)
    if canal is None:
        try:
            canal = await bot.fetch_channel(AUDIT_CHANNEL_ID)
        except discord.HTTPException as e:
            log_to_gui(f"Falha ao buscar canal de auditoria admin: {e}", "ERROR")
            return
    try:
        await canal.send(embed=embed)
    except discord.HTTPException as e:
        log_to_gui(f"Falha ao enviar log de auditoria admin: {e}", "ERROR")


def _audit_embed(title: str, executor: discord.abc.User, color: discord.Color, **fields) -> discord.Embed:
    embed = discord.Embed(title=title, color=color, timestamp=datetime.now())
    embed.add_field(name="Executado por", value=f"{executor.mention} (`{executor.id}`)", inline=False)
    for name, value in fields.items():
        embed.add_field(name=name, value=str(value), inline=False)
    return embed


def _dump_user_state(conn, user_id: int) -> str:
    """Resumo compacto e somente-leitura do estado de um jogador nas
    principais tabelas — cabe no limite de 1024 chars de um campo de embed."""
    lines = []
    u = conn.execute(
        "SELECT wallet, scrap, fish_count, guild_rank, guild_xp FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if u:
        lines.append(f"💰 {u['wallet']} Sachês | ⚙️ {u['scrap']} sucata | 🐟 {u['fish_count']} capturas")
        lines.append(f"🏅 Rank {u['guild_rank']} ({u['guild_xp']} XP)")
    else:
        lines.append("Sem linha em `users`.")

    rod = conn.execute("SELECT current_rod FROM user_rods WHERE user_id = ?", (user_id,)).fetchone()
    upg = conn.execute("SELECT luck_level, cd_level FROM rod_upgrades WHERE user_id = ?", (user_id,)).fetchone()
    lines.append(
        f"🎣 Vara: {rod['current_rod'] if rod else '?'} "
        f"(sorte Lv{upg['luck_level'] if upg else 0}, cd Lv{upg['cd_level'] if upg else 0})"
    )

    trap = conn.execute("SELECT trap_type, status FROM user_trap WHERE user_id = ?", (user_id,)).fetchone()
    if trap and trap["trap_type"]:
        lines.append(f"🦀 Armadilha: {trap['trap_type']} ({trap['status']})")

    cd = conn.execute(
        "SELECT last_fish, last_daily, last_explore FROM user_cooldowns WHERE user_id = ?", (user_id,)
    ).fetchone()
    if cd:
        lines.append(f"⏱️ last_fish={cd['last_fish']} | last_daily={cd['last_daily']} | last_explore={cd['last_explore']}")

    q = conn.execute("SELECT current_chapter FROM quest_progress WHERE user_id = ?", (user_id,)).fetchone()
    lines.append(f"📜 Quest: {q['current_chapter'] if q else '?'}")

    island = conn.execute("SELECT tier FROM user_islands WHERE user_id = ?", (user_id,)).fetchone()
    struct_row = conn.execute(
        "SELECT COUNT(*) c FROM user_island_structures WHERE user_id = ?", (user_id,)
    ).fetchone()
    lines.append(f"🏝️ Ilha: Tier {island['tier'] if island else 0} ({struct_row['c']} construção(ões))")

    inv_row = conn.execute("SELECT COUNT(*) c FROM user_inventory WHERE user_id = ?", (user_id,)).fetchone()
    lines.append(f"🎒 Inventário: {inv_row['c']} tipo(s) de item")

    return "\n".join(lines)[:1024]


def _dump_table_sample(conn, table: str, user_id: int | None) -> str:
    """Contagem total + amostra de uma tabela permitida. `table` DEVE vir de
    _INSPECTABLE_TABLES (nome de tabela nunca é parametrizável em SQL) — a
    checagem abaixo é defesa em profundidade além do app_commands.Choice que
    já restringe a entrada do usuário."""
    if table not in _INSPECTABLE_TABLES:
        return "Tabela não permitida."
    total = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    lines = [f"Total de linhas: {total}"]
    if user_id is not None:
        row = conn.execute(f"SELECT * FROM {table} WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            lines.append("Linha do jogador: `" + ", ".join(f"{k}={row[k]}" for k in row.keys()) + "`")
        else:
            lines.append("Sem linha para este jogador.")
    else:
        for r in conn.execute(f"SELECT * FROM {table} LIMIT 3").fetchall():
            lines.append("`" + ", ".join(f"{k}={r[k]}" for k in r.keys()) + "`")
    return "\n".join(lines)[:1024]


class ConfirmView(discord.ui.View):
    """Confirmação genérica sim/não para ações de admin com efeito real.

    Nenhum componente de confirmação genérico existia no bot antes desta
    etapa (o mais próximo, AuctionApprovalView em minigames.py, é
    específico do fluxo de leilão) — esta view segue o mesmo padrão de
    interaction_check + desabilitar botões ao clicar/no timeout já usado
    em BlackjackView, CrashView e AuctionApprovalView.
    """

    def __init__(self, author_id: int, timeout: float = 15):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Só quem pediu a confirmação pode responder.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        self.confirmed = False
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class ResetGlobalModal(discord.ui.Modal, title="⚠️ Confirmar Reset Global"):
    """Segundo portão de confirmação do reset global — exige digitar de
    volta o número de jogadores afetados (obtido dinamicamente do banco no
    momento em que o comando foi chamado). Só chega aqui depois de já ter
    passado pelo aviso detalhado em ResetGlobalWarningView.
    """

    def __init__(self, bot, executor: discord.abc.User, players_affected: int):
        super().__init__(timeout=120)
        self.bot = bot
        self.executor = executor
        self.players_affected = players_affected
        self.confirm_text = discord.ui.TextInput(
            label=f"Digite o número {players_affected} para confirmar",
            placeholder=str(players_affected),
            min_length=1,
            max_length=10,
            required=True,
        )
        self.add_item(self.confirm_text)

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm_text.value.strip() != str(self.players_affected):
            return await interaction.response.send_message(
                "🚫 Texto de confirmação não bateu com o número esperado. "
                "Reset **cancelado** — nada foi alterado.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Backup ANTES de qualquer alteração — só prossegue se ele terminar
        # com sucesso (mesma função síncrona que backup_loop usa em
        # cogs/backup.py, aqui via asyncio.to_thread para não travar o loop).
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        remote_name = f"bot_backup_pre_reset_global_{ts}.db"
        try:
            backup_link = await asyncio.to_thread(_upload_db_sync, DB_PATH, remote_name)
        except Exception as e:
            log_to_gui(f"Backup pré-reset-global falhou: {e}", "ERROR")
            await interaction.followup.send(
                f"🚫 **Backup falhou — reset ABORTADO, nada foi alterado.**\nErro: `{e}`",
                ephemeral=True,
            )
            await _send_audit_log(
                self.bot,
                _audit_embed(
                    "🚫 Reset Global ABORTADO (falha no backup)",
                    self.executor,
                    discord.Color.orange(),
                    Erro=str(e),
                    **{"Jogadores que seriam afetados": self.players_affected},
                ),
            )
            return

        conn = get_bot_instance().db_conn
        result = reset_all_players(conn)

        await interaction.followup.send(
            f"✅ **Reset global concluído.** {result['players_affected']} jogador(es) afetado(s).\n"
            f"🛟 Backup pré-reset: {backup_link}",
            ephemeral=True,
        )
        await _send_audit_log(
            self.bot,
            _audit_embed(
                "💥 RESET GLOBAL EXECUTADO",
                self.executor,
                discord.Color.dark_red(),
                **{"Jogadores afetados": result["players_affected"], "Backup pré-reset": backup_link},
            ),
        )


class ResetGlobalWarningView(discord.ui.View):
    """Primeiro portão: aviso detalhado do que será afetado. Só ao clicar
    aqui é que o segundo portão (ResetGlobalModal, com texto exato) abre."""

    def __init__(self, bot, author_id: int, players_affected: int, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.author_id = author_id
        self.players_affected = players_affected

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Só quem pediu pode confirmar.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Continuar para confirmação final", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def continue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        # send_modal() precisa ser a resposta inicial da interação — a edição
        # do botão (pra não ficar clicável de novo) vai por fora, direto na
        # mensagem original, via REST (não consome o slot de resposta).
        await interaction.response.send_modal(
            ResetGlobalModal(self.bot, interaction.user, self.players_affected)
        )
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        self.stop()


class AdminCog(commands.Cog):
    """Comandos exclusivos do Criador. Nunca exposto como comando comum."""

    admin_group = app_commands.Group(
        name="admin",
        description="Ferramentas administrativas (somente o Criador).",
        default_permissions=discord.Permissions(administrator=True),
    )
    economia_group = app_commands.Group(
        name="economia",
        description="Consulta, correção e reset de economia de jogadores.",
        parent=admin_group,
    )
    sistema_group = app_commands.Group(
        name="sistema",
        description="Mensagens, cooldowns e reset global do bot.",
        parent=admin_group,
    )
    debug_group = app_commands.Group(
        name="debug",
        description="Ferramentas de depuração e inspeção genérica.",
        parent=admin_group,
    )

    def __init__(self, bot):
        self.bot = bot

    @economia_group.command(name="consultar", description="Consulta a economia de um jogador (somente leitura).")
    @is_bot_owner()
    @app_commands.describe(usuario="Jogador a consultar")
    async def economia_consultar(self, interaction: discord.Interaction, usuario: discord.Member):
        conn = get_bot_instance().db_conn
        wallet = get_wallet(conn, usuario.id)

        embed = discord.Embed(title=f"🔍 Economia de {usuario.display_name}", color=discord.Color.blue())
        embed.add_field(name="Carteira", value=f"{wallet} Sachês", inline=False)
        embed.set_footer(text=f"ID: {usuario.id}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @economia_group.command(name="corrigir", description="Corrige um campo da economia de um jogador (com confirmação).")
    @is_bot_owner()
    @app_commands.describe(usuario="Jogador a corrigir", campo="Campo a corrigir", valor="Novo valor absoluto do campo")
    @app_commands.choices(campo=[app_commands.Choice(name="Carteira (wallet)", value="wallet")])
    async def economia_corrigir(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        campo: app_commands.Choice[str],
        valor: int,
    ):
        conn = get_bot_instance().db_conn

        # Só "wallet" tem helper atômico pronto na camada v4 hoje
        # (get_wallet/modify_wallet). Outros campos (scrap, guild_rank,
        # fish_count...) exigem helpers equivalentes ainda não escritos —
        # fora do escopo desta etapa mínima.
        if campo.value != "wallet":
            return await interaction.response.send_message(
                f"❌ Campo `{campo.value}` ainda não suportado.", ephemeral=True
            )

        atual = get_wallet(conn, usuario.id)
        descricao = f"Carteira: **{atual}** → **{valor}** Sachês"

        view = ConfirmView(author_id=interaction.user.id)
        await interaction.response.send_message(
            f"⚠️ **Confirmar correção manual?**\n{descricao}\n"
            f"Jogador: {usuario.mention} (ID: {usuario.id})",
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if not view.confirmed:
            await interaction.edit_original_response(
                content="🚫 Correção cancelada (recusada ou expirou sem resposta).", view=None
            )
            return

        delta = valor - atual
        novo_saldo = modify_wallet(conn, usuario.id, delta, usuario.display_name)
        await interaction.edit_original_response(
            content=f"✅ Corrigido. Carteira de {usuario.mention} agora é **{novo_saldo}** Sachês.",
            view=None,
        )
        await _send_audit_log(
            self.bot,
            _audit_embed(
                "🔧 Economia corrigida",
                interaction.user,
                discord.Color.blue(),
                Jogador=f"{usuario.mention} (`{usuario.id}`)",
                Alteração=f"Carteira: {atual} → {novo_saldo} Sachês",
            ),
        )

    @economia_group.command(name="dar", description="Adiciona Sachês à carteira de um jogador (com confirmação).")
    @is_bot_owner()
    @app_commands.describe(usuario="Jogador a receber", valor="Quantidade de Sachês a adicionar (positivo)")
    async def economia_dar(self, interaction: discord.Interaction, usuario: discord.Member, valor: int):
        if valor <= 0:
            return await interaction.response.send_message("❌ O valor precisa ser positivo.", ephemeral=True)

        conn = get_bot_instance().db_conn
        atual = get_wallet(conn, usuario.id)

        view = ConfirmView(author_id=interaction.user.id)
        await interaction.response.send_message(
            f"⚠️ **Confirmar concessão manual?**\nDar **+{valor}** Sachês a {usuario.mention} "
            f"(atual: {atual} → {atual + valor}).",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            return await interaction.edit_original_response(
                content="🚫 Operação cancelada (recusada ou expirou sem resposta).", view=None
            )

        novo_saldo = modify_wallet(conn, usuario.id, valor, usuario.display_name)
        await interaction.edit_original_response(
            content=f"✅ {usuario.mention} recebeu **{valor}** Sachês. Saldo agora: **{novo_saldo}**.",
            view=None,
        )
        await _send_audit_log(
            self.bot,
            _audit_embed(
                "💰 Sachês concedidos",
                interaction.user,
                discord.Color.green(),
                Jogador=f"{usuario.mention} (`{usuario.id}`)",
                Alteração=f"+{valor} Sachês (Carteira: {atual} → {novo_saldo})",
            ),
        )

    @economia_group.command(name="remover", description="Remove Sachês da carteira de um jogador (com confirmação).")
    @is_bot_owner()
    @app_commands.describe(usuario="Jogador a debitar", valor="Quantidade de Sachês a remover (positivo)")
    async def economia_remover(self, interaction: discord.Interaction, usuario: discord.Member, valor: int):
        if valor <= 0:
            return await interaction.response.send_message("❌ O valor precisa ser positivo.", ephemeral=True)

        conn = get_bot_instance().db_conn
        atual = get_wallet(conn, usuario.id)

        view = ConfirmView(author_id=interaction.user.id)
        await interaction.response.send_message(
            f"⚠️ **Confirmar remoção manual?**\nRemover **-{valor}** Sachês de {usuario.mention} "
            f"(atual: {atual} → {max(0, atual - valor)}).",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            return await interaction.edit_original_response(
                content="🚫 Operação cancelada (recusada ou expirou sem resposta).", view=None
            )

        novo_saldo = modify_wallet(conn, usuario.id, -valor, usuario.display_name)
        await interaction.edit_original_response(
            content=f"✅ Removidos **{valor}** Sachês de {usuario.mention}. Saldo agora: **{novo_saldo}**.",
            view=None,
        )
        await _send_audit_log(
            self.bot,
            _audit_embed(
                "💸 Sachês removidos",
                interaction.user,
                discord.Color.orange(),
                Jogador=f"{usuario.mention} (`{usuario.id}`)",
                Alteração=f"-{valor} Sachês (Carteira: {atual} → {novo_saldo})",
            ),
        )

    @economia_group.command(
        name="resetar",
        description="⚠️ Zera TODO o progresso de economia de um jogador (destrutivo, com confirmação).",
    )
    @is_bot_owner()
    @app_commands.describe(usuario="Jogador a resetar")
    async def economia_resetar(self, interaction: discord.Interaction, usuario: discord.Member):
        conn = get_bot_instance().db_conn

        view = ConfirmView(author_id=interaction.user.id)
        await interaction.response.send_message(
            f"⚠️ **Resetar TODO o progresso de {usuario.mention}?**\n"
            "Isso zera: saldo, sucata, inventário, vara equipada/upgrades, armadilha AFK, "
            "cooldowns, rank/XP de guilda, progresso de quest, a **ilha pessoal** "
            "(construções incluídas) e a posição no **leaderboard de torneio**.\n"
            "Conquistas (achievements) e histórico de vendas **não** são afetados.\n"
            f"**Não é reversível** sem restaurar backup. (ID: {usuario.id})",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            return await interaction.edit_original_response(
                content="🚫 Reset cancelado (recusado ou expirou sem resposta).", view=None
            )

        reset_player_progress(conn, usuario.id)
        await interaction.edit_original_response(
            content=f"✅ Progresso de {usuario.mention} foi resetado.", view=None
        )
        await _send_audit_log(
            self.bot,
            _audit_embed(
                "🔄 Reset individual executado",
                interaction.user,
                discord.Color.red(),
                Jogador=f"{usuario.mention} (`{usuario.id}`)",
            ),
        )

    # ──────────────────────────────────────────────
    # /admin sistema — migrado de sistema.py (mensagem_manual, admin_fix_time)
    # ──────────────────────────────────────────────

    @sistema_group.command(name="falar", description="Envia uma mensagem manual em um canal específico.")
    @is_bot_owner()
    @app_commands.describe(
        canal="Selecione o canal onde a mensagem será enviada.",
        mensagem="O conteúdo da mensagem a ser enviada.",
    )
    async def sistema_falar(self, interaction: discord.Interaction, canal: discord.TextChannel, mensagem: str):
        await interaction.response.defer(ephemeral=True)
        try:
            await canal.send(mensagem)
            await interaction.followup.send(
                f"✅ Mensagem enviada com sucesso para o canal {canal.mention}!", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Erro: Não tenho permissão para falar no canal {canal.mention}.", ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Erro ao enviar: {e}", ephemeral=True)

    @sistema_group.command(
        name="fix_cooldowns", description="Reseta cooldowns de pesca/exploração de TODOS os jogadores."
    )
    @is_bot_owner()
    async def sistema_fix_cooldowns(self, interaction: discord.Interaction):
        conn = get_bot_instance().db_conn
        cursor = conn.cursor()
        # Toca as duas tabelas: `economy` (legada, ainda lida por comandos
        # não migrados) e `user_cooldowns` (v4, a que realmente controla
        # /eco pescar e /eco explorar hoje). O comando original só tocava a
        # legada — de nada adiantaria "corrigir" cooldowns que o fluxo real
        # nem lê mais.
        cursor.execute("UPDATE economy SET last_fish = NULL, last_explore = NULL")
        cursor.execute("UPDATE user_cooldowns SET last_fish = NULL, last_explore = NULL")
        conn.commit()
        await interaction.response.send_message(
            "✅ Cooldowns de pesca/exploração resetados para todos os jogadores.", ephemeral=True
        )
        await _send_audit_log(
            self.bot, _audit_embed("⏱️ Cooldowns globais resetados", interaction.user, discord.Color.blue())
        )

    @sistema_group.command(
        name="resetar_tudo",
        description="☠️ RESET GLOBAL: zera economia/progresso de TODOS os jogadores (com backup automático).",
    )
    @is_bot_owner()
    async def sistema_resetar_tudo(self, interaction: discord.Interaction):
        conn = get_bot_instance().db_conn
        row = conn.execute("SELECT COUNT(*) c FROM users").fetchone()
        players_affected = row["c"] if row else 0

        view = ResetGlobalWarningView(self.bot, interaction.user.id, players_affected)
        await interaction.response.send_message(
            "☠️ **RESET GLOBAL — leia com atenção.**\n\n"
            f"Isso vai zerar saldo, sucata, inventário, vara/upgrades, armadilha AFK, cooldowns, "
            f"rank/XP de guilda, quest e ilha pessoal de **TODOS os {players_affected} jogador(es)** "
            "cadastrados, e apagar conquistas, leaderboard de torneio e grupos.\n\n"
            "🛟 Um backup completo do banco é enviado ao Drive **antes** de qualquer alteração — "
            "se o backup falhar, o reset é **abortado automaticamente**, nada é alterado.\n\n"
            "Isso não é reversível sem restaurar esse backup manualmente.\n\n"
            "Clique abaixo para prosseguir para a confirmação final.",
            view=view,
            ephemeral=True,
        )

    # ──────────────────────────────────────────────
    # /admin debug — migrado de sistema.py (catches_inspect/reset, admin_quest)
    # + ferramenta de inspeção genérica nova
    # ──────────────────────────────────────────────

    @debug_group.command(
        name="catches", description="Inspeciona ou reseta o contador de pescas desde o último restart."
    )
    @is_bot_owner()
    @app_commands.describe(
        acao="Inspecionar (lista) ou resetar (limpa) o contador",
        usuario="Limita o reset a este usuário (ignorado ao inspecionar)",
    )
    @app_commands.choices(
        acao=[
            app_commands.Choice(name="Inspecionar", value="inspecionar"),
            app_commands.Choice(name="Resetar", value="resetar"),
        ]
    )
    async def debug_catches(
        self,
        interaction: discord.Interaction,
        acao: app_commands.Choice[str],
        usuario: discord.User = None,
    ):
        if acao.value == "inspecionar":
            async with CATCHES_LOCK:
                _cleanup_stale_catches()
                items = sorted(
                    ((uid, data[0]) for uid, data in CATCHES_SINCE_RESTART.items()),
                    key=lambda x: x[1],
                    reverse=True,
                )
            if not items:
                return await interaction.response.send_message(
                    "Nenhuma pesca registrada desde o restart.", ephemeral=True
                )
            text = "\n".join([f"<@{uid}>: {cnt}" for uid, cnt in items[:50]])
            return await interaction.response.send_message(f"Contador de pescas (top 50):\n{text}", ephemeral=True)

        # acao.value == "resetar"
        async with CATCHES_LOCK:
            if usuario:
                removed = CATCHES_SINCE_RESTART.pop(usuario.id, None)
                await interaction.response.send_message(
                    f"Resetado contador de <@{usuario.id}> (antes: {removed}).", ephemeral=True
                )
            else:
                CATCHES_SINCE_RESTART.clear()
                await interaction.response.send_message(
                    "Resetado contador global de pescas desde restart.", ephemeral=True
                )

    @debug_group.command(name="quest", description="[DEBUG] Força o ganho da Garrafa Incrustada para testes.")
    @is_bot_owner()
    async def debug_quest(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        conn = get_bot_instance().db_conn
        cursor = conn.cursor()

        if not has_account(conn, user_id):
            return await interaction.response.send_message("Crie uma conta pescando primeiro.", ephemeral=True)

        cursor.execute("UPDATE quest_progress SET current_chapter = 'inicio' WHERE user_id = ?", (user_id,))
        conn.commit()

        # Grava pela v4. Escrever o JSON de `economy.inventory` direto era o
        # último escritor só-na-legada do projeto: a garrafa nunca chegava em
        # user_inventory e o sync_user_to_economy do comando seguinte
        # sobrescrevia a coluna a partir da v4, apagando o item.
        set_inventory_item(conn, user_id, "garrafa_incrustada", 1)

        await interaction.response.send_message(
            "🛠️ **DEBUG:** Garrafa adicionada e Quest resetada.\nTeste agora usando `/ler_garrafa`.",
            ephemeral=True,
        )

    @debug_group.command(
        name="inspecionar", description="Inspeciona o estado bruto de um jogador e/ou tabela (somente leitura)."
    )
    @is_bot_owner()
    @app_commands.describe(
        usuario="Jogador a inspecionar (resumo das principais tabelas)",
        tabela="Tabela a inspecionar (contagem + amostra, ou a linha do usuário se ambos informados)",
    )
    @app_commands.choices(tabela=[app_commands.Choice(name=t, value=t) for t in _INSPECTABLE_TABLES])
    async def debug_inspecionar(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member = None,
        tabela: app_commands.Choice[str] = None,
    ):
        if not usuario and not tabela:
            return await interaction.response.send_message("❌ Informe `usuario` e/ou `tabela`.", ephemeral=True)

        conn = get_bot_instance().db_conn
        embed = discord.Embed(title="🔍 Inspeção (somente leitura)", color=discord.Color.dark_teal())

        if usuario:
            embed.add_field(
                name=f"Jogador: {usuario.display_name} (`{usuario.id}`)",
                value=_dump_user_state(conn, usuario.id),
                inline=False,
            )
        if tabela:
            embed.add_field(
                name=f"Tabela: `{tabela.value}`",
                value=_dump_table_sample(conn, tabela.value, usuario.id if usuario else None),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
