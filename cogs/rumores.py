"""
╔══════════════════════════════════════════════════════════════════╗
║  COG: rumores.py                                                 ║
║  Desc: Sistema de Propagação de Rumores — MODO TESTE             ║
║  ⚠️  USA BANCO ISOLADO (rumores_test.db) — NENHUM DADO REAL      ║
║  Padrão: grupo definido fora da classe + cog_load (= lore_ai.py) ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import json
import os
import random
import sqlite3
from datetime import datetime

import discord
import networkx as nx
from discord import app_commands
from discord.ext import commands, tasks

# ──────────────────────────────────────────────
#  CONFIGURAÇÃO
# ──────────────────────────────────────────────

TEST_MODE            = True
TEST_DB_PATH         = "rumores_test.db"
CANAL_EDITORIAL_ID   = 1449087228239155270        # ← ID do canal #rumores-pendentes
GEMINI_MOCK          = True     # ← False para usar tokens reais
PROPAGATION_INTERVAL = 60       # segundos (use 3600 em prod)
MAX_HOPS             = 3
DISTORTION_CHANCE    = 0.4

# ──────────────────────────────────────────────
#  MOCK DATA
# ──────────────────────────────────────────────

MOCK_LORE_UPDATES = [
    {"player_id": 111, "personagem": "Kael Sombras", "texto": "meu personagem perdeu o braço esquerdo numa batalha contra a Ordem da Chama"},
    {"player_id": 222, "personagem": "Lyra Voss",    "texto": "meu personagem descobriu que seu pai era um espião da coroa"},
    {"player_id": 333, "personagem": "Doran Pedra",  "texto": "meu personagem quer ajudar Kael Sombras a recuperar uma relíquia perdida"},
    {"player_id": 444, "personagem": "Seraphina",    "texto": "meu personagem abriu uma taverna secreta nos subterrâneos da cidade"},
    {"player_id": 555, "personagem": "Vex",          "texto": "meu personagem traiu a Guilda dos Sussurros e fugiu para o norte"},
]

MOCK_NOS_MUNDO = [
    "Taverna do Corvo",
    "Guilda da Prata",
    "Mercado Sul",
    "Torre do Vigia",
    "Porto das Almas",
]

DISTORTIONS = {
    "Taverna do Corvo":  ["dizem que", "um bêbado jurou que", "ouvi de um viajante que"],
    "Guilda da Prata":   ["fontes confiáveis indicam que", "há rumores internos de que"],
    "Mercado Sul":       ["um comerciante sussurrou que", "as fofocas do mercado dizem que"],
    "Torre do Vigia":    ["os vigias relataram que", "foi avistado que"],
    "Porto das Almas":   ["marinheiros falam que", "chegou um navio com notícias de que"],
}

OMIT_WORDS = ["esquerdo", "direito", "secreto", "secreta", "espião", "traiu"]


# ──────────────────────────────────────────────
#  DATABASE
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
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id  INTEGER NOT NULL,
                    personagem TEXT    NOT NULL,
                    texto_raw  TEXT    NOT NULL,
                    processado INTEGER DEFAULT 0,
                    created_at TEXT    DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS lore_canonico (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    lore_raw_id INTEGER REFERENCES lore_raw(id),
                    personagem  TEXT NOT NULL,
                    fato        TEXT NOT NULL,
                    tipo_evento TEXT,
                    created_at  TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS rumores_pendentes (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    lore_canonico_id INTEGER REFERENCES lore_canonico(id),
                    fato_original    TEXT NOT NULL,
                    rumor_texto      TEXT,
                    status           TEXT DEFAULT 'aguardando',
                    message_id       INTEGER,
                    created_at       TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS rumores_ativos (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    rumor_id   INTEGER REFERENCES rumores_pendentes(id),
                    no_atual   TEXT    NOT NULL,
                    texto_atual TEXT   NOT NULL,
                    hop        INTEGER DEFAULT 0,
                    postado    INTEGER DEFAULT 0,
                    postado_em TEXT,
                    created_at TEXT    DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS grafo_nos (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome     TEXT UNIQUE NOT NULL,
                    tipo     TEXT NOT NULL,
                    canal_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS grafo_arestas (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    no_origem  TEXT REFERENCES grafo_nos(nome),
                    no_destino TEXT REFERENCES grafo_nos(nome),
                    peso       REAL DEFAULT 1.0
                );
            """)

    def inserir_lore_raw(self, player_id, personagem, texto):
        with self._conn() as conn:
            return conn.execute(
                "INSERT INTO lore_raw (player_id, personagem, texto_raw) VALUES (?,?,?)",
                (player_id, personagem, texto)
            ).lastrowid

    def buscar_lores_pendentes(self):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM lore_raw WHERE processado=0").fetchall()

    def marcar_processado(self, lore_id):
        with self._conn() as conn:
            conn.execute("UPDATE lore_raw SET processado=1 WHERE id=?", (lore_id,))

    def inserir_lore_canonico(self, lore_raw_id, personagem, fato, tipo_evento):
        with self._conn() as conn:
            return conn.execute(
                "INSERT INTO lore_canonico (lore_raw_id, personagem, fato, tipo_evento) VALUES (?,?,?,?)",
                (lore_raw_id, personagem, fato, tipo_evento)
            ).lastrowid

    def criar_rumor_pendente(self, lore_canonico_id, fato_original):
        with self._conn() as conn:
            return conn.execute(
                "INSERT INTO rumores_pendentes (lore_canonico_id, fato_original) VALUES (?,?)",
                (lore_canonico_id, fato_original)
            ).lastrowid

    def atualizar_rumor(self, rumor_id, status, rumor_texto=None, message_id=None):
        with self._conn() as conn:
            conn.execute("""
                UPDATE rumores_pendentes
                SET status=?, rumor_texto=COALESCE(?,rumor_texto), message_id=COALESCE(?,message_id)
                WHERE id=?
            """, (status, rumor_texto, message_id, rumor_id))

    def buscar_rumores_aprovados_nao_propagados(self):
        with self._conn() as conn:
            return conn.execute("""
                SELECT * FROM rumores_pendentes
                WHERE status IN ('aprovado_auto','aprovado_manual')
                AND id NOT IN (SELECT DISTINCT rumor_id FROM rumores_ativos)
            """).fetchall()

    def inserir_no_ativo(self, rumor_id, no_atual, texto_atual, hop):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO rumores_ativos (rumor_id, no_atual, texto_atual, hop) VALUES (?,?,?,?)",
                (rumor_id, no_atual, texto_atual, hop)
            )

    def buscar_ativos_nao_postados(self):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM rumores_ativos WHERE postado=0").fetchall()

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
            nos     = conn.execute("SELECT nome, tipo FROM grafo_nos").fetchall()
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
#  GEMINI WRAPPER
# ──────────────────────────────────────────────

async def gemini_extrair_fatos(updates: list) -> list:
    if GEMINI_MOCK:
        tipo_map = {
            "perdeu": "perda", "traiu": "traição", "quer ajudar": "aliança",
            "descobriu": "descoberta", "abriu": "criação", "fugiu": "fuga",
        }
        resultado = []
        for u in updates:
            tipo = "evento"
            for kw, t in tipo_map.items():
                if kw in u["texto"].lower():
                    tipo = t
                    break
            fato = u["texto"].strip().rstrip(".")
            fato = fato[0].upper() + fato[1:] if fato else fato
            resultado.append({"personagem": u["personagem"], "fato": fato, "tipo_evento": tipo})
        return resultado

    try:
        import google.generativeai as genai
        model  = genai.GenerativeModel("gemini-2.0-flash")
        batch  = "\n".join(f"- personagem '{u['personagem']}': {u['texto']}" for u in updates)
        prompt = (
            "Para cada item abaixo extraia em JSON: personagem, fato (frase limpa), "
            "tipo_evento (perda|aliança|traição|descoberta|criação|fuga|outro). "
            "Responda APENAS com array JSON, sem markdown.\n\n" + batch
        )
        resp = model.generate_content(prompt)
        text = resp.text.strip().lstrip("```json").rstrip("```").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[RUMORES] Erro Gemini extrair_fatos: {e}")
        return []


async def gemini_gerar_rumor(fato: str, personagem: str) -> str:
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
        model  = genai.GenerativeModel("gemini-2.0-flash")
        prompt = (
            f"Transforme o fato abaixo em um rumor vago de taverna, como alguém que "
            f"ouviu de terceiros contaria. Máximo 2 frases. Não mencione fontes diretas. "
            f"Fato: {fato}"
        )
        return (await asyncio.to_thread(model.generate_content, prompt)).text.strip()
    except Exception as e:
        print(f"[RUMORES] Erro Gemini gerar_rumor: {e}")
        return f"Dizem que algo aconteceu com {personagem}..."


# ──────────────────────────────────────────────
#  DISTORÇÃO SEM IA
# ──────────────────────────────────────────────

def distorcer_texto(texto: str, no_nome: str, hop: int) -> str:
    prefixo   = random.choice(DISTORTIONS.get(no_nome, ["ouvi dizer que"]))
    resultado = texto
    if hop >= 2:
        for palavra in OMIT_WORDS:
            resultado = resultado.replace(palavra, "").replace("  ", " ")
    if hop >= 3:
        resultado = resultado.rstrip(".") + ", mas ninguém sabe ao certo..."
    palavras_inicio = ["dizem", "correm", "sussurros", "ouvi", "ninguém", "fontes",
                       "um", "uma", "marinheiros", "os", "foi"]
    if not any(resultado.lower().startswith(p) for p in palavras_inicio):
        resultado = f"{prefixo} {resultado[0].lower()}{resultado[1:]}"
    return resultado.strip()


# ──────────────────────────────────────────────
#  VIEWS
# ──────────────────────────────────────────────

class RumorManualModal(discord.ui.Modal, title="Escrever Rumor"):
    rumor = discord.ui.TextInput(
        label="Texto do rumor",
        style=discord.TextStyle.paragraph,
        placeholder="Como este fato chegaria aos ouvidos do povo?",
        max_length=400,
    )

    def __init__(self, db: RumoresDB, rumor_id, fato, personagem, message):
        super().__init__()
        self.db              = db
        self.rumor_id        = rumor_id
        self.fato            = fato
        self.personagem      = personagem
        self.original_message = message

    async def on_submit(self, interaction: discord.Interaction):
        self.db.atualizar_rumor(self.rumor_id, "aprovado_manual", rumor_texto=str(self.rumor))
        embed = discord.Embed(title="✍️ Aprovado — rumor escrito manualmente", color=discord.Color.blue())
        embed.add_field(name="Fato original", value=self.fato,       inline=False)
        embed.add_field(name="Rumor",         value=str(self.rumor), inline=False)
        embed.set_footer(text=f"Personagem: {self.personagem}")
        await self.original_message.edit(embed=embed, view=None)
        await interaction.response.send_message("Rumor salvo e na fila!", ephemeral=True)


class PainelEditorialView(discord.ui.View):
    def __init__(self, db: RumoresDB, rumor_id: int, fato: str, personagem: str):
        super().__init__(timeout=None)
        self.db         = db
        self.rumor_id   = rumor_id
        self.fato       = fato
        self.personagem = personagem

    def _embed(self, titulo, rumor, cor):
        e = discord.Embed(title=titulo, color=cor)
        e.add_field(name="Fato original", value=self.fato, inline=False)
        if rumor:
            e.add_field(name="Rumor", value=rumor, inline=False)
        e.set_footer(text=f"Personagem: {self.personagem}")
        return e

    @discord.ui.button(label="✅ Gerar rumor automaticamente", style=discord.ButtonStyle.success, custom_id="rumor_auto")
    async def auto(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        rumor_texto = await gemini_gerar_rumor(self.fato, self.personagem)
        self.db.atualizar_rumor(self.rumor_id, "aprovado_auto", rumor_texto=rumor_texto)
        await interaction.message.edit(embed=self._embed("✅ Aprovado — gerado automaticamente", rumor_texto, discord.Color.green()), view=None)
        await interaction.followup.send("Rumor na fila de propagação!", ephemeral=True)

    @discord.ui.button(label="✍️ Escrever manualmente", style=discord.ButtonStyle.primary, custom_id="rumor_manual")
    async def manual(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RumorManualModal(self.db, self.rumor_id, self.fato, self.personagem, interaction.message)
        )

    @discord.ui.button(label="🔒 Manter secreto", style=discord.ButtonStyle.secondary, custom_id="rumor_secreto")
    async def secreto(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.db.atualizar_rumor(self.rumor_id, "secreto")
        await interaction.message.edit(embed=self._embed("🔒 Marcado como secreto", "", discord.Color.greyple()), view=None)
        await interaction.response.send_message("Lore mantido em segredo.", ephemeral=True)

    @discord.ui.button(label="🗑️ Descartar", style=discord.ButtonStyle.danger, custom_id="rumor_descartar")
    async def descartar(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.db.atualizar_rumor(self.rumor_id, "descartado")
        await interaction.message.edit(embed=self._embed("🗑️ Descartado", "", discord.Color.red()), view=None)
        await interaction.response.send_message("Rumor descartado.", ephemeral=True)


# ──────────────────────────────────────────────
#  COG PRINCIPAL
# ──────────────────────────────────────────────

class RumoresCog(commands.Cog, name="Rumores"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db  = RumoresDB(TEST_DB_PATH)
        self._setup_grafo_teste()
        if TEST_MODE:
            self._seed_mock_data()
        self.job_processar.start()
        self.job_propagar.start()
        print(f"[RUMORES] Cog carregada. TEST_MODE={TEST_MODE} | GEMINI_MOCK={GEMINI_MOCK}")

    async def cog_load(self):
        self.bot.tree.add_command(rumores_group)

    async def cog_unload(self):
        self.bot.tree.remove_command("rumores")
        self.job_processar.cancel()
        self.job_propagar.cancel()

    def _setup_grafo_teste(self):
        for no in MOCK_NOS_MUNDO:
            self.db.inserir_no(no, "local")
        nos = MOCK_NOS_MUNDO
        for i in range(len(nos) - 1):
            self.db.inserir_aresta(nos[i], nos[i + 1], round(random.uniform(0.5, 1.0), 2))
        self.db.inserir_aresta(nos[0], nos[2], 0.6)
        self.db.inserir_aresta(nos[1], nos[4], 0.4)
        self.db.inserir_aresta(nos[3], nos[0], 0.3)

    def _seed_mock_data(self):
        if not self.db.buscar_lores_pendentes():
            print("[RUMORES][TEST] Inserindo mock data...")
            for m in MOCK_LORE_UPDATES:
                self.db.inserir_lore_raw(m["player_id"], m["personagem"], m["texto"])
            print(f"[RUMORES][TEST] {len(MOCK_LORE_UPDATES)} lores mock inseridos.")

    @tasks.loop(seconds=PROPAGATION_INTERVAL)
    async def job_processar(self):
        pendentes = self.db.buscar_lores_pendentes()
        if not pendentes:
            return
        print(f"[RUMORES] Processando {len(pendentes)} lore(s) pendente(s)...")
        updates = [{"personagem": r["personagem"], "texto": r["texto_raw"]} for r in pendentes]
        fatos   = await gemini_extrair_fatos(updates)
        canal   = self.bot.get_channel(CANAL_EDITORIAL_ID)
        for i, raw in enumerate(pendentes):
            if i >= len(fatos):
                break
            f        = fatos[i]
            cid      = self.db.inserir_lore_canonico(raw["id"], f["personagem"], f["fato"], f["tipo_evento"])
            rumor_id = self.db.criar_rumor_pendente(cid, f["fato"])
            self.db.marcar_processado(raw["id"])
            if canal:
                await self._postar_editorial(canal, rumor_id, f["fato"], f["personagem"], f["tipo_evento"])
            else:
                print(f"[RUMORES] ⚠️ CANAL_EDITORIAL_ID não configurado. Rumor ID {rumor_id} aguarda canal.")

    @job_processar.before_loop
    async def before_processar(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)

    @tasks.loop(seconds=PROPAGATION_INTERVAL * 2)
    async def job_propagar(self):
        aprovados = self.db.buscar_rumores_aprovados_nao_propagados()
        if not aprovados:
            return
        G   = self.db.carregar_grafo()
        nos = list(G.nodes())
        if not nos:
            return
        for rumor in aprovados:
            no_inicial = random.choice(nos)
            print(f"[RUMORES] Propagando rumor ID {rumor['id']} a partir de '{no_inicial}'")
            await self._propagar_bfs(G, rumor["id"], no_inicial, rumor["rumor_texto"])

    @job_propagar.before_loop
    async def before_propagar(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(15)

    async def _propagar_bfs(self, G: nx.Graph, rumor_id: int, no_inicial: str, texto: str):
        visitados = set()
        fila = [(no_inicial, texto, 0)]
        while fila:
            no_atual, texto_atual, hop = fila.pop(0)
            if no_atual in visitados or hop > MAX_HOPS:
                continue
            visitados.add(no_atual)
            texto_dist = distorcer_texto(texto_atual, no_atual, hop) if hop > 0 else texto_atual
            self.db.inserir_no_ativo(rumor_id, no_atual, texto_dist, hop)
            for vizinho in G.neighbors(no_atual):
                if vizinho not in visitados:
                    peso = G[no_atual][vizinho].get("weight", 1.0)
                    if random.random() <= peso:
                        fila.append((vizinho, texto_dist, hop + 1))

    async def _postar_editorial(self, canal, rumor_id, fato, personagem, tipo_evento):
        cores = {
            "perda": discord.Color.red(),       "traição": discord.Color.dark_red(),
            "aliança": discord.Color.blue(),     "descoberta": discord.Color.gold(),
            "criação": discord.Color.green(),    "fuga": discord.Color.orange(),
        }
        embed = discord.Embed(
            title=f"📜 Novo lore processado — `{tipo_evento.upper()}`",
            color=cores.get(tipo_evento, discord.Color.greyple()),
            timestamp=datetime.now()
        )
        embed.add_field(name="Personagem",     value=personagem,  inline=True)
        embed.add_field(name="Tipo de Evento", value=tipo_evento, inline=True)
        embed.add_field(name="Fato Canônico",  value=f"*{fato}*", inline=False)
        if TEST_MODE:
            embed.set_footer(text="⚠️ MODO TESTE — dados fictícios")
        view = PainelEditorialView(self.db, rumor_id, fato, personagem)
        msg  = await canal.send(embed=embed, view=view)
        self.db.atualizar_rumor(rumor_id, "aguardando", message_id=msg.id)


# ──────────────────────────────────────────────
#  GRUPO — fora da classe (padrão lore_ai.py)
# ──────────────────────────────────────────────

rumores_group = app_commands.Group(name="rumores", description="Sistema de Rumores")


def _db(interaction: discord.Interaction) -> RumoresDB:
    return interaction.client.get_cog("Rumores").db


def _cog(interaction: discord.Interaction) -> RumoresCog:
    return interaction.client.get_cog("Rumores")


@rumores_group.command(name="status", description="Mostra o estado atual do sistema de rumores")
async def cmd_status(interaction: discord.Interaction):
    db        = _db(interaction)
    raws      = db.listar_tudo("lore_raw")
    canonicos = db.listar_tudo("lore_canonico")
    pendentes = db.listar_tudo("rumores_pendentes")
    ativos    = db.listar_tudo("rumores_ativos")
    nos       = db.listar_tudo("grafo_nos")
    aguardando = sum(1 for r in pendentes if r["status"] == "aguardando")
    aprovados  = sum(1 for r in pendentes if "aprovado" in r["status"])
    secretos   = sum(1 for r in pendentes if r["status"] == "secreto")
    embed = discord.Embed(title="🕸️ Sistema de Rumores — Status", color=discord.Color.teal(), timestamp=datetime.now())
    embed.add_field(name="📥 Lores Raw",          value=str(len(raws)),      inline=True)
    embed.add_field(name="📖 Lores Canônicos",    value=str(len(canonicos)), inline=True)
    embed.add_field(name="🗺️ Nós no Grafo",       value=str(len(nos)),       inline=True)
    embed.add_field(name="⏳ Aguardando revisão", value=str(aguardando),     inline=True)
    embed.add_field(name="✅ Aprovados",           value=str(aprovados),      inline=True)
    embed.add_field(name="🔒 Secretos",           value=str(secretos),       inline=True)
    embed.add_field(name="🌐 Propagando",         value=str(len(ativos)),    inline=True)
    if TEST_MODE:
        embed.set_footer(text=f"⚠️ MODO TESTE | DB: {TEST_DB_PATH} | GEMINI_MOCK: {GEMINI_MOCK}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@rumores_group.command(name="grafo", description="Mostra os nós e arestas do grafo de propagação")
async def cmd_grafo(interaction: discord.Interaction):
    G = _db(interaction).carregar_grafo()
    nos_txt     = "\n".join(f"• `{n}`" for n in G.nodes())
    arestas_txt = "\n".join(
        f"• `{u}` ↔ `{v}` (peso: {d.get('weight', 1.0):.1f})"
        for u, v, d in G.edges(data=True)
    )
    embed = discord.Embed(title="🕸️ Grafo de Propagação", color=discord.Color.purple())
    embed.add_field(name=f"Nós ({G.number_of_nodes()})",     value=nos_txt     or "—", inline=False)
    embed.add_field(name=f"Arestas ({G.number_of_edges()})", value=arestas_txt or "—", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@rumores_group.command(name="ativos", description="Lista os rumores atualmente se propagando")
async def cmd_ativos(interaction: discord.Interaction):
    ativos = _db(interaction).buscar_ativos_nao_postados()
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
        embed.set_footer(text=f"... e mais {len(ativos) - 10} rumores")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@rumores_group.command(name="simular_ticket", description="[TESTE] Simula um ticket de atualização de lore")
@app_commands.describe(personagem="Nome do personagem", update="O que aconteceu com ele")
async def cmd_simular_ticket(interaction: discord.Interaction, personagem: str, update: str):
    lore_id = _db(interaction).inserir_lore_raw(interaction.user.id, personagem, update)
    embed = discord.Embed(
        title="🎫 Ticket Simulado",
        description=f"Lore raw inserido com ID `{lore_id}`.\nSerá processado no próximo ciclo do job.",
        color=discord.Color.blue()
    )
    embed.add_field(name="Personagem", value=personagem, inline=True)
    embed.add_field(name="Update",     value=update,     inline=False)
    embed.set_footer(text="⚠️ MODO TESTE")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@rumores_group.command(name="forcar_job", description="[TESTE] Força execução imediata dos jobs")
async def cmd_forcar_job(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    cog = _cog(interaction)
    await cog.job_processar()
    await cog.job_propagar()
    await interaction.followup.send("✅ Jobs executados. Use `/rumores status` para ver o resultado.", ephemeral=True)


@rumores_group.command(name="limpar_teste", description="[TESTE] Apaga todos os dados do banco de teste")
async def cmd_limpar_teste(interaction: discord.Interaction):
    if not TEST_MODE:
        await interaction.response.send_message("❌ Só pode ser usado em TEST_MODE.", ephemeral=True)
        return
    cog = _cog(interaction)
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    cog.db = RumoresDB(TEST_DB_PATH)
    cog._setup_grafo_teste()
    cog._seed_mock_data()
    await interaction.response.send_message("🗑️ Banco de teste limpo e reiniciado com mock data.", ephemeral=True)


# ──────────────────────────────────────────────
#  SETUP
# ──────────────────────────────────────────────

async def setup(bot: commands.Bot):
    await bot.add_cog(RumoresCog(bot))
    print("[RUMORES] Cog registrada com sucesso.")