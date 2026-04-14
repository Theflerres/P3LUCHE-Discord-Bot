"""
╔══════════════════════════════════════════════════════════════════╗
║  COG: rumores_test.py                                            ║
║  Desc: Sistema de Propagação de Rumores — MODO TESTE             ║
║  ⚠️  USA BANCO ISOLADO (rumores_test.db) — NENHUM DADO REAL      ║
║  Dev: Para ativar em prod, setar TEST_MODE = False e ajustar     ║
║       TEST_DB_PATH e CANAL_EDITORIAL_ID                          ║
╚══════════════════════════════════════════════════════════════════╝
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import asyncio
import json
import random
import networkx as nx
from datetime import datetime, timedelta
from typing import Optional
import os

# ──────────────────────────────────────────────
#  CONFIGURAÇÃO — ajuste antes de testar
# ──────────────────────────────────────────────

TEST_MODE = True                      # ← False em produção
TEST_DB_PATH = "rumores_test.db"      # banco isolado, nunca toca o real
CANAL_EDITORIAL_ID = 1489330740876284206  # ← coloque o ID do seu canal #rumores-pendentes
GEMINI_MOCK = True                    # ← False quando quiser gastar tokens reais

# Intervalo do job de propagação (segundos). Em prod use 3600 (1h) ou mais.
PROPAGATION_INTERVAL = 60             # 60s em teste pra ver funcionando rápido

# Quantos hops um rumor pode dar no grafo antes de parar
MAX_HOPS = 3

# Chance de distorção a cada hop (0.0 a 1.0)
DISTORTION_CHANCE = 0.4


# ──────────────────────────────────────────────
#  MOCK DATA — lore falso para testes
# ──────────────────────────────────────────────

MOCK_LORE_UPDATES = [
    {"player_id": 111, "personagem": "Kael Sombras",  "texto": "meu personagem perdeu o braço esquerdo numa batalha contra a Ordem da Chama"},
    {"player_id": 222, "personagem": "Lyra Voss",     "texto": "meu personagem descobriu que seu pai era um espião da coroa"},
    {"player_id": 333, "personagem": "Doran Pedra",   "texto": "meu personagem quer ajudar Kael Sombras a recuperar uma relíquia perdida"},
    {"player_id": 444, "personagem": "Seraphina",     "texto": "meu personagem abriu uma taverna secreta nos subterrâneos da cidade"},
    {"player_id": 555, "personagem": "Vex",           "texto": "meu personagem traiu a Guilda dos Sussurros e fugiu para o norte"},
]

MOCK_NOS_MUNDO = [
    "Taverna do Corvo",
    "Guilda da Prata",
    "Mercado Sul",
    "Torre do Vigia",
    "Porto das Almas",
]

# Distorções simples por tipo de nó (sem IA)
DISTORTIONS = {
    "Taverna do Corvo":  ["dizem que", "um bêbado jurou que", "ouvi de um viajante que"],
    "Guilda da Prata":   ["fontes confiáveis indicam que", "há rumores internos de que"],
    "Mercado Sul":       ["um comerciante sussurrou que", "as fofocas do mercado dizem que"],
    "Torre do Vigia":    ["os vigias relataram que", "foi avistado que"],
    "Porto das Almas":   ["marinheiros falam que", "chegou um navio com notícias de que"],
}

# Palavras que podem ser omitidas em hops avançados (distorção leve)
OMIT_WORDS = ["esquerdo", "direito", "secreto", "secreta", "espião", "traiu"]


# ──────────────────────────────────────────────
#  DATABASE — totalmente isolado em TEST_MODE
# ──────────────────────────────────────────────

class RumoresDB:
    def __init__(self, db_path: str):
        self.path = db_path
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS lore_raw (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id   INTEGER NOT NULL,
                    personagem  TEXT    NOT NULL,
                    texto_raw   TEXT    NOT NULL,
                    processado  INTEGER DEFAULT 0,   -- 0=pendente 1=processado
                    created_at  TEXT    DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS lore_canonico (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    lore_raw_id     INTEGER REFERENCES lore_raw(id),
                    personagem      TEXT    NOT NULL,
                    fato            TEXT    NOT NULL,   -- fato limpo extraído
                    tipo_evento     TEXT,               -- perda, aliança, traição, descoberta...
                    created_at      TEXT    DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS rumores_pendentes (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    lore_canonico_id    INTEGER REFERENCES lore_canonico(id),
                    fato_original       TEXT    NOT NULL,
                    rumor_texto         TEXT,           -- preenchido após aprovação
                    status              TEXT    DEFAULT 'aguardando',
                    -- aguardando | aprovado_auto | aprovado_manual | secreto | descartado
                    message_id          INTEGER,        -- msg do bot no canal editorial
                    created_at          TEXT    DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS rumores_ativos (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    rumor_id        INTEGER REFERENCES rumores_pendentes(id),
                    no_atual        TEXT    NOT NULL,   -- nó do grafo onde está agora
                    texto_atual     TEXT    NOT NULL,   -- versão possivelmente distorcida
                    hop             INTEGER DEFAULT 0,
                    postado         INTEGER DEFAULT 0,  -- já foi postado neste nó?
                    postado_em      TEXT,
                    created_at      TEXT    DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS grafo_nos (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome    TEXT UNIQUE NOT NULL,
                    tipo    TEXT NOT NULL,              -- player | local | faccao
                    canal_id INTEGER                    -- canal Discord associado (pode ser NULL)
                );

                CREATE TABLE IF NOT EXISTS grafo_arestas (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    no_origem   TEXT REFERENCES grafo_nos(nome),
                    no_destino  TEXT REFERENCES grafo_nos(nome),
                    peso        REAL DEFAULT 1.0        -- 0.0 a 1.0: chance de rumor passar
                );
            """)

    # ── Lore Raw ──
    def inserir_lore_raw(self, player_id, personagem, texto):
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO lore_raw (player_id, personagem, texto_raw) VALUES (?,?,?)",
                (player_id, personagem, texto)
            )
            return cur.lastrowid

    def buscar_lores_pendentes(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM lore_raw WHERE processado = 0"
            ).fetchall()

    def marcar_processado(self, lore_id):
        with self._conn() as conn:
            conn.execute("UPDATE lore_raw SET processado=1 WHERE id=?", (lore_id,))

    # ── Lore Canônico ──
    def inserir_lore_canonico(self, lore_raw_id, personagem, fato, tipo_evento):
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO lore_canonico (lore_raw_id, personagem, fato, tipo_evento) VALUES (?,?,?,?)",
                (lore_raw_id, personagem, fato, tipo_evento)
            )
            return cur.lastrowid

    # ── Rumores Pendentes ──
    def criar_rumor_pendente(self, lore_canonico_id, fato_original):
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO rumores_pendentes (lore_canonico_id, fato_original) VALUES (?,?)",
                (lore_canonico_id, fato_original)
            )
            return cur.lastrowid

    def atualizar_rumor(self, rumor_id, status, rumor_texto=None, message_id=None):
        with self._conn() as conn:
            conn.execute("""
                UPDATE rumores_pendentes
                SET status=?, rumor_texto=COALESCE(?,rumor_texto), message_id=COALESCE(?,message_id)
                WHERE id=?
            """, (status, rumor_texto, message_id, rumor_id))

    def buscar_rumor_por_msg(self, message_id):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM rumores_pendentes WHERE message_id=?", (message_id,)
            ).fetchone()

    def buscar_rumores_aprovados_nao_propagados(self):
        with self._conn() as conn:
            return conn.execute("""
                SELECT * FROM rumores_pendentes
                WHERE status IN ('aprovado_auto','aprovado_manual')
                AND id NOT IN (SELECT DISTINCT rumor_id FROM rumores_ativos)
            """).fetchall()

    # ── Rumores Ativos (propagação) ──
    def inserir_no_ativo(self, rumor_id, no_atual, texto_atual, hop):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO rumores_ativos (rumor_id, no_atual, texto_atual, hop) VALUES (?,?,?,?)",
                (rumor_id, no_atual, texto_atual, hop)
            )

    def buscar_ativos_nao_postados(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM rumores_ativos WHERE postado=0"
            ).fetchall()

    def marcar_postado(self, ativo_id):
        with self._conn() as conn:
            conn.execute(
                "UPDATE rumores_ativos SET postado=1, postado_em=datetime('now') WHERE id=?",
                (ativo_id,)
            )

    # ── Grafo ──
    def inserir_no(self, nome, tipo, canal_id=None):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO grafo_nos (nome, tipo, canal_id) VALUES (?,?,?)",
                (nome, tipo, canal_id)
            )

    def inserir_aresta(self, origem, destino, peso=1.0):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO grafo_arestas (no_origem, no_destino, peso) VALUES (?,?,?)",
                (origem, destino, peso)
            )

    def carregar_grafo(self) -> nx.Graph:
        G = nx.Graph()
        with self._conn() as conn:
            nos = conn.execute("SELECT nome, tipo FROM grafo_nos").fetchall()
            arestas = conn.execute("SELECT no_origem, no_destino, peso FROM grafo_arestas").fetchall()
        for n in nos:
            G.add_node(n["nome"], tipo=n["tipo"])
        for a in arestas:
            G.add_edge(a["no_origem"], a["no_destino"], weight=a["peso"])
        return G

    def listar_tudo(self, tabela: str):
        with self._conn() as conn:
            return conn.execute(f"SELECT * FROM {tabela}").fetchall()


# ──────────────────────────────────────────────
#  GEMINI WRAPPER — mock em GEMINI_MOCK=True
# ──────────────────────────────────────────────

async def gemini_extrair_fatos(updates: list[dict]) -> list[dict]:
    """
    Recebe lista de {personagem, texto_raw} e retorna lista de
    {personagem, fato, tipo_evento}.
    Em GEMINI_MOCK=True retorna dados simulados sem chamar a API.
    """
    if GEMINI_MOCK:
        resultados = []
        tipo_map = {
            "perdeu": "perda", "traiu": "traição", "quer ajudar": "aliança",
            "descobriu": "descoberta", "abriu": "criação", "fugiu": "fuga",
        }
        for u in updates:
            tipo = "evento"
            for kw, t in tipo_map.items():
                if kw in u["texto"].lower():
                    tipo = t
                    break
            # Capitaliza e limpa o fato
            fato = u["texto"].strip().rstrip(".")
            fato = fato[0].upper() + fato[1:] if fato else fato
            resultados.append({
                "personagem": u["personagem"],
                "fato": fato,
                "tipo_evento": tipo,
            })
        return resultados

    # ── PRODUÇÃO: chamada real ao Gemini ──
    try:
        import google.generativeai as genai
        # genai.configure(api_key=...) já deve estar feito no main.py
        model = genai.GenerativeModel("gemini-1.5-pro")

        batch_text = "\n".join(
            f"- personagem '{u['personagem']}': {u['texto']}"
            for u in updates
        )
        prompt = (
            "Para cada item abaixo, extraia em JSON: personagem, fato (frase limpa e direta), "
            "tipo_evento (perda|aliança|traição|descoberta|criação|fuga|outro). "
            "Responda APENAS com um array JSON, sem markdown.\n\n" + batch_text
        )
        resp = model.generate_content(prompt)
        text = resp.text.strip().lstrip("```json").rstrip("```").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[RUMORES] Erro Gemini extrair_fatos: {e}")
        return []


async def gemini_gerar_rumor(fato: str, personagem: str) -> str:
    """
    Transforma um fato canônico num rumor de taverna vago.
    Em GEMINI_MOCK=True retorna texto simulado.
    """
    if GEMINI_MOCK:
        prefixos = [
            "Dizem pelos becos que",
            "Uma fonte anônima jurou ter visto que",
            "Correm rumores de que",
            "Sussurros nos corredores falam que",
            "Ninguém confirma, mas dizem que",
        ]
        return f"{random.choice(prefixos)} {fato.lower().rstrip('.')}..."

    try:
        import google.generativeai as genai
        model = genai.GenerativeModel("gemini-1.5-pro")
        prompt = (
            f"Transforme o fato abaixo em um rumor vago de taverna, como alguém que "
            f"ouviu de terceiros contaria. Máximo 2 frases. Não mencione fontes diretas. "
            f"Fato: {fato}"
        )
        resp = model.generate_content(prompt)
        return resp.text.strip()
    except Exception as e:
        print(f"[RUMORES] Erro Gemini gerar_rumor: {e}")
        return f"Dizem que algo aconteceu com {personagem}..."


# ──────────────────────────────────────────────
#  DISTORÇÃO SEM IA — aplicada a cada hop
# ──────────────────────────────────────────────

def distorcer_texto(texto: str, no_nome: str, hop: int) -> str:
    """Distorce levemente o rumor baseado no nó e no número de hops."""
    # Prefixo do nó
    prefixos = DISTORTIONS.get(no_nome, ["ouvi dizer que"])
    prefixo = random.choice(prefixos)

    # Em hops altos, omite detalhes específicos
    resultado = texto
    if hop >= 2:
        for palavra in OMIT_WORDS:
            resultado = resultado.replace(palavra, "").replace("  ", " ")

    # Em hops altos, adiciona incerteza extra
    if hop >= 3:
        resultado = resultado.rstrip(".") + ", mas ninguém sabe ao certo..."

    # Só adiciona prefixo se o texto não começar com um
    palavras_inicio = ["dizem", "correm", "sussurros", "ouvi", "ninguém", "fontes",
                       "um", "uma", "marinheiros", "os", "foi"]
    if not any(resultado.lower().startswith(p) for p in palavras_inicio):
        resultado = f"{prefixo} {resultado[0].lower()}{resultado[1:]}"

    return resultado.strip()


# ──────────────────────────────────────────────
#  VIEW — Botões do painel editorial
# ──────────────────────────────────────────────

class PainelEditorialView(discord.ui.View):
    def __init__(self, cog, rumor_id: int, fato: str, personagem: str):
        super().__init__(timeout=None)  # persistente
        self.cog = cog
        self.rumor_id = rumor_id
        self.fato = fato
        self.personagem = personagem

    @discord.ui.button(label="✅ Gerar rumor automaticamente", style=discord.ButtonStyle.success, custom_id="rumor_auto")
    async def auto(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        rumor_texto = await gemini_gerar_rumor(self.fato, self.personagem)
        self.cog.db.atualizar_rumor(self.rumor_id, "aprovado_auto", rumor_texto=rumor_texto)
        await interaction.message.edit(
            embed=self._embed_atualizado("✅ Aprovado — rumor gerado automaticamente", rumor_texto, discord.Color.green()),
            view=None
        )
        await interaction.followup.send(f"Rumor gerado e na fila de propagação!", ephemeral=True)

    @discord.ui.button(label="✍️ Escrever manualmente", style=discord.ButtonStyle.primary, custom_id="rumor_manual")
    async def manual(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RumorManualModal(self.cog, self.rumor_id, self.fato, self.personagem, interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🔒 Manter secreto", style=discord.ButtonStyle.secondary, custom_id="rumor_secreto")
    async def secreto(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.cog.db.atualizar_rumor(self.rumor_id, "secreto")
        await interaction.message.edit(
            embed=self._embed_atualizado("🔒 Marcado como secreto — não será espalhado", "", discord.Color.greyple()),
            view=None
        )
        await interaction.response.send_message("Lore mantido em segredo.", ephemeral=True)

    @discord.ui.button(label="🗑️ Descartar", style=discord.ButtonStyle.danger, custom_id="rumor_descartar")
    async def descartar(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.cog.db.atualizar_rumor(self.rumor_id, "descartado")
        await interaction.message.edit(
            embed=self._embed_atualizado("🗑️ Descartado", "", discord.Color.red()),
            view=None
        )
        await interaction.response.send_message("Rumor descartado.", ephemeral=True)

    def _embed_atualizado(self, titulo: str, rumor: str, cor: discord.Color) -> discord.Embed:
        e = discord.Embed(title=titulo, color=cor)
        e.add_field(name="Fato original", value=self.fato, inline=False)
        if rumor:
            e.add_field(name="Rumor", value=rumor, inline=False)
        e.set_footer(text=f"Personagem: {self.personagem}")
        return e


class RumorManualModal(discord.ui.Modal, title="Escrever Rumor"):
    rumor = discord.ui.TextInput(
        label="Texto do rumor",
        style=discord.TextStyle.paragraph,
        placeholder="Como este fato chegaria aos ouvidos do povo?",
        max_length=400,
    )

    def __init__(self, cog, rumor_id, fato, personagem, message):
        super().__init__()
        self.cog = cog
        self.rumor_id = rumor_id
        self.fato = fato
        self.personagem = personagem
        self.original_message = message

    async def on_submit(self, interaction: discord.Interaction):
        self.cog.db.atualizar_rumor(self.rumor_id, "aprovado_manual", rumor_texto=str(self.rumor))
        embed = discord.Embed(
            title="✍️ Aprovado — rumor escrito manualmente",
            color=discord.Color.blue()
        )
        embed.add_field(name="Fato original", value=self.fato, inline=False)
        embed.add_field(name="Rumor", value=str(self.rumor), inline=False)
        embed.set_footer(text=f"Personagem: {self.personagem}")
        await self.original_message.edit(embed=embed, view=None)
        await interaction.response.send_message("Rumor salvo e na fila!", ephemeral=True)


# ──────────────────────────────────────────────
#  COG PRINCIPAL
# ──────────────────────────────────────────────

class RumoresCog(commands.Cog, name="Rumores"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = RumoresDB(TEST_DB_PATH)
        self._setup_grafo_teste()
        if TEST_MODE:
            self._seed_mock_data()
        self.job_processar.start()
        self.job_propagar.start()
        print(f"[RUMORES] Cog carregada. TEST_MODE={TEST_MODE} | GEMINI_MOCK={GEMINI_MOCK}")

    def cog_unload(self):
        self.job_processar.cancel()
        self.job_propagar.cancel()

    # ── Setup do grafo de teste ──
    def _setup_grafo_teste(self):
        for no in MOCK_NOS_MUNDO:
            self.db.inserir_no(no, "local")
        # Conecta os nós em cadeia + algumas conexões extras
        nos = MOCK_NOS_MUNDO
        for i in range(len(nos) - 1):
            peso = round(random.uniform(0.5, 1.0), 2)
            self.db.inserir_aresta(nos[i], nos[i+1], peso)
        # Conexões extras para criar caminhos alternativos
        self.db.inserir_aresta(nos[0], nos[2], 0.6)
        self.db.inserir_aresta(nos[1], nos[4], 0.4)
        self.db.inserir_aresta(nos[3], nos[0], 0.3)

    # ── Seed de dados falsos ──
    def _seed_mock_data(self):
        pendentes = self.db.buscar_lores_pendentes()
        if len(pendentes) == 0:
            print("[RUMORES][TEST] Inserindo mock data...")
            for m in MOCK_LORE_UPDATES:
                self.db.inserir_lore_raw(m["player_id"], m["personagem"], m["texto"])
            print(f"[RUMORES][TEST] {len(MOCK_LORE_UPDATES)} lores mock inseridos.")

    # ── JOB 1: Processar lores pendentes (batch) ──
    @tasks.loop(seconds=PROPAGATION_INTERVAL)
    async def job_processar(self):
        pendentes = self.db.buscar_lores_pendentes()
        if not pendentes:
            return

        print(f"[RUMORES] Processando {len(pendentes)} lore(s) pendente(s)...")

        updates = [{"personagem": r["personagem"], "texto": r["texto_raw"]} for r in pendentes]
        fatos = await gemini_extrair_fatos(updates)

        canal = self.bot.get_channel(CANAL_EDITORIAL_ID)

        for i, raw in enumerate(pendentes):
            if i >= len(fatos):
                break
            f = fatos[i]
            canon_id = self.db.inserir_lore_canonico(
                raw["id"], f["personagem"], f["fato"], f["tipo_evento"]
            )
            rumor_id = self.db.criar_rumor_pendente(canon_id, f["fato"])
            self.db.marcar_processado(raw["id"])

            # Posta no canal editorial se configurado
            if canal:
                await self._postar_editorial(canal, rumor_id, f["fato"], f["personagem"], f["tipo_evento"])
            else:
                print(f"[RUMORES] ⚠️  CANAL_EDITORIAL_ID não configurado. Rumor ID {rumor_id} criado sem notificação.")

    @job_processar.before_loop
    async def before_processar(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)  # pequeno delay inicial

    # ── JOB 2: Propagar rumores aprovados pelo grafo ──
    @tasks.loop(seconds=PROPAGATION_INTERVAL * 2)
    async def job_propagar(self):
        aprovados = self.db.buscar_rumores_aprovados_nao_propagados()
        if not aprovados:
            return

        G = self.db.carregar_grafo()
        nos = list(G.nodes())
        if not nos:
            return

        for rumor in aprovados:
            no_inicial = random.choice(nos)
            texto_inicial = rumor["rumor_texto"]
            print(f"[RUMORES] Propagando rumor ID {rumor['id']} a partir de '{no_inicial}'")
            await self._propagar_bfs(G, rumor["id"], no_inicial, texto_inicial)

    @job_propagar.before_loop
    async def before_propagar(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(15)

    # ── Propagação BFS pelo grafo ──
    async def _propagar_bfs(self, G: nx.Graph, rumor_id: int, no_inicial: str, texto: str):
        visitados = set()
        fila = [(no_inicial, texto, 0)]

        while fila:
            no_atual, texto_atual, hop = fila.pop(0)
            if no_atual in visitados or hop > MAX_HOPS:
                continue
            visitados.add(no_atual)

            # Aplica distorção (sem IA)
            texto_distorcido = distorcer_texto(texto_atual, no_atual, hop) if hop > 0 else texto_atual

            self.db.inserir_no_ativo(rumor_id, no_atual, texto_distorcido, hop)

            # Adiciona vizinhos à fila com base no peso da aresta
            for vizinho in G.neighbors(no_atual):
                if vizinho not in visitados:
                    peso = G[no_atual][vizinho].get("weight", 1.0)
                    if random.random() <= peso:
                        fila.append((vizinho, texto_distorcido, hop + 1))

    # ── Posta no canal editorial ──
    async def _postar_editorial(self, canal, rumor_id, fato, personagem, tipo_evento):
        cores = {
            "perda": discord.Color.red(), "traição": discord.Color.dark_red(),
            "aliança": discord.Color.blue(), "descoberta": discord.Color.gold(),
            "criação": discord.Color.green(), "fuga": discord.Color.orange(),
        }
        cor = cores.get(tipo_evento, discord.Color.greyple())

        embed = discord.Embed(
            title=f"📜 Novo lore processado — `{tipo_evento.upper()}`",
            color=cor,
            timestamp=datetime.now()
        )
        embed.add_field(name="Personagem", value=personagem, inline=True)
        embed.add_field(name="Tipo de Evento", value=tipo_evento, inline=True)
        embed.add_field(name="Fato Canônico", value=f"*{fato}*", inline=False)
        if TEST_MODE:
            embed.set_footer(text="⚠️ MODO TESTE — dados fictícios")

        view = PainelEditorialView(self, rumor_id, fato, personagem)
        msg = await canal.send(embed=embed, view=view)
        self.db.atualizar_rumor(rumor_id, "aguardando", message_id=msg.id)

    # ──────────────────────────────────────────
    #  SLASH COMMANDS
    # ──────────────────────────────────────────

    rumores_group = app_commands.Group(name="rumores", description="Sistema de Rumores [TESTE]")

    @rumores_group.command(name="status", description="Mostra o estado atual do sistema de rumores")
    async def status(self, interaction: discord.Interaction):
        raws = self.db.listar_tudo("lore_raw")
        canonicos = self.db.listar_tudo("lore_canonico")
        pendentes = self.db.listar_tudo("rumores_pendentes")
        ativos = self.db.listar_tudo("rumores_ativos")
        nos = self.db.listar_tudo("grafo_nos")

        aguardando = sum(1 for r in pendentes if r["status"] == "aguardando")
        aprovados  = sum(1 for r in pendentes if "aprovado" in r["status"])
        secretos   = sum(1 for r in pendentes if r["status"] == "secreto")

        embed = discord.Embed(
            title="🕸️ Sistema de Rumores — Status",
            color=discord.Color.teal(),
            timestamp=datetime.now()
        )
        embed.add_field(name="📥 Lores Raw", value=str(len(raws)), inline=True)
        embed.add_field(name="📖 Lores Canônicos", value=str(len(canonicos)), inline=True)
        embed.add_field(name="🗺️ Nós no Grafo", value=str(len(nos)), inline=True)
        embed.add_field(name="⏳ Aguardando revisão", value=str(aguardando), inline=True)
        embed.add_field(name="✅ Aprovados", value=str(aprovados), inline=True)
        embed.add_field(name="🔒 Secretos", value=str(secretos), inline=True)
        embed.add_field(name="🌐 Rumores propagando", value=str(len(ativos)), inline=True)
        if TEST_MODE:
            embed.set_footer(text=f"⚠️ MODO TESTE | DB: {TEST_DB_PATH} | GEMINI_MOCK: {GEMINI_MOCK}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @rumores_group.command(name="grafo", description="Mostra os nós e arestas do grafo de propagação")
    async def grafo(self, interaction: discord.Interaction):
        G = self.db.carregar_grafo()
        nos_txt = "\n".join(f"• `{n}`" for n in G.nodes())
        arestas_txt = "\n".join(
            f"• `{u}` ↔ `{v}` (peso: {d.get('weight',1.0):.1f})"
            for u, v, d in G.edges(data=True)
        )
        embed = discord.Embed(title="🕸️ Grafo de Propagação", color=discord.Color.purple())
        embed.add_field(name=f"Nós ({G.number_of_nodes()})", value=nos_txt or "—", inline=False)
        embed.add_field(name=f"Arestas ({G.number_of_edges()})", value=arestas_txt or "—", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @rumores_group.command(name="rumores_ativos", description="Lista os rumores atualmente se propagando")
    async def listar_ativos(self, interaction: discord.Interaction):
        ativos = self.db.buscar_ativos_nao_postados()
        if not ativos:
            await interaction.response.send_message("Nenhum rumor ativo no momento.", ephemeral=True)
            return
        embed = discord.Embed(title="🌐 Rumores em Propagação", color=discord.Color.orange())
        for a in ativos[:10]:
            embed.add_field(
                name=f"Nó: `{a['no_atual']}` (hop {a['hop']})",
                value=f"*\"{a['texto_atual']}\"*",
                inline=False
            )
        if len(ativos) > 10:
            embed.set_footer(text=f"... e mais {len(ativos)-10} rumores")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @rumores_group.command(name="simular_ticket", description="[TESTE] Simula um ticket de atualização de lore")
    @app_commands.describe(personagem="Nome do personagem", update="O que aconteceu com ele")
    async def simular_ticket(self, interaction: discord.Interaction, personagem: str, update: str):
        lore_id = self.db.inserir_lore_raw(interaction.user.id, personagem, update)
        embed = discord.Embed(
            title="🎫 Ticket Simulado",
            description=f"Lore raw inserido com ID `{lore_id}`.\nSerá processado no próximo ciclo do job.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Personagem", value=personagem, inline=True)
        embed.add_field(name="Update", value=update, inline=False)
        embed.set_footer(text="⚠️ MODO TESTE")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @rumores_group.command(name="forcar_job", description="[TESTE] Força execução imediata dos jobs")
    async def forcar_job(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.job_processar()
        await self.job_propagar()
        await interaction.followup.send("✅ Jobs executados. Use `/rumores status` para ver o resultado.", ephemeral=True)

    @rumores_group.command(name="limpar_teste", description="[TESTE] Apaga todos os dados do banco de teste")
    async def limpar_teste(self, interaction: discord.Interaction):
        if not TEST_MODE:
            await interaction.response.send_message("❌ Só pode ser usado em TEST_MODE.", ephemeral=True)
            return
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = RumoresDB(TEST_DB_PATH)
        self._setup_grafo_teste()
        self._seed_mock_data()
        await interaction.response.send_message("🗑️ Banco de teste limpo e reiniciado com mock data.", ephemeral=True)


# ──────────────────────────────────────────────
#  SETUP
# ──────────────────────────────────────────────

async def setup(bot: commands.Bot):
    cog = RumoresCog(bot)
    await bot.add_cog(cog)
    bot.tree.add_command(cog.rumores_group)
    print("[RUMORES] Cog registrada com sucesso.")