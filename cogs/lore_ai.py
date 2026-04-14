"""
Lore, IA (Gemini), acervo, grafo e personalidade P3LUCHE.
Melhorias v2:
  - Tickets com aprovação de staff antes de salvar no banco
  - Grafo com detecção de tipo de relação + arestas coloridas
  - P3LUCHE aberto a todos + roteamento inteligente + comandos práticos /p3luche
"""
import asyncio
import difflib
import gc
import json
import random
import re
import textwrap
import time
from datetime import datetime
from io import BytesIO

import discord
import matplotlib.pyplot as plt
import networkx as nx
import scipy  # noqa: F401
from discord import app_commands
from discord.ext import commands, tasks
from google import genai

from config import (
    CAT_ACTIVITIES,
    CAT_FACES,
    CREATOR_ID,
    EMOTE_CANSADO,
    EMOTE_FOGO,
    EMOTE_MEDO,
    GEMINI_KEY,
    MOD_ROLE_IDS,
    STANDBY_TIMEOUT_MINUTES,
    USER_MUSIC_CHANNEL_ID,
    get_bot_instance,
    set_bot_instance,
)
from utils import extract_text_from_attachment, log_to_gui, sanitize_text

# ──────────────────────────────────────────────
#  CONFIGURAÇÃO — ajuste esses dois IDs
# ──────────────────────────────────────────────

# Canal onde o staff recebe pedidos de aprovação de lore
CANAL_APROVACAO_LORE_ID: int = 0   # ← coloque o ID do canal #aprovacao-lore

# Cooldown em segundos por usuário para mensagens ao P3LUCHE (evita abuso de tokens)
COOLDOWN_GERAL_SEGUNDOS: int = 15

# ──────────────────────────────────────────────
#  TIPOS DE RELAÇÃO PARA O GRAFO
# ──────────────────────────────────────────────

# (palavras-chave, cor da aresta, label)
RELATION_TYPES = {
    "aliado":  (["aliado", "aliança", "parceiro", "ajudar", "juntos", "lado a lado",
                 "amigo", "confia", "defende", "protege"],            "#2ecc71", "Aliado"),
    "inimigo": (["inimigo", "rival", "odeia", "conflito", "guerra", "traiu",
                 "matou", "assassinou", "contra", "ameaça", "persegue"], "#e74c3c", "Inimigo"),
    "familia": (["pai", "mãe", "irmão", "irmã", "filho", "filha",
                 "família", "sangue", "primo", "avô", "avó", "parente"], "#f1c40f", "Família"),
    "mestre":  (["mestre", "discípulo", "treinou", "ensinou", "aprendeu",
                 "mentor", "guia", "aprendiz"],                        "#9b59b6", "Mestre/Discípulo"),
}
RELATION_DEFAULT_COLOR = "#99aab5"   # cinza para relação neutra/genérica


# ──────────────────────────────────────────────
#  HELPERS DE RELAÇÃO
# ──────────────────────────────────────────────

def _detect_relation(text_origem: str, nome_alvo: str) -> tuple[str, str]:
    """
    Dado o texto de um personagem e o nome de outro,
    retorna (cor_aresta, label_relação) detectando o tipo pelo contexto.
    """
    # Encontra o trecho em torno do nome do alvo (±80 chars de contexto)
    idx = text_origem.lower().find(nome_alvo.lower())
    if idx == -1:
        return RELATION_DEFAULT_COLOR, "Conhece"
    trecho = text_origem[max(0, idx - 80): idx + 80].lower()

    for rel_key, (keywords, color, label) in RELATION_TYPES.items():
        if any(kw in trecho for kw in keywords):
            return color, label

    return RELATION_DEFAULT_COLOR, "Conhece"


# ──────────────────────────────────────────────
#  GERAÇÃO DE IMAGEM DO GRAFO (MELHORADA)
# ──────────────────────────────────────────────

def _generate_graph_image(nodes: list, edges: list, node_colors: dict) -> BytesIO:
    """
    edges: lista de (origem, destino, cor_aresta, label_relação)
    """
    G = nx.Graph()
    G.add_nodes_from(nodes)
    for u, v, color, label in edges:
        G.add_edge(u, v, color=color, label=label)

    node_count = len(nodes)
    width  = min(16 + node_count * 0.6, 100)
    height = min(9  + node_count * 0.3, 60)

    fig, ax = plt.subplots(figsize=(width, height), dpi=150)
    fig.patch.set_facecolor("#2f3136")
    ax.set_facecolor("#2f3136")

    pos = nx.spring_layout(G, k=2.5, iterations=150, seed=42)

    # Agrupa arestas por cor para desenhar em batch (mais performático)
    edges_by_color: dict[str, list] = {}
    for u, v, data in G.edges(data=True):
        c = data.get("color", RELATION_DEFAULT_COLOR)
        edges_by_color.setdefault(c, []).append((u, v))

    for color, edge_list in edges_by_color.items():
        nx.draw_networkx_edges(G, pos, edgelist=edge_list,
                               edge_color=color, width=2.5, alpha=0.75, ax=ax)

    # Nós
    colors_mapped = [node_colors.get(n, "#5865F2") for n in G.nodes()]
    nx.draw_networkx_nodes(G, pos, node_size=5000, node_color=colors_mapped,
                           edgecolors="#ffffff", linewidths=3, ax=ax)

    # Labels dos nós
    labels = {n: textwrap.fill(str(n), width=12) for n in G.nodes()}
    nx.draw_networkx_labels(G, pos, labels=labels, font_size=12,
                            font_color="white", font_weight="bold",
                            font_family="sans-serif", ax=ax)

    # Labels das arestas (relação) — só se o grafo não for enorme
    if node_count <= 15:
        edge_labels = {(u, v): data.get("label", "") for u, v, data in G.edges(data=True)}
        nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels,
                                     font_size=8, font_color="#dddddd", ax=ax)

    # Legenda de cores
    from matplotlib.lines import Line2D
    legend_items = [
        Line2D([0], [0], color="#5865F2", marker="o", linestyle="None", markersize=10, label="Player"),
        Line2D([0], [0], color="#9b59b6", marker="o", linestyle="None", markersize=10, label="Mundo"),
        Line2D([0], [0], color="#2ecc71", linewidth=2, label="Aliado"),
        Line2D([0], [0], color="#e74c3c", linewidth=2, label="Inimigo"),
        Line2D([0], [0], color="#f1c40f", linewidth=2, label="Família"),
        Line2D([0], [0], color="#9b59b6", linewidth=2, label="Mestre/Discípulo"),
        Line2D([0], [0], color="#99aab5", linewidth=2, label="Conhece"),
    ]
    ax.legend(handles=legend_items, loc="upper left", framealpha=0.3,
              labelcolor="white", facecolor="#2f3136", fontsize=10)

    ax.axis("off")
    buf = BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf


# ──────────────────────────────────────────────
#  SISTEMA DE APROVAÇÃO DE TICKETS DE LORE
# ──────────────────────────────────────────────

# Armazena lores pendentes em memória: {message_id: {dados}}
# Se o bot reiniciar, pedidos pendentes são perdidos (aceitável por ora)
_pending_lores: dict[int, dict] = {}


class RejectReasonModal(discord.ui.Modal, title="Motivo da Rejeição"):
    motivo = discord.ui.TextInput(
        label="Explique o motivo para o player",
        style=discord.TextStyle.paragraph,
        placeholder="Ex: O nome do personagem já existe, lore muito curta...",
        max_length=500,
    )

    def __init__(self, message_id: int, original_message: discord.Message):
        super().__init__()
        self.message_id      = message_id
        self.original_message = original_message

    async def on_submit(self, interaction: discord.Interaction):
        data = _pending_lores.pop(self.message_id, None)
        if not data:
            await interaction.response.send_message("❌ Pedido não encontrado (pode ter expirado).", ephemeral=True)
            return

        # Notifica o player via DM
        try:
            guild  = interaction.guild
            member = guild.get_member(data["player_id"]) or await guild.fetch_member(data["player_id"])
            embed  = discord.Embed(title="❌ Lore Rejeitada", color=discord.Color.red())
            embed.add_field(name="Personagem", value=data["nome_personagem"], inline=True)
            embed.add_field(name="Motivo",     value=str(self.motivo),        inline=False)
            embed.set_footer(text=f"Rejeitado por {interaction.user.name}")
            await member.send(embed=embed)
        except Exception:
            pass  # DM fechada, sem problema

        # Atualiza a mensagem do canal de aprovação
        embed_upd = discord.Embed(title="❌ Lore Rejeitada", color=discord.Color.red())
        embed_upd.add_field(name="Personagem",   value=data["nome_personagem"],      inline=True)
        embed_upd.add_field(name="Player",        value=f"<@{data['player_id']}>",   inline=True)
        embed_upd.add_field(name="Motivo",        value=str(self.motivo),            inline=False)
        embed_upd.set_footer(text=f"Rejeitado por {interaction.user.name} · {datetime.now().strftime('%d/%m %H:%M')}")
        await self.original_message.edit(embed=embed_upd, view=None)
        await interaction.response.send_message("✅ Rejeição enviada ao player.", ephemeral=True)


class AprovacaoLoreView(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="✅ Aprovar", style=discord.ButtonStyle.success, custom_id="lore_aprovar")
    async def aprovar(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = _pending_lores.pop(self.message_id, None)
        if not data:
            await interaction.response.send_message("❌ Pedido não encontrado (pode ter expirado).", ephemeral=True)
            return

        # Salva no banco
        try:
            cursor = get_bot_instance().db_conn.cursor()
            cursor.execute(
                "INSERT INTO player_lore (target_id, target_name, character_name, content, added_by) VALUES (?,?,?,?,?)",
                (data["player_id"], data["player_name"], data["nome_personagem"],
                 data["content"], data["added_by"])
            )
            get_bot_instance().db_conn.commit()
        except Exception as e:
            await interaction.response.send_message(f"❌ Erro ao salvar no banco: {e}", ephemeral=True)
            return

        # Notifica player
        try:
            guild  = interaction.guild
            member = guild.get_member(data["player_id"]) or await guild.fetch_member(data["player_id"])
            embed  = discord.Embed(title="✅ Lore Aprovada!", color=discord.Color.green())
            embed.add_field(name="Personagem", value=data["nome_personagem"], inline=True)
            embed.add_field(name="Status", value="Sua história foi registrada na Biblioteca de Alexandria.", inline=False)
            await member.send(embed=embed)
        except Exception:
            pass

        # Atualiza mensagem
        embed_upd = discord.Embed(title="✅ Lore Aprovada e Salva", color=discord.Color.green())
        embed_upd.add_field(name="Personagem", value=data["nome_personagem"],    inline=True)
        embed_upd.add_field(name="Player",     value=f"<@{data['player_id']}>", inline=True)
        embed_upd.set_footer(text=f"Aprovado por {interaction.user.name} · {datetime.now().strftime('%d/%m %H:%M')}")
        await interaction.message.edit(embed=embed_upd, view=None)
        await interaction.response.send_message("✅ Lore aprovada e player notificado.", ephemeral=True)

    @discord.ui.button(label="❌ Rejeitar", style=discord.ButtonStyle.danger, custom_id="lore_rejeitar")
    async def rejeitar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RejectReasonModal(self.message_id, interaction.message)
        )

    @discord.ui.button(label="👁️ Ver Conteúdo", style=discord.ButtonStyle.secondary, custom_id="lore_ver")
    async def ver(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = _pending_lores.get(self.message_id)
        if not data:
            await interaction.response.send_message("❌ Pedido não encontrado.", ephemeral=True)
            return
        preview = data["content"][:1900] + ("..." if len(data["content"]) > 1900 else "")
        await interaction.response.send_message(
            f"**Prévia do conteúdo de {data['nome_personagem']}:**\n```\n{preview}\n```",
            ephemeral=True
        )


# ──────────────────────────────────────────────
#  P3LUCHE PERSONA
# ──────────────────────────────────────────────

class P3luchePersona(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.allowed_channels = USER_MUSIC_CHANNEL_ID
        self.last_activity    = datetime.now()
        self.is_standby       = False
        # Cooldown por user_id: {user_id: último_timestamp}
        self._cooldowns: dict[int, float] = {}

        self.persona_base = (
            "Você é o P3LUCHE, o gato mascote do servidor. "
            "Personalidade: Temperamental, imprevisível, felino. '8 ou 80'. "
            "Diretrizes: SEJA BREVE. TOM: Sarcástico/Felino. ZERO TECH. "
            "Se perguntarem de lore, use o contexto fornecido. "
            "Para perguntas práticas (clima, tradução, resumo, etc), seja útil e direto."
        )

        if GEMINI_KEY:
            try:
                self.ai_client     = genai.Client(api_key=GEMINI_KEY)
                self.ai_model_name = "gemini-2.0-flash"
                log_to_gui(f"IA Conectada: Cliente Google GenAI (Modelo: {self.ai_model_name})", "SUCCESS")
            except Exception as e:
                log_to_gui(f"Erro ao configurar IA: {e}", "ERROR")
                self.ai_client = None
        else:
            self.ai_client = None

        self.emote_fogo    = EMOTE_FOGO
        self.emote_medo    = EMOTE_MEDO
        self.emote_cansado = EMOTE_CANSADO

        self.random_event_loop.start()
        self.standby_check_loop.start()
        self.status_rotation_loop.start()

    def cog_unload(self):
        self.random_event_loop.cancel()
        self.standby_check_loop.cancel()
        self.status_rotation_loop.cancel()

    # ── Standby ──
    async def register_activity(self):
        self.last_activity = datetime.now()
        if self.is_standby:
            self.is_standby = False
            log_to_gui("Acordando...", "WAKEUP")
            await self.update_rich_presence()
            if not self.random_event_loop.is_running():
                self.random_event_loop.start()

    @tasks.loop(minutes=5)
    async def status_rotation_loop(self):
        if not self.is_standby:
            await self.update_rich_presence()

    async def update_rich_presence(self):
        face             = random.choice(CAT_FACES)
        act_type, act_name = random.choice(CAT_ACTIVITIES)
        status           = f"{act_name} {face}"
        act_obj = discord.Activity(
            type=act_type if act_type != discord.ActivityType.custom else discord.ActivityType.custom,
            name="custom" if act_type == discord.ActivityType.custom else status,
            state=status if act_type == discord.ActivityType.custom else None,
        )
        await self.bot.change_presence(status=discord.Status.online, activity=act_obj)

    @tasks.loop(minutes=1)
    async def standby_check_loop(self):
        if self.is_standby:
            return
        if (datetime.now() - self.last_activity).total_seconds() > STANDBY_TIMEOUT_MINUTES * 60:
            self.is_standby = True
            log_to_gui("Standby iniciado.", "SLEEP")
            await self.bot.change_presence(
                status=discord.Status.idle,
                activity=discord.Activity(type=discord.ActivityType.custom, name="custom", state="💤 Zzz..."),
            )
            self.random_event_loop.cancel()
            import gc; gc.collect()

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        await self.register_activity()

    @status_rotation_loop.before_loop
    async def before_status(self): await self.bot.wait_until_ready()
    @standby_check_loop.before_loop
    async def before_standby(self): await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if not isinstance(error, commands.CommandNotFound):
            print(f"Erro: {error}")

    # ── DB Helpers ──
    def get_server_lore(self):
        try:
            r = self.bot.db_conn.cursor().execute(
                "SELECT content FROM server_lore ORDER BY created_at DESC LIMIT 15"
            ).fetchall()
            return "\n".join([f"- {x[0]}" for x in r[::-1]]) if r else ""
        except:
            return ""

    def get_all_players_with_lore(self):
        try:
            r = self.bot.db_conn.cursor().execute(
                "SELECT DISTINCT target_name, character_name FROM player_lore"
            ).fetchall()
            return "\n".join([f"- {n} ({c or '?'})" for n, c in r]) if r else "Ninguém."
        except:
            return ""

    def get_player_lore(self, tid):
        try:
            r = self.bot.db_conn.cursor().execute(
                "SELECT content, character_name FROM player_lore WHERE target_id=? ORDER BY created_at DESC LIMIT 10",
                (tid,),
            ).fetchall()
            return (f"PERSONAGEM: {r[0][1]}\n" + "\n".join([f"- {x[0]}" for x in r[::-1]])) if r else ""
        except:
            return ""

    async def split_and_send(self, message, text):
        if len(text) <= 2000:
            await message.reply(text)
        else:
            for chunk in [text[i:i+1900] for i in range(0, len(text), 1900)]:
                await message.channel.send(chunk)

    @tasks.loop(minutes=45)
    async def random_event_loop(self):
        if not self.allowed_channels or random.random() > 0.2:
            return
        try:
            ch = self.bot.get_channel(random.choice(self.allowed_channels))
            if ch:
                await ch.send(random.choice(["Tédio...", "Miau.", "*Julgando.*", "Zzz...", "Cadê meu sachê?"]))
        except:
            pass

    @random_event_loop.before_loop
    async def before_random(self): await self.bot.wait_until_ready()

    # ──────────────────────────────────────────
    #  ON_MESSAGE — RESTRITO A STAFF/CRIADOR + ROTEAMENTO
    # ──────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if not self.bot.user.mentioned_in(message):
            return

        await self.register_activity()

        # Cooldown global (evita spam de tokens)
        now = time.time()
        last = self._cooldowns.get(message.author.id, 0)
        if now - last < COOLDOWN_GERAL_SEGUNDOS:
            remaining = int(COOLDOWN_GERAL_SEGUNDOS - (now - last))
            await message.reply(f"*Boceja.* Espera {remaining}s antes de me incomodar de novo.", delete_after=5)
            return
        self._cooldowns[message.author.id] = now

        raw_content = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
        content     = sanitize_text(raw_content)
        if not content:
            return

        is_creator = message.author.id == CREATOR_ID
        is_staff   = any(r.id in MOD_ROLE_IDS for r in message.author.roles)

        if not is_creator and not is_staff:
            await message.reply("🔒 IA restrita à Staff e ao criador.", delete_after=10)
            return

        # ── Gravar memória (só staff/creator) ──
        if (is_staff or is_creator) and re.search(r"\b(lembre-se que|anote que)\b", content.lower()):
            try:
                c = re.sub(r"(lembre-se\s+que|anote\s+que)", "", content, flags=re.IGNORECASE).strip()
                self.bot.db_conn.cursor().execute(
                    "INSERT INTO user_memories (user_id, user_name, memory_text, is_active) VALUES (?,?,?,1)",
                    (message.author.id, message.author.name, c),
                )
                self.bot.db_conn.commit()
                await message.add_reaction("💙" if is_creator else "💾")
                await message.reply("Anotado no meu núcleo, pai! 😺" if is_creator else "Tá, guardei na memória.")
            except Exception as e:
                log_to_gui(f"Erro ao salvar memória: {e}", "ERROR")
            return

        if not self.ai_client:
            return

        async with message.channel.typing():
            try:
                # ── Detecta se é consulta de LORE ou pergunta GERAL ──
                lore_keywords = ["lore", "personagem", "história", "mundo", "quem é", "o que aconteceu"]
                is_lore_query = any(kw in content.lower() for kw in lore_keywords) or bool(message.mentions)

                ctx_lore    = ""
                user_memories = ""

                if is_lore_query and (is_staff or is_creator):
                    # Staff ou criador perguntando sobre lore → contexto completo
                    ctx_lore = f"\n[SERVER LORE]:\n{self.get_server_lore()}"
                    if any(x in content.lower() for x in ["quem", "lista", "lore"]):
                        ctx_lore += f"\n[LISTA]:\n{self.get_all_players_with_lore()}"
                    for m in message.mentions:
                        if m.id != self.bot.user.id:
                            lore = self.get_player_lore(m.id)
                            if lore:
                                ctx_lore += f"\n[LORE {m.name}]:\n{lore}"

                    mem_rows = self.bot.db_conn.cursor().execute(
                        "SELECT memory_text FROM user_memories WHERE user_id=? AND is_active=1 ORDER BY created_at DESC LIMIT 5",
                        (message.author.id,),
                    ).fetchall()
                    if mem_rows:
                        user_memories = "\n[O QUE SEI SOBRE VOCÊ]:\n" + "\n- ".join(
                            [r["memory_text"] for r in mem_rows]
                        )

                # ── Persona ──
                if is_creator:
                    persona = (
                        f"{self.persona_base}\n"
                        "IMPORTANTE: O usuário atual é seu CRIADOR/PAI (theflerres). "
                        "Com ele, seja doce, carinhoso e leal. Use emojis fofos."
                    )
                elif is_staff:
                    persona = (
                        f"{self.persona_base}\n"
                        "IMPORTANTE: Usuário é Staff. Seja levemente mais prestativo, mas mantenha o sarcasmo felino."
                    )
                else:
                    persona = (
                        f"{self.persona_base}\n"
                        "IMPORTANTE: Usuário comum. Sarcástico, '8 ou 80'. "
                        "Se pedir informações de lore privada de outros jogadores, recuse elegantemente. "
                        "Para perguntas práticas (tradução, resumo, dúvida geral), responda de forma útil e breve."
                    )

                prompt = (
                    f"{persona}\n\n"
                    f"{ctx_lore}\n"
                    f"{user_memories}\n\n"
                    f"USUÁRIO ({message.author.name}) DIZ: {content}\n"
                    f"RESPOSTA DO P3LUCHE:"
                )

                response = await self.ai_client.aio.models.generate_content(
                    model=self.ai_model_name, contents=prompt
                )
                await self.split_and_send(message, response.text)

            except Exception as e:
                if "429" in str(e):
                    await message.reply("Cota excedida. (Gemma cansou)")
                else:
                    log_to_gui(f"Erro na IA: {e}", "ERROR")
                    await message.reply("*Tosse bola de pelos* (Erro no processamento).")


# ──────────────────────────────────────────────
#  VIEWS DO ACERVO (inalteradas)
# ──────────────────────────────────────────────

class LorePaginationView(discord.ui.View):
    def __init__(self, title, text):
        super().__init__(timeout=600)
        self.title       = title
        self.chunks      = [text[i:i+2000] for i in range(0, len(text), 2000)]
        self.current_page = 0
        self.total_pages = len(self.chunks)

    async def get_page_embed(self):
        embed = discord.Embed(title=f"📖 {self.title}", color=discord.Color.blue())
        embed.description = self.chunks[self.current_page]
        embed.set_footer(text=f"Página {self.current_page+1}/{self.total_pages} · {sum(len(c) for c in self.chunks)} chars")
        return embed

    async def update_buttons(self, interaction):
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = self.current_page == self.total_pages - 1
        await interaction.response.edit_message(embed=await self.get_page_embed(), view=self)

    @discord.ui.button(label="◀️ Anterior", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_btn(self, interaction, button):
        self.current_page -= 1
        await self.update_buttons(interaction)

    @discord.ui.button(label="Próximo ▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction, button):
        self.current_page += 1
        await self.update_buttons(interaction)


class AskLoreModal(discord.ui.Modal, title="Consultar a Sabedoria Ancestral"):
    def __init__(self, lore_content, persona_cog, target_name):
        super().__init__()
        self.lore_content = lore_content
        self.persona_cog  = persona_cog
        self.target_name  = target_name
        self.question = discord.ui.TextInput(
            label="Qual sua dúvida?",
            placeholder=f"O que deseja saber sobre {target_name}?",
            style=discord.TextStyle.paragraph, required=True, max_length=500,
        )
        self.add_item(self.question)

    async def on_submit(self, interaction):
        await interaction.response.defer(thinking=True)
        try:
            prompt = (
                f"Você é o Guardião da Biblioteca P3LUCHE. Use APENAS o texto abaixo para responder.\n"
                f"TEXTO FONTE ({self.target_name}):\n{self.lore_content[:25000]}\n\n"
                f"PERGUNTA: {self.question.value}\n\n"
                f"Resposta (seja direto e cite se a informação consta ou não no texto):"
            )
            if not self.persona_cog or not getattr(self.persona_cog, "ai_client", None):
                return await interaction.followup.send("❌ IA offline.", ephemeral=True)
            response = await self.persona_cog.ai_client.aio.models.generate_content(
                model=self.persona_cog.ai_model_name, contents=prompt
            )
            embed = discord.Embed(title=f"❓ Pergunta sobre: {self.target_name}", color=discord.Color.gold())
            embed.add_field(name="Dúvida",            value=self.question.value,    inline=False)
            embed.add_field(name="Resposta do Arquivo", value=response.text[:1024], inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao consultar: {e}")


class AcervoActionsView(discord.ui.View):
    def __init__(self, bot_ref, lore_type, target_id=None, target_name="Mundo"):
        super().__init__(timeout=300)
        self.bot         = bot_ref
        self.lore_type   = lore_type
        self.target_id   = target_id
        self.target_name = target_name

    def get_full_lore(self):
        cursor = self.bot.db_conn.cursor()
        if self.lore_type == "server":
            rows = cursor.execute("SELECT content FROM server_lore ORDER BY created_at ASC").fetchall()
        else:
            rows = cursor.execute(
                "SELECT content FROM player_lore WHERE target_id=? ORDER BY created_at ASC", (self.target_id,)
            ).fetchall()
        return "\n\n=== REGISTRO ===\n".join([r[0] for r in rows]) if rows else ""

    @discord.ui.button(label="Baixar .txt Completo", style=discord.ButtonStyle.secondary, emoji="📄")
    async def download_txt(self, interaction, button):
        full_text = self.get_full_lore()
        if not full_text:
            return await interaction.response.send_message("📭 O arquivo está vazio.", ephemeral=True)
        file = discord.File(BytesIO(full_text.encode("utf-8")), filename=f"Lore_{self.target_name.replace(' ','_')}.txt")
        await interaction.response.send_message(f"📂 Arquivo completo de **{self.target_name}**.", file=file, ephemeral=True)

    @discord.ui.button(label="Pedir Resumo (IA)", style=discord.ButtonStyle.primary, emoji="📝")
    async def summarize(self, interaction, button):
        full_text = self.get_full_lore()
        if not full_text:
            return await interaction.response.send_message("📭 Nada para resumir.", ephemeral=True)
        cog = self.bot.get_cog("P3luchePersona")
        if not cog or not getattr(cog, "ai_client", None):
            return await interaction.response.send_message("❌ IA offline.", ephemeral=True)
        await interaction.response.defer(thinking=True)
        try:
            prompt   = f"Resumo estruturado em tópicos das informações mais importantes desta Lore ({self.target_name}):\n\n{full_text[:30000]}"
            response = await cog.ai_client.aio.models.generate_content(model=cog.ai_model_name, contents=prompt)
            embed    = discord.Embed(title=f"📝 Resumo: {self.target_name}", description=response.text[:4000], color=discord.Color.blue())
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Erro na IA: {e}")

    @discord.ui.button(label="Fazer Pergunta Específica", style=discord.ButtonStyle.success, emoji="❓")
    async def ask_specific(self, interaction, button):
        full_text = self.get_full_lore()
        if not full_text:
            return await interaction.response.send_message("📭 Nada para consultar.", ephemeral=True)
        cog = self.bot.get_cog("P3luchePersona")
        if not cog or not getattr(cog, "ai_client", None):
            return await interaction.response.send_message("❌ IA offline.", ephemeral=True)
        await interaction.response.send_modal(AskLoreModal(full_text, cog, self.target_name))

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.danger, row=1)
    async def back(self, interaction, button):
        await interaction.response.edit_message(content="Retornando...", embed=get_hub_embed(), view=AcervoHubView(self.bot))


class PlayerSelect(discord.ui.Select):
    def __init__(self, bot_ref, players_data):
        self.bot = bot_ref
        options  = []
        for p_id, p_name, char_name in players_data[:25]:
            options.append(discord.SelectOption(
                label=str(p_name),
                description=f"Personagem: {char_name or 'Desconhecido'}",
                value=str(p_id), emoji="👤",
            ))
        super().__init__(placeholder="Selecione um Player...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        target_id     = int(self.values[0])
        selected      = next(o for o in self.options if o.value == self.values[0])
        target_name   = f"{selected.label} ({selected.description})"
        embed = discord.Embed(title=f"📂 Arquivo: {target_name}", description="O que deseja fazer?", color=discord.Color.green())
        await interaction.response.edit_message(embed=embed, view=AcervoActionsView(self.bot, "player", target_id, target_name))


class AcervoHubView(discord.ui.View):
    def __init__(self, bot_ref):
        super().__init__(timeout=None)
        self.bot = bot_ref

    @discord.ui.button(label="Lore do Mundo (Servidor)", style=discord.ButtonStyle.blurple, emoji="🌍")
    async def server_lore_btn(self, interaction, button):
        embed = discord.Embed(title="🌍 Lore Global do Mundo", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=AcervoActionsView(self.bot, "server", target_name="Mundo/Servidor"))

    @discord.ui.button(label="Lore dos Convidados (Players)", style=discord.ButtonStyle.green, emoji="👥")
    async def players_lore_btn(self, interaction, button):
        players = self.bot.db_conn.cursor().execute(
            "SELECT DISTINCT target_id, target_name, character_name FROM player_lore ORDER BY target_name ASC"
        ).fetchall()
        if not players:
            return await interaction.response.send_message("📭 Nenhum player registrou lore.", ephemeral=True)
        view = discord.ui.View()
        view.add_item(PlayerSelect(self.bot, players))
        back_btn = discord.ui.Button(label="Voltar", style=discord.ButtonStyle.danger, row=1)
        async def back_cb(inter): await inter.response.edit_message(embed=get_hub_embed(), view=AcervoHubView(self.bot))
        back_btn.callback = back_cb
        view.add_item(back_btn)
        embed = discord.Embed(title="👥 Arquivo de Convidados", color=discord.Color.green())
        await interaction.response.edit_message(embed=embed, view=view)


def get_hub_embed():
    embed = discord.Embed(
        title="🏛️ Biblioteca de Alexandria — HUB",
        description="Acervo central de conhecimento.\nSelecione uma categoria abaixo.",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3534/3534033.png")
    embed.set_footer(text="Acesso Restrito: Nível Administrador")
    return embed


# ──────────────────────────────────────────────
#  MODAL DE EDIÇÃO COM VERSIONAMENTO
# ──────────────────────────────────────────────

class EditLoreModal(discord.ui.Modal, title="Editar Registro Histórico"):
    def __init__(self, lore_id, current_content, table_name):
        super().__init__()
        self.lore_id    = lore_id
        self.table_name = table_name
        self.new_content = discord.ui.TextInput(
            label="Novo Conteúdo", style=discord.TextStyle.paragraph,
            default=current_content[:3900], required=True, max_length=4000,
        )
        self.add_item(self.new_content)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            cursor   = get_bot_instance().db_conn.cursor()
            is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
            if "player" in self.table_name and not is_staff:
                owner = cursor.execute(f"SELECT target_id FROM {self.table_name} WHERE id=?", (self.lore_id,)).fetchone()
                if not owner or owner["target_id"] != interaction.user.id:
                    return await interaction.followup.send("🚫 Você não é o dono desta história.", ephemeral=True)
            current = cursor.execute(f"SELECT content FROM {self.table_name} WHERE id=?", (self.lore_id,)).fetchone()
            if current:
                cursor.execute(
                    "INSERT INTO lore_versions (lore_type, original_lore_id, content, edited_by, created_at) VALUES (?,?,?,?,?)",
                    ("player" if "player" in self.table_name else "server", self.lore_id, current["content"],
                     interaction.user.name, datetime.now()),
                )
            cursor.execute(f"UPDATE {self.table_name} SET content=? WHERE id=?", (self.new_content.value, self.lore_id))
            get_bot_instance().db_conn.commit()
            await interaction.followup.send(f"✅ Registro **#{self.lore_id}** atualizado! Versão antiga salva.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)
            log_to_gui(f"Erro versionamento: {e}", "ERROR")


class SelectLoreToEdit(discord.ui.View):
    def __init__(self, rows, table_name):
        super().__init__(timeout=60)
        self.table_name = table_name
        options = []
        for row in rows[:25]:
            l_id, content, target = row
            snippet = (content[:50] + "...") if len(content) > 50 else content
            options.append(discord.SelectOption(label=f"ID {l_id} | {target}", description=snippet, value=str(l_id)))
        sel = discord.ui.Select(placeholder="Selecione qual registro editar...", options=options)
        sel.callback = self.callback
        self.add_item(sel)

    async def callback(self, interaction):
        lore_id = int(self.values[0])
        row     = get_bot_instance().db_conn.cursor().execute(
            f"SELECT content FROM {self.table_name} WHERE id=?", (lore_id,)
        ).fetchone()
        if not row:
            return await interaction.response.send_message("❌ Registro não encontrado.", ephemeral=True)
        if len(row["content"]) > 3800:
            embed = discord.Embed(title="🚨 Arquivo Muito Grande!", color=discord.Color.red(),
                description=f"**{len(row['content'])} chars** — use `/lore atualizar id_lore:{lore_id} arquivo:[novo]`")
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.send_modal(EditLoreModal(lore_id, row["content"], self.table_name))


# ──────────────────────────────────────────────
#  COMANDOS — definidos fora das classes
# ──────────────────────────────────────────────

# /acervo
@app_commands.command(name="acervo", description="Abre o HUB da Biblioteca de Alexandria (Apenas Staff).")
async def acervo(interaction: discord.Interaction):
    if not any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
        return await interaction.response.send_message("🚫 Acesso negado.", ephemeral=True)
    await interaction.response.send_message(embed=get_hub_embed(), view=AcervoHubView(get_bot_instance()))


# /lore
lore_group = app_commands.Group(name="lore", description="Gerenciamento da Biblioteca de Alexandria")


@lore_group.command(name="player", description="Arquiva lore de personagem (PDF, DOCX, TXT).")
@app_commands.describe(
    usuario="De quem é a lore? (Players só podem escolher a si mesmos)",
    nome_personagem="Nome do Personagem (RP)",
    arquivo1="Arquivo 1 (Opcional)", arquivo2="Arquivo 2 (Opcional)", arquivo3="Arquivo 3 (Opcional)",
    texto="Texto adicional (opcional)",
)
async def lore_player(
    interaction: discord.Interaction,
    usuario: discord.Member,
    nome_personagem: str,
    arquivo1: discord.Attachment = None,
    arquivo2: discord.Attachment = None,
    arquivo3: discord.Attachment = None,
    texto: str = None,
):
    is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    if not is_staff and usuario.id != interaction.user.id:
        return await interaction.response.send_message("🚫 Você só pode registrar a história do seu próprio personagem.", ephemeral=True)

    await interaction.response.defer(thinking=True)
    final_content = ""

    for i, anexo in enumerate([a for a in [arquivo1, arquivo2, arquivo3] if a]):
        extracted = await extract_text_from_attachment(anexo)
        final_content += f"\n--- ARQUIVO {i+1} ({anexo.filename}) ---\n{extracted or '[erro ao ler]'}\n"
    if texto:
        final_content += f"\n--- NOTA ADICIONAL ---\n{texto}"

    if not final_content.strip():
        return await interaction.followup.send("❌ Envie ao menos um arquivo ou escreva no campo texto.", ephemeral=True)

    # ── Staff salva direto; players entram em fila de aprovação ──
    if is_staff:
        try:
            cursor = get_bot_instance().db_conn.cursor()
            cursor.execute(
                "INSERT INTO player_lore (target_id, target_name, character_name, content, added_by) VALUES (?,?,?,?,?)",
                (usuario.id, usuario.name, nome_personagem, final_content, interaction.user.name),
            )
            get_bot_instance().db_conn.commit()
            embed = discord.Embed(title="📚 Lore Arquivada!", color=discord.Color.green())
            embed.add_field(name="Personagem", value=nome_personagem, inline=True)
            embed.add_field(name="Player",     value=usuario.mention, inline=True)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: {e}")
    else:
        # Envia para aprovação
        canal_aprov = get_bot_instance().get_channel(CANAL_APROVACAO_LORE_ID)
        if not canal_aprov:
            # Fallback: avisa que canal não está configurado
            log_to_gui("CANAL_APROVACAO_LORE_ID não configurado.", "WARNING")
            await interaction.followup.send(
                "✅ Lore enviada! Aguardando um Bibliotecário revisar antes de arquivar.", ephemeral=True
            )
        else:
            embed = discord.Embed(
                title="📋 Nova Lore Aguardando Aprovação",
                color=discord.Color.orange(),
                timestamp=datetime.now(),
            )
            embed.add_field(name="Personagem", value=nome_personagem,           inline=True)
            embed.add_field(name="Player",     value=f"<@{usuario.id}>",        inline=True)
            embed.add_field(name="Enviado por", value=interaction.user.mention, inline=True)
            embed.add_field(name="Tamanho",    value=f"{len(final_content)} chars", inline=True)
            embed.set_footer(text="Use os botões abaixo para aprovar ou rejeitar")

            view = AprovacaoLoreView(0)  # message_id será preenchido após envio
            msg  = await canal_aprov.send(embed=embed, view=view)

            # Armazena pendente
            _pending_lores[msg.id] = {
                "player_id":      usuario.id,
                "player_name":    usuario.name,
                "nome_personagem": nome_personagem,
                "content":        final_content,
                "added_by":       interaction.user.name,
            }
            # Atualiza a view com o message_id correto
            view.message_id = msg.id

            await interaction.followup.send(
                "✅ Lore enviada para aprovação! Você será notificado por DM quando o Bibliotecário revisar.",
                ephemeral=True,
            )


@lore_group.command(name="editar", description="Edita uma lore existente.")
@app_commands.describe(tipo="Tipo de lore", usuario="Filtrar por usuário (Apenas Staff)")
@app_commands.choices(tipo=[
    app_commands.Choice(name="Minhas Lores / Player Lore", value="player_lore"),
    app_commands.Choice(name="Server Lore (Apenas Staff)",  value="server_lore"),
])
async def lore_editar(interaction: discord.Interaction, tipo: app_commands.Choice[str], usuario: discord.Member = None):
    is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    table    = tipo.value
    cursor   = get_bot_instance().db_conn.cursor()

    if table == "server_lore" and not is_staff:
        return await interaction.response.send_message("🚫 Apenas Staff pode editar a Server Lore.", ephemeral=True)

    if table == "player_lore":
        if is_staff:
            query  = ("SELECT id, content, character_name FROM player_lore WHERE target_id=? ORDER BY created_at DESC", (usuario.id,)) if usuario \
                else ("SELECT id, content, character_name FROM player_lore ORDER BY created_at DESC LIMIT 25", ())
        else:
            if usuario and usuario.id != interaction.user.id:
                return await interaction.response.send_message("🚫 Sem permissão.", ephemeral=True)
            query = ("SELECT id, content, character_name FROM player_lore WHERE target_id=? ORDER BY created_at DESC", (interaction.user.id,))
    else:
        query = ("SELECT id, content, 'Mundo' FROM server_lore ORDER BY created_at DESC LIMIT 25", ())

    rows = cursor.execute(*query).fetchall()
    if not rows:
        return await interaction.response.send_message("📭 Nenhuma lore encontrada.", ephemeral=True)
    await interaction.response.send_message("Selecione o registro:", view=SelectLoreToEdit(rows, table), ephemeral=True)


@lore_group.command(name="historico", description="Vê versões antigas de uma lore.")
@app_commands.describe(id_lore="ID do registro original")
async def lore_historico(interaction: discord.Interaction, id_lore: int):
    cursor   = get_bot_instance().db_conn.cursor()
    is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    lore_info = cursor.execute("SELECT target_id, character_name FROM player_lore WHERE id=?", (id_lore,)).fetchone()

    if not lore_info:
        srv = cursor.execute("SELECT id FROM server_lore WHERE id=?", (id_lore,)).fetchone()
        if srv:
            if not is_staff: return await interaction.response.send_message("🚫 Apenas Staff.", ephemeral=True)
            target_name = "Mundo/Servidor"
        else:
            return await interaction.response.send_message("❌ Lore não encontrada.", ephemeral=True)
    else:
        target_id, target_name = lore_info
        if not is_staff and target_id != interaction.user.id:
            return await interaction.response.send_message("🚫 Só pode ver o histórico das suas próprias histórias.", ephemeral=True)

    versions = cursor.execute(
        "SELECT id, edited_by, created_at FROM lore_versions WHERE original_lore_id=? ORDER BY created_at DESC", (id_lore,)
    ).fetchall()
    if not versions:
        return await interaction.response.send_message(f"📭 Registro **#{id_lore}** nunca foi editado.", ephemeral=True)

    embed = discord.Embed(title=f"📜 Arquivo Morto: {target_name}", color=discord.Color.light_grey())
    for v in versions:
        embed.add_field(
            name=f"📅 {v['created_at']} (ID: {v['id']})",
            value=f"**Editado por:** {v['edited_by']}\nUse `/lore diff id_versao:{v['id']}`",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@lore_group.command(name="diff", description="Mostra o que mudou entre versões.")
@app_commands.describe(id_versao="ID da versão antiga (pegue no /lore historico)")
async def lore_diff(interaction: discord.Interaction, id_versao: int):
    cursor    = get_bot_instance().db_conn.cursor()
    old_ver   = cursor.execute(
        "SELECT original_lore_id, content, created_at FROM lore_versions WHERE id=?", (id_versao,)
    ).fetchone()
    if not old_ver:
        return await interaction.response.send_message("❌ Versão não encontrada.", ephemeral=True)

    is_staff  = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    lore_info = cursor.execute("SELECT target_id, content FROM player_lore WHERE id=?", (old_ver["original_lore_id"],)).fetchone()
    if lore_info:
        if not is_staff and lore_info["target_id"] != interaction.user.id:
            return await interaction.response.send_message("🚫 Acesso negado.", ephemeral=True)
        current_text = lore_info["content"]
    else:
        srv = cursor.execute("SELECT content FROM server_lore WHERE id=?", (old_ver["original_lore_id"],)).fetchone()
        if srv and is_staff: current_text = srv["content"]
        else: return await interaction.response.send_message("❌ Lore original não encontrada.", ephemeral=True)

    diff_text = "\n".join(difflib.unified_diff(
        old_ver["content"].splitlines(), current_text.splitlines(),
        fromfile=f"Versão Antiga ({old_ver['created_at']})", tofile="Versão Atual", lineterm="",
    ))
    if not diff_text:
        return await interaction.response.send_message("🤷 Textos idênticos.", ephemeral=True)
    if len(diff_text) > 1900:
        f = discord.File(BytesIO(diff_text.encode("utf-8")), filename="mudancas.diff")
        await interaction.response.send_message("📑 Mudanças muito grandes — baixe o arquivo:", file=f, ephemeral=True)
    else:
        await interaction.response.send_message(f"```diff\n{diff_text}\n```", ephemeral=True)


@lore_group.command(name="ler", description="Lê uma lore completa com paginação.")
@app_commands.describe(id_lore="ID da Lore")
async def lore_ler(interaction: discord.Interaction, id_lore: int):
    cursor = get_bot_instance().db_conn.cursor()
    row    = cursor.execute("SELECT character_name, content FROM player_lore WHERE id=?", (id_lore,)).fetchone()
    if row:  title, content = row["character_name"], row["content"]
    else:
        row = cursor.execute("SELECT content FROM server_lore WHERE id=?", (id_lore,)).fetchone()
        if row: title, content = "Lore do Mundo", row["content"]
        else:   return await interaction.response.send_message("❌ Lore não encontrada.", ephemeral=True)
    view  = LorePaginationView(title, content)
    embed = await view.get_page_embed()
    if view.total_pages <= 1: view.children[1].disabled = True
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@lore_group.command(name="atualizar", description="Atualiza lore existente via arquivo (para textos grandes).")
@app_commands.describe(id_lore="ID da Lore", arquivo="Novo arquivo (PDF/DOCX/TXT)")
async def lore_atualizar(interaction: discord.Interaction, id_lore: int, arquivo: discord.Attachment):
    await interaction.response.defer(thinking=True)
    cursor   = get_bot_instance().db_conn.cursor()
    is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    lore_info = cursor.execute("SELECT target_id, content, character_name FROM player_lore WHERE id=?", (id_lore,)).fetchone()
    table     = "player_lore"
    if not lore_info:
        lore_info = cursor.execute("SELECT id, content FROM server_lore WHERE id=?", (id_lore,)).fetchone()
        table = "server_lore"
        if not lore_info: return await interaction.followup.send("❌ Lore não encontrada.")
    if table == "player_lore" and not is_staff and lore_info["target_id"] != interaction.user.id:
        return await interaction.followup.send("🚫 Sem permissão.")
    if table == "server_lore" and not is_staff:
        return await interaction.followup.send("🚫 Apenas Staff.")
    new_text = await extract_text_from_attachment(arquivo)
    if not new_text: return await interaction.followup.send("❌ Não consegui ler o arquivo.")
    try:
        cursor.execute(
            "INSERT INTO lore_versions (lore_type, original_lore_id, content, edited_by, created_at) VALUES (?,?,?,?,?)",
            ("player" if table == "player_lore" else "server", id_lore, lore_info["content"], interaction.user.name, datetime.now()),
        )
        cursor.execute(f"UPDATE {table} SET content=?, edited_by=?, edited_at=? WHERE id=?",
                       (new_text, interaction.user.name, datetime.now(), id_lore))
        get_bot_instance().db_conn.commit()
        await interaction.followup.send(f"✅ Registro **#{id_lore}** atualizado via arquivo! (Backup salvo).")
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {e}")


@lore_group.command(name="grafo", description="Gera teia visual de relações (Players=Azul, Mundo=Roxo, arestas coloridas por tipo).")
async def lore_grafo(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        cursor = get_bot_instance().db_conn.cursor()
        p_rows = cursor.execute("SELECT character_name, content FROM player_lore WHERE character_name IS NOT NULL").fetchall()
        s_rows = cursor.execute("SELECT content FROM server_lore").fetchall()

        if not p_rows:
            return await interaction.followup.send("❌ Preciso de pelo menos 1 player para desenhar.")

        nodes        = []
        edges        = []  # (origem, destino, cor, label)
        node_colors  = {}
        player_dict  = {r["character_name"]: r["content"].lower() for r in p_rows}
        all_players  = list(player_dict.keys())

        for p in all_players:
            nodes.append(p)
            node_colors[p] = "#5865F2"

        # Conexões Player ↔ Player com detecção de tipo de relação
        already_connected = set()
        for origin in all_players:
            for target in all_players:
                if origin == target: continue
                pair = tuple(sorted([origin, target]))
                if pair in already_connected: continue
                if target.lower() in player_dict[origin]:
                    color, label = _detect_relation(player_dict[origin], target)
                    edges.append((origin, target, color, label))
                    already_connected.add(pair)
                elif origin.lower() in player_dict[target]:
                    color, label = _detect_relation(player_dict[target], origin)
                    edges.append((target, origin, color, label))
                    already_connected.add(pair)

        # Nó do Mundo
        if s_rows:
            server_lore_full = " ".join([r["content"].lower() for r in s_rows])
            nodes.append("Mundo")
            node_colors["Mundo"] = "#9b59b6"
            for p in all_players:
                if p.lower() in server_lore_full:
                    color, label = _detect_relation(server_lore_full, p)
                    edges.append(("Mundo", p, color, label))
                elif any(kw in player_dict[p] for kw in ["mundo", "reino", "servidor", "capital", "história"]):
                    edges.append((p, "Mundo", RELATION_DEFAULT_COLOR, "Conhece"))

        if not edges:
            return await interaction.followup.send("❌ Não encontrei conexões suficientes para desenhar.")

        image_buffer = await asyncio.to_thread(_generate_graph_image, nodes, edges, node_colors)
        file  = discord.File(image_buffer, filename="teia_destinos.png")
        embed = discord.Embed(
            title="🕸️ Teia de Destinos",
            description=(
                "🔵 **Azul:** Players | 🟣 **Roxo:** Mundo\n"
                "🟢 Aliado | 🔴 Inimigo | 🟡 Família | 🟣 Mestre/Discípulo | ⬜ Conhece"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_image(url="attachment://teia_destinos.png")
        await interaction.followup.send(embed=embed, file=file)
    except Exception as e:
        log_to_gui(f"Erro no grafo: {e}", "ERROR")
        await interaction.followup.send(f"❌ Erro: {e}")


@lore_group.command(name="server", description="Arquiva lore do mundo/servidor (Apenas Staff).")
async def lore_server(interaction: discord.Interaction, arquivo: discord.Attachment = None, texto: str = None):
    if not any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
        return await interaction.response.send_message("🚫 Apenas Staff.", ephemeral=True)
    await interaction.response.defer(thinking=True)
    final_content = ""
    if arquivo:
        final_content += f"\n{await extract_text_from_attachment(arquivo)}\n"
    if texto:
        final_content += f"\n{texto}"
    if not final_content.strip():
        return await interaction.followup.send("❌ Nada para salvar.", ephemeral=True)
    try:
        get_bot_instance().db_conn.cursor().execute("INSERT INTO server_lore (content) VALUES (?)", (final_content,))
        get_bot_instance().db_conn.commit()
        await interaction.followup.send("✅ **Lore Global** adicionada à Biblioteca de Alexandria.")
    except Exception as e:
        await interaction.followup.send(f"Erro: {e}")


# ──────────────────────────────────────────────
#  /p3luche — UTILIDADES PRÁTICAS
# ──────────────────────────────────────────────

p3luche_group = app_commands.Group(name="p3luche", description="Utilidades práticas do P3LUCHE")


def _get_ai(interaction: discord.Interaction):
    cog = interaction.client.get_cog("P3luchePersona")
    return cog.ai_client if cog else None, cog.ai_model_name if cog else None


def _can_use_ai_command(interaction: discord.Interaction) -> bool:
    return interaction.user.id == CREATOR_ID or any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)


@p3luche_group.command(name="traduzir", description="Traduz um texto para o idioma desejado.")
@app_commands.describe(texto="Texto a traduzir", idioma="Idioma de destino (ex: inglês, espanhol, japonês)")
async def p3_traduzir(interaction: discord.Interaction, texto: str, idioma: str):
    await interaction.response.defer(thinking=True)
    if not _can_use_ai_command(interaction):
        return await interaction.followup.send("🚫 IA restrita ao criador e à staff.", ephemeral=True)
    ai_client, ai_model = _get_ai(interaction)
    if not ai_client:
        return await interaction.followup.send("❌ IA offline.")
    try:
        resp = await ai_client.aio.models.generate_content(
            model=ai_model,
            contents=f"Traduza o texto abaixo para {idioma}. Responda APENAS com a tradução, sem explicações.\n\nTexto: {texto}",
        )
        embed = discord.Embed(title=f"🌐 Tradução → {idioma}", color=discord.Color.blue())
        embed.add_field(name="Original",  value=texto[:500],      inline=False)
        embed.add_field(name="Traduzido", value=resp.text[:1000], inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {e}")


@p3luche_group.command(name="resumir", description="Resume um texto longo.")
@app_commands.describe(texto="Texto a resumir")
async def p3_resumir(interaction: discord.Interaction, texto: str):
    await interaction.response.defer(thinking=True)
    if not _can_use_ai_command(interaction):
        return await interaction.followup.send("🚫 IA restrita ao criador e à staff.", ephemeral=True)
    ai_client, ai_model = _get_ai(interaction)
    if not ai_client:
        return await interaction.followup.send("❌ IA offline.")
    try:
        resp = await ai_client.aio.models.generate_content(
            model=ai_model,
            contents=f"Resuma o texto abaixo em no máximo 3 parágrafos curtos. Seja direto.\n\nTexto: {texto[:8000]}",
        )
        embed = discord.Embed(title="📝 Resumo", description=resp.text[:2000], color=discord.Color.teal())
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {e}")


@p3luche_group.command(name="enquete", description="Cria uma enquete rápida com reações.")
@app_commands.describe(
    pergunta="A pergunta da enquete",
    opcoes="Opções separadas por | (ex: Opção A | Opção B | Opção C)",
)
async def p3_enquete(interaction: discord.Interaction, pergunta: str, opcoes: str):
    emojis  = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    items   = [o.strip() for o in opcoes.split("|") if o.strip()][:10]
    if len(items) < 2:
        return await interaction.response.send_message("❌ Informe pelo menos 2 opções separadas por |", ephemeral=True)

    embed = discord.Embed(title=f"📊 {pergunta}", color=discord.Color.blurple(),
                          description="\n".join(f"{emojis[i]} {item}" for i, item in enumerate(items)))
    embed.set_footer(text=f"Enquete criada por {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    for i in range(len(items)):
        await msg.add_reaction(emojis[i])


@p3luche_group.command(name="reescrever", description="Reescreve um texto num tom diferente.")
@app_commands.describe(
    texto="Texto original",
    tom="Tom desejado (ex: formal, casual, épico, engraçado, técnico)",
)
async def p3_reescrever(interaction: discord.Interaction, texto: str, tom: str):
    await interaction.response.defer(thinking=True)
    if not _can_use_ai_command(interaction):
        return await interaction.followup.send("🚫 IA restrita ao criador e à staff.", ephemeral=True)
    ai_client, ai_model = _get_ai(interaction)
    if not ai_client:
        return await interaction.followup.send("❌ IA offline.")
    try:
        resp = await ai_client.aio.models.generate_content(
            model=ai_model,
            contents=f"Reescreva o texto abaixo num tom {tom}. Responda APENAS com a reescrita.\n\nTexto: {texto[:3000]}",
        )
        embed = discord.Embed(title=f"✍️ Reescrito — tom: {tom}", color=discord.Color.purple())
        embed.add_field(name="Original",  value=texto[:500],      inline=False)
        embed.add_field(name="Resultado", value=resp.text[:1000], inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {e}")


@p3luche_group.command(name="ajuda", description="Lista tudo que o P3LUCHE sabe fazer.")
async def p3_ajuda(interaction: discord.Interaction):
    embed = discord.Embed(
        title="😺 P3LUCHE — O que eu sei fazer",
        color=discord.Color.gold(),
    )
    embed.add_field(name="💬 Mencione @P3LUCHE", value=(
        "Pergunte ao bot sobre lore, mas atenção: a IA é exclusiva do Staff e do criador.\n"
        f"*Cooldown: {COOLDOWN_GERAL_SEGUNDOS}s entre mensagens.*"
    ), inline=False)
    embed.add_field(name="📚 Lore", value=(
        "`/lore player` — Arquivar lore de personagem\n"
        "`/lore editar` — Editar lore existente\n"
        "`/lore grafo` — Teia de destinos visual\n"
        "`/lore ler` — Ler lore com paginação\n"
        "`/lore historico` — Ver versões antigas\n"
        "`/lore diff` — Comparar versões"
    ), inline=False)
    embed.add_field(name="🛠️ Utilidades", value=(
        "`/p3luche traduzir` — Traduz texto\n"
        "`/p3luche resumir` — Resume texto\n"
        "`/p3luche reescrever` — Muda o tom de um texto\n"
        "`/p3luche enquete` — Cria enquete com reações"
    ), inline=False)
    embed.set_footer(text="Staff: /acervo para a Biblioteca completa")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────
#  COG LOADER
# ──────────────────────────────────────────────

class LoreAICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        set_bot_instance(bot)

    async def cog_load(self):
        self.bot.tree.add_command(lore_group)
        self.bot.tree.add_command(acervo)
        self.bot.tree.add_command(p3luche_group)


async def setup(bot):
    await bot.add_cog(P3luchePersona(bot))
    await bot.add_cog(LoreAICog(bot))