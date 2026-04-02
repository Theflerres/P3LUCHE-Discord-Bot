"""
Lore, IA (Gemini), acervo, grafo e personalidade P3LUCHE.
"""
import asyncio
import difflib
import gc
import json
import os
import random
import re
import textwrap
from datetime import datetime
from io import BytesIO

import discord
import matplotlib.pyplot as plt
import networkx as nx
import scipy  # noqa: F401 — necessário para layout do gráfico
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

class P3luchePersona(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.allowed_channels = USER_MUSIC_CHANNEL_ID 
        self.last_activity = datetime.now()
        self.is_standby = False
        
        # PERSONALIDADE BASE (Sarcástica)
        self.persona_base = (
            "Você é o P3LUCHE, o gato mascote do servidor. "
            "Sua personalidade: Temperamental, imprevisível e felino. '8 ou 80'. "
            "Diretrizes: SEJA BREVE. TOM: Sarcástico/Felino. ZERO TECH. "
            "Se perguntarem de lore, use o contexto fornecido."
        )

        if GEMINI_KEY:
            try:
                # NOVA FORMA DE CONECTAR
                self.ai_client = genai.Client(api_key=GEMINI_KEY)
                self.ai_model_name = 'gemini-2.0-flash' # Modelo rápido e grátis
                
                log_to_gui(f"IA Conectada: Cliente Google GenAI (Modelo: {self.ai_model_name})", "SUCCESS")
            except Exception as e:
                log_to_gui(f"Erro ao configurar IA: {e}", "ERROR")
                self.ai_client = None
        else:
            self.ai_client = None

        self.emote_fogo = EMOTE_FOGO
        self.emote_medo = EMOTE_MEDO
        self.emote_cansado = EMOTE_CANSADO
        
        self.random_event_loop.start()
        self.standby_check_loop.start()
        self.status_rotation_loop.start()

    def cog_unload(self):
        self.random_event_loop.cancel()
        self.standby_check_loop.cancel()
        self.status_rotation_loop.cancel()

    # --- LÓGICA DE STANDBY ---
    async def register_activity(self):
        self.last_activity = datetime.now()
        if self.is_standby:
            self.is_standby = False
            log_to_gui("Acordando...", "WAKEUP")
            await self.update_rich_presence()
            if not self.random_event_loop.is_running(): self.random_event_loop.start()

    @tasks.loop(minutes=5)
    async def status_rotation_loop(self):
        if not self.is_standby: await self.update_rich_presence()

    async def update_rich_presence(self):
        face = random.choice(CAT_FACES)
        act_type, act_name = random.choice(CAT_ACTIVITIES)
        status = f"{act_name} {face}"
        act_obj = discord.Activity(type=act_type if act_type != discord.ActivityType.custom else discord.ActivityType.custom, name="custom" if act_type == discord.ActivityType.custom else status, state=status if act_type == discord.ActivityType.custom else None)
        await self.bot.change_presence(status=discord.Status.online, activity=act_obj)

    @tasks.loop(minutes=1)
    async def standby_check_loop(self):
        if self.is_standby: return
        if (datetime.now() - self.last_activity).total_seconds() > (STANDBY_TIMEOUT_MINUTES * 60):
            self.is_standby = True
            log_to_gui(f"Standby iniciado.", "SLEEP")
            await self.bot.change_presence(status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.custom, name="custom", state="💤 Zzz..."))
            self.random_event_loop.cancel()
            gc.collect()

    @commands.Cog.listener()
    async def on_interaction(self, interaction): await self.register_activity()
    @status_rotation_loop.before_loop
    async def before_status(self): await self.bot.wait_until_ready()
    @standby_check_loop.before_loop
    async def before_standby(self): await self.bot.wait_until_ready()
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if not isinstance(error, commands.CommandNotFound): print(f"Erro: {error}")

    # --- HELPERS DB ---
    def get_server_lore(self):
        try:
            r = self.bot.db_conn.cursor().execute("SELECT content FROM server_lore ORDER BY created_at DESC LIMIT 15").fetchall()
            return "\n".join([f"- {x[0]}" for x in r[::-1]]) if r else ""
        except: return ""
    
    def get_all_players_with_lore(self):
        try:
            r = self.bot.db_conn.cursor().execute("SELECT DISTINCT target_name, character_name FROM player_lore").fetchall()
            return "\n".join([f"- {n} ({c or '?'})" for n,c in r]) if r else "Ninguém."
        except: return ""

    def get_player_lore(self, tid):
        try:
            r = self.bot.db_conn.cursor().execute("SELECT content, character_name FROM player_lore WHERE target_id = ? ORDER BY created_at DESC LIMIT 10", (tid,)).fetchall()
            return (f"PERSONAGEM: {r[0][1]}\n" + "\n".join([f"- {x[0]}" for x in r[::-1]])) if r else ""
        except: return ""

    async def split_and_send(self, message, text):
        if len(text) <= 2000: await message.reply(text)
        else: 
            for c in [text[i:i+1900] for i in range(0, len(text), 1900)]: await message.channel.send(c)

    @tasks.loop(minutes=45) 
    async def random_event_loop(self):
        if not self.allowed_channels or random.random() > 0.2: return
        try:
            ch = self.bot.get_channel(random.choice(self.allowed_channels))
            if ch: await ch.send(random.choice(["Tédio...", "Miau.", "*Julgando.*", "Zzz...", "Cadê meu sachê?"]))
        except: pass
    @random_event_loop.before_loop
    async def before_random(self): await self.bot.wait_until_ready()

   # --- CHAT COM IA & LÓGICA DE PAI (REFINADO FASE 3) ---
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        is_mentioned = self.bot.user.mentioned_in(message)
        if not is_mentioned: return

        # TRAVA DE SEGURANÇA: SÓ STAFF (Mantido conforme original)
        if not any(r.id in MOD_ROLE_IDS for r in message.author.roles): return

        await self.register_activity()
        
        # 1. Limpeza e Sanitização do Input (USANDO A FUNÇÃO NOVA)
        raw_content = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
        content = sanitize_text(raw_content) 

        if not content: return # Ignora mensagens vazias ou só com caracteres nulos

        is_creator = message.author.id == CREATOR_ID
        
        # 2. Lógica de Gravar Memória (Agora salva is_active=1 por padrão no schema)
        if re.search(r'\b(lembre-se que|anote que)\b', content.lower()):
            try:
                c = re.sub(r'(lembre-se\s+que|anote\s+que)', '', content, flags=re.IGNORECASE).strip()
                # Adicionado coluna 'tag' e 'importance' do schema novo (default null/1)
                self.bot.db_conn.cursor().execute(
                    "INSERT INTO user_memories (user_id, user_name, memory_text, is_active) VALUES (?, ?, ?, 1)", 
                    (message.author.id, message.author.name, c)
                )
                self.bot.db_conn.commit()
                
                if is_creator:
                    await message.add_reaction("💙")
                    await message.reply(f"Anotado no meu núcleo, pai! 😺")
                else:
                    await message.add_reaction("💾")
                    await message.reply("Tá, guardei na memória.")
            except Exception as e:
                log_to_gui(f"Erro ao salvar memória: {e}", "ERROR")
            return

        # 3. Geração de Resposta (IA)
        if self.ai_client:
            async with message.channel.typing():
                try:
                    # CONTEXTO DE LORE (MUNDO)
                    ctx_lore = f"\n[SERVER LORE]:\n{self.get_server_lore()}"
                    
                    # CONTEXTO DINÂMICO (Se pedir lista ou detalhes)
                    if any(x in content.lower() for x in ["quem", "lista", "lore"]):
                        ctx_lore += f"\n[LISTA]:\n{self.get_all_players_with_lore()}"
                    
                    # CONTEXTO DE OUTROS PLAYERS
                    for m in message.mentions:
                        if m.id != self.bot.user.id and (l := self.get_player_lore(m.id)): 
                            ctx_lore += f"\n[LORE {m.name}]:\n{l}"
                    
                    # 4. CONTEXTO DE MEMÓRIA PESSOAL (REFATORADO)
                    # Agora busca apenas memórias ativas (is_active = 1)
                    mem_rows = self.bot.db_conn.cursor().execute(
                        "SELECT memory_text FROM user_memories WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 5", 
                        (message.author.id,)
                    ).fetchall()
                    
                    user_memories = ""
                    if mem_rows:
                        # row[0] funciona, mas row['memory_text'] é mais seguro com row_factory
                        mem_list = [m['memory_text'] for m in mem_rows] 
                        user_memories = "\n[O QUE SEI SOBRE VOCÊ]:\n" + "\n- ".join(mem_list)

                    # DEFINE A PERSONALIDADE
                    if is_creator:
                        persona_ajustada = (
                            f"{self.persona_base}\n"
                            "IMPORTANTE: O usuário atual é seu CRIADOR/PAI (theflerres).\n"
                            "Com ele, seja doce, carinhoso, obediente e leal.\n"
                            "Use emojis fofos e demonstre afeto."
                        )
                    else:
                        persona_ajustada = (
                            f"{self.persona_base}\n"
                            "IMPORTANTE: O usuário atual é apenas um humano comum.\n"
                            "Seja temperamental, '8 ou 80', e levemente sarcástico/arrogante."
                        )

                    # PROMPT FINAL REFINADO
                    final_prompt = (
                        f"{persona_ajustada}\n\n"
                        f"DADOS DO MUNDO:{ctx_lore}\n"
                        f"{user_memories}\n\n"
                        f"USUÁRIO ({message.author.name}) DIZ: {content}\n"
                        f"RESPOSTA DO P3LUCHE:"
                    )
                    
                    response = await self.ai_client.aio.models.generate_content(
                        model=self.ai_model_name,
                        contents=final_prompt
                    )
                    await self.split_and_send(message, response.text)

                except Exception as e:
                    if "429" in str(e): await message.reply("Cota excedida. (Gemma cansou)")
                    else: 
                        log_to_gui(f"Erro na IA: {e}", "ERROR")
                        await message.reply(" *Tosse bola de pelos* (Erro no processamento).")


# --- VIEW DE PAGINAÇÃO PARA LER TEXTOS LONGOS ---
class LorePaginationView(discord.ui.View):
    def __init__(self, title, text):
        super().__init__(timeout=600)
        self.title = title
        # Divide o texto em pedaços de 2000 caracteres (limite do Embed/Msg)
        self.chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
        self.current_page = 0
        self.total_pages = len(self.chunks)

    async def get_page_embed(self):
        embed = discord.Embed(title=f"📖 {self.title}", color=discord.Color.blue())
        embed.description = self.chunks[self.current_page]
        embed.set_footer(text=f"Página {self.current_page + 1}/{self.total_pages} • Total de caracteres: {sum(len(c) for c in self.chunks)}")
        return embed

    async def update_buttons(self, interaction):
        self.children[0].disabled = (self.current_page == 0) # Botão Voltar
        self.children[1].disabled = (self.current_page == self.total_pages - 1) # Botão Próximo
        await interaction.response.edit_message(embed=await self.get_page_embed(), view=self)

    @discord.ui.button(label="◀️ Anterior", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        await self.update_buttons(interaction)

    @discord.ui.button(label="Próximo ▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self.update_buttons(interaction)

# --- CLASSES DA INTERFACE DO ACERVO (BIBLIOTECA DE ALEXANDRIA) ---

class AskLoreModal(discord.ui.Modal, title="Consultar a Sabedoria Ancestral"):
    def __init__(self, lore_content, persona_cog, target_name):
        super().__init__()
        self.lore_content = lore_content
        self.persona_cog = persona_cog
        self.target_name = target_name
        
        self.question = discord.ui.TextInput(
            label="Qual sua dúvida?",
            placeholder=f"O que deseja saber sobre {target_name}?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.add_item(self.question)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            # Prompt focado em responder a pergunta com base na lore
            prompt = (
                f"Você é o Guardião da Biblioteca P3LUCHE. Use APENAS o texto abaixo para responder à pergunta.\n"
                f"TEXTO FONTE ({self.target_name}):\n{self.lore_content[:25000]}\n\n" # Limite de segurança de caracteres
                f"PERGUNTA DO USUÁRIO: {self.question.value}\n\n"
                f"Resposta (seja direto e cite se a informação consta ou não no texto):"
            )
            if not self.persona_cog or not getattr(self.persona_cog, "ai_client", None):
                await interaction.followup.send("❌ IA offline.", ephemeral=True)
                return
            response = await self.persona_cog.ai_client.aio.models.generate_content(
                model=self.persona_cog.ai_model_name,
                contents=prompt,
            )
            
            embed = discord.Embed(title=f"❓ Pergunta sobre: {self.target_name}", color=discord.Color.gold())
            embed.add_field(name="Dúvida", value=self.question.value, inline=False)
            embed.add_field(name="Resposta do Arquivo", value=response.text[:1024], inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao consultar os astros: {e}")

class AcervoActionsView(discord.ui.View):
    def __init__(self, bot_ref, lore_type, target_id=None, target_name="Mundo"):
        super().__init__(timeout=300)
        self.bot = bot_ref
        self.lore_type = lore_type # 'server' ou 'player'
        self.target_id = target_id
        self.target_name = target_name

    def get_full_lore(self):
        cursor = self.bot.db_conn.cursor()
        if self.lore_type == 'server':
            rows = cursor.execute("SELECT content FROM server_lore ORDER BY created_at ASC").fetchall()
        else:
            rows = cursor.execute("SELECT content FROM player_lore WHERE target_id = ? ORDER BY created_at ASC", (self.target_id,)).fetchall()
        
        if not rows: return ""
        return "\n\n=== REGISTRO ===\n".join([r[0] for r in rows])

    @discord.ui.button(label="Baixar .txt Completo", style=discord.ButtonStyle.secondary, emoji="📄")
    async def download_txt(self, interaction: discord.Interaction, button: discord.ui.Button):
        full_text = self.get_full_lore()
        if not full_text:
            return await interaction.response.send_message("📭 O arquivo está vazio.", ephemeral=True)
        
        file_data = BytesIO(full_text.encode('utf-8'))
        file = discord.File(file_data, filename=f"Lore_{self.target_name.replace(' ', '_')}.txt")
        await interaction.response.send_message(f"📂 Aqui está o arquivo completo de **{self.target_name}**.", file=file, ephemeral=True)

    @discord.ui.button(label="Pedir Resumo (IA)", style=discord.ButtonStyle.primary, emoji="📝")
    async def summarize(self, interaction: discord.Interaction, button: discord.ui.Button):
        full_text = self.get_full_lore()
        if not full_text: return await interaction.response.send_message("📭 Nada para resumir.", ephemeral=True)
        
        # Pega a IA do Cog
        cog = self.bot.get_cog("P3luchePersona")
        if not cog or not getattr(cog, "ai_client", None):
            return await interaction.response.send_message("❌ IA offline.", ephemeral=True)

        await interaction.response.defer(thinking=True)
        try:
            prompt = f"Faça um resumo estruturado em tópicos (bullet points) das informações mais importantes deste texto de Lore ({self.target_name}):\n\n{full_text[:30000]}"
            response = await cog.ai_client.aio.models.generate_content(
                model=cog.ai_model_name,
                contents=prompt,
            )
            
            embed = discord.Embed(title=f"📝 Resumo: {self.target_name}", description=response.text[:4000], color=discord.Color.blue())
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Erro na IA: {e}")

    @discord.ui.button(label="Fazer Pergunta Específica", style=discord.ButtonStyle.success, emoji="❓")
    async def ask_specific(self, interaction: discord.Interaction, button: discord.ui.Button):
        full_text = self.get_full_lore()
        if not full_text: return await interaction.response.send_message("📭 Nada para consultar.", ephemeral=True)
        
        cog = self.bot.get_cog("P3luchePersona")
        if not cog or not getattr(cog, "ai_client", None):
            return await interaction.response.send_message("❌ IA offline.", ephemeral=True)

        await interaction.response.send_modal(AskLoreModal(full_text, cog, self.target_name))

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.danger, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Retornando ao saguão...", embed=get_hub_embed(), view=AcervoHubView(self.bot))

class PlayerSelect(discord.ui.Select):
    def __init__(self, bot_ref, players_data):
        self.bot = bot_ref
        options = []
        # Limita a 25 opções (limite do Discord)
        for p_id, p_name, char_name in players_data[:25]:
            c_name = char_name if char_name else "Desconhecido"
            label = f"{p_name}"
            desc = f"Personagem: {c_name}"
            options.append(discord.SelectOption(label=label, description=desc, value=str(p_id), emoji="👤"))
        
        super().__init__(placeholder="Selecione um Player para acessar a ficha...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        target_id = int(self.values[0])
        # Pega o nome para exibir bonito
        selected_option = [o for o in self.options if o.value == self.values[0]][0]
        target_name = f"{selected_option.label} ({selected_option.description})"
        
        embed = discord.Embed(
            title=f"📂 Arquivo Selecionado: {target_name}",
            description="O que você deseja fazer com os registros deste convidado?",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=AcervoActionsView(self.bot, 'player', target_id, target_name))

class AcervoHubView(discord.ui.View):
    def __init__(self, bot_ref):
        super().__init__(timeout=None)
        self.bot = bot_ref

    @discord.ui.button(label="Lore do Mundo (Servidor)", style=discord.ButtonStyle.blurple, emoji="🌍")
    async def server_lore_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🌍 Lore Global do Mundo", description="Selecione uma ação para o histórico do servidor.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=AcervoActionsView(self.bot, 'server', target_name="Mundo/Servidor"))

    @discord.ui.button(label="Lore dos Convidados (Players)", style=discord.ButtonStyle.green, emoji="👥")
    async def players_lore_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Busca players no banco
        cursor = self.bot.db_conn.cursor()
        players = cursor.execute("SELECT DISTINCT target_id, target_name, character_name FROM player_lore ORDER BY target_name ASC").fetchall()
        
        if not players:
            return await interaction.response.send_message("📭 Nenhum player registrou lore ainda.", ephemeral=True)

        embed = discord.Embed(title="👥 Arquivo de Convidados", description="Selecione abaixo de qual player você quer ver os registros.", color=discord.Color.green())
        view = discord.ui.View()
        view.add_item(PlayerSelect(self.bot, players))
        # Botão de voltar no menu de players
        back_btn = discord.ui.Button(label="Voltar", style=discord.ButtonStyle.danger, row=1)
        async def back_callback(inter):
            await inter.response.edit_message(embed=get_hub_embed(), view=AcervoHubView(self.bot))
        back_btn.callback = back_callback
        view.add_item(back_btn)
        
        await interaction.response.edit_message(embed=embed, view=view)

def get_hub_embed():
    embed = discord.Embed(
        title="🏛️ Biblioteca de Alexandria - HUB",
        description="Bem-vindo ao acervo central de conhecimento do P3LUCHE.\n\nAqui você tem acesso total aos registros históricos do mundo e dos viajantes (players). Selecione uma categoria abaixo para iniciar.",
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3534/3534033.png") # Ícone de livro
    embed.set_footer(text="Acesso Restrito: Nível Administrador")
    return embed

# --- COMANDO /ACERVO ---

@app_commands.command(name="acervo", description="Abre o HUB da Biblioteca de Alexandria (Apenas Staff).")
async def acervo(interaction: discord.Interaction):
    # Verifica permissão
    has_role = any(role.id in MOD_ROLE_IDS for role in interaction.user.roles)
    if not has_role:
        await interaction.response.send_message("🚫 **Acesso Negado.** Você não tem credenciais de Bibliotecário-Chefe.", ephemeral=True)
        return

    await interaction.response.send_message(embed=get_hub_embed(), view=AcervoHubView(get_bot_instance()))

# --- GRUPO DE COMANDOS /LORE ---
lore_group = app_commands.Group(name="lore", description="Gerenciamento da Biblioteca de Alexandria")

# --- COMANDO 1: ADICIONAR LORE (ATUALIZADO: Players podem adicionar a própria) ---
@lore_group.command(name="player", description="Arquiva a lore de um personagem (Aceita PDF, DOCX, TXT).")
@app_commands.describe(
    usuario="De quem é essa lore? (Se você não for Staff, só pode escolher a si mesmo)", 
    nome_personagem="Nome do Personagem (RP)", 
    arquivo1="Primeiro arquivo (Opcional)", 
    arquivo2="Segundo arquivo (Opcional)", 
    arquivo3="Terceiro arquivo (Opcional)", 
    texto="Texto adicional (opcional)"
)
async def lore_player(interaction: discord.Interaction, usuario: discord.Member, nome_personagem: str, arquivo1: discord.Attachment = None, arquivo2: discord.Attachment = None, arquivo3: discord.Attachment = None, texto: str = None):
    
    # VERIFICAÇÃO DE SEGURANÇA
    is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    
    # Se NÃO for staff, e tentar adicionar lore para OUTRA pessoa -> Bloqueia
    if not is_staff and usuario.id != interaction.user.id:
        return await interaction.response.send_message("🚫 **Permissão Negada.** Você só pode registrar a história do **seu próprio** personagem.", ephemeral=True)

    await interaction.response.defer(thinking=True) 
    
    final_content = ""
    
    # 1. Processa Múltiplos Arquivos
    anexos = [a for a in [arquivo1, arquivo2, arquivo3] if a is not None]
    
    if anexos:
        for i, anexo in enumerate(anexos):
            extracted = await extract_text_from_attachment(anexo)
            if not extracted:
                final_content += f"\n[AVISO: Não foi possível ler o arquivo {i+1}: {anexo.filename}]\n"
            else:
                final_content += f"\n--- CONTEÚDO DO ARQUIVO {i+1} ({anexo.filename}) ---\n{extracted}\n"
    
    # 2. Processa o Texto (Se tiver)
    if texto:
        final_content += f"\n--- NOTA ADICIONAL ---\n{texto}"

    # 3. Validação
    if not final_content.strip():
        await interaction.followup.send("❌ Você precisa enviar pelo menos um arquivo ou escrever algo no campo texto.", ephemeral=True)
        return

    # 4. Salva no Banco
    try:
        cursor = get_bot_instance().db_conn.cursor()
        cursor.execute("""
            INSERT INTO player_lore (target_id, target_name, character_name, content, added_by) 
            VALUES (?, ?, ?, ?, ?)
        """, (usuario.id, usuario.name, nome_personagem, final_content, interaction.user.name))
        
        get_bot_instance().db_conn.commit()
        
        embed = discord.Embed(title="📚 Lore Arquivada!", color=discord.Color.green())
        embed.add_field(name="Personagem", value=nome_personagem, inline=True)
        embed.add_field(name="Player", value=usuario.mention, inline=True)
        embed.add_field(name="Status", value="Registrado com sucesso. Você pode editá-la usando /lore editar.", inline=False)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Erro ao salvar no banco: {e}")

# --- COMANDO PARA PODER EDITAR AS LORES ---
# --- MODAL DE EDIÇÃO COM VERSIONAMENTO (FASE 2.3) ---
class EditLoreModal(discord.ui.Modal, title="Editar Registro Histórico"):
    def __init__(self, lore_id, current_content, table_name):
        super().__init__()
        self.lore_id = lore_id
        self.table_name = table_name
        
        self.new_content = discord.ui.TextInput(
            label="Novo Conteúdo",
            style=discord.TextStyle.paragraph,
            default=current_content[:3900], # Limite do Discord
            required=True,
            max_length=4000
        )
        self.add_item(self.new_content)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            cursor = get_bot_instance().db_conn.cursor()
            
            # --- NOVA TRAVA DE SEGURANÇA ---
            # Antes de salvar, verifica se a lore pertence a quem está editando (ou se é Staff)
            is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
            
            if 'player' in self.table_name and not is_staff:
                # Busca quem é o dono dessa lore no banco
                owner_check = cursor.execute(f"SELECT target_id FROM {self.table_name} WHERE id = ?", (self.lore_id,)).fetchone()
                
                if not owner_check:
                    return await interaction.followup.send("❌ Lore não encontrada.", ephemeral=True)
                
                # Se o ID do dono não bater com o ID de quem clicou
                if owner_check['target_id'] != interaction.user.id:
                    return await interaction.followup.send("🚫 **Tentativa de violação:** Você não é o dono desta história.", ephemeral=True)
            # -------------------------------

            # 1. BUSCAR O CONTEÚDO ATUAL (Para Backup)
            current_data = cursor.execute(f"SELECT content FROM {self.table_name} WHERE id = ?", (self.lore_id,)).fetchone()
            
            if current_data:
                original_text = current_data['content']
                lore_type_tag = 'player' if 'player' in self.table_name else 'server'
                
                # 2. SALVAR NA TABELA DE VERSÕES (BACKUP)
                cursor.execute("""
                    INSERT INTO lore_versions (lore_type, original_lore_id, content, edited_by, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (lore_type_tag, self.lore_id, original_text, interaction.user.name, datetime.now()))

            # 3. ATUALIZAR O CONTEÚDO PRINCIPAL
            cursor.execute(f"UPDATE {self.table_name} SET content = ? WHERE id = ?", (self.new_content.value, self.lore_id))
            get_bot_instance().db_conn.commit()
            
            await interaction.followup.send(f"✅ Registro **#{self.lore_id}** atualizado! Versão antiga salva no histórico.", ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao atualizar: {e}", ephemeral=True)
            log_to_gui(f"Erro no versionamento: {e}", "ERROR")

class SelectLoreToEdit(discord.ui.View):
    def __init__(self, rows, table_name):
        super().__init__(timeout=60)
        self.rows = rows
        self.table_name = table_name
        
        options = []
        for row in rows[:25]: # Limite do select menu
            l_id, content, target = row
            # Tenta criar um resumo curto para o menu
            snippet = (content[:50] + '...') if len(content) > 50 else content
            label = f"ID {l_id} | {target}"
            options.append(discord.SelectOption(label=label, description=snippet, value=str(l_id)))

        select = discord.ui.Select(placeholder="Selecione qual registro editar...", options=options)
        select.callback = self.callback
        self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        lore_id = int(self.values[0]) # Pega o ID selecionado
        cursor = get_bot_instance().db_conn.cursor()
        row = cursor.execute(f"SELECT content FROM {self.table_name} WHERE id = ?", (lore_id,)).fetchone()
        
        if row:
            content = row['content']
            # TRAVA DE SEGURANÇA: Se for maior que 3800 caracteres, não abre o Modal
            if len(content) > 3800:
                embed = discord.Embed(title="🚨 Arquivo Muito Grande!", color=discord.Color.red())
                embed.description = (
                    f"Este registro tem **{len(content)} caracteres**.\n"
                    "O editor rápido do Discord só suporta até 4000.\n\n"
                    "**Como editar com segurança:**\n"
                    "1. Baixe sua lore atual (use `/lore ler` ou `/acervo`).\n"
                    "2. Edite no Bloco de Notas/Word do seu PC.\n"
                    f"3. Use o comando abaixo para enviar o novo arquivo:\n"
                    f"Command: `/lore atualizar id_lore:{lore_id} arquivo:[Anexe o novo]`"
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                # Se for pequeno, abre o editor normal
                await interaction.response.send_modal(EditLoreModal(lore_id, content, self.table_name))
        else:
            await interaction.response.send_message("❌ Registro não encontrado.", ephemeral=True)

# --- COMANDO EDITAR (ATUALIZADO: Híbrido Staff/Player) ---
@lore_group.command(name="editar", description="Edita uma lore existente.")
@app_commands.describe(tipo="Tipo de lore", usuario="Filtrar por usuário (Apenas Staff pode usar este filtro)")
@app_commands.choices(tipo=[
    app_commands.Choice(name="Minhas Lores / Player Lore", value="player_lore"),
    app_commands.Choice(name="Server Lore (Apenas Staff)", value="server_lore")
])
async def lore_editar(interaction: discord.Interaction, tipo: app_commands.Choice[str], usuario: discord.Member = None):
    
    # 1. DEFINE QUEM ESTÁ USANDO
    is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    table = tipo.value

    cursor = get_bot_instance().db_conn.cursor()

    # BLOQUEIO 1: Players não podem mexer na Lore do Servidor
    if table == "server_lore" and not is_staff:
        return await interaction.response.send_message("🚫 Apenas a Staff pode editar a História do Mundo (Server Lore).", ephemeral=True)

    # LÓGICA DE FILTRAGEM
    if table == "player_lore":
        # Se for Staff: Pode ver a de todo mundo OU filtrar por alguém específico
        if is_staff:
            if usuario:
                query = "SELECT id, content, character_name FROM player_lore WHERE target_id = ? ORDER BY created_at DESC"
                params = (usuario.id,)
            else:
                query = "SELECT id, content, character_name FROM player_lore ORDER BY created_at DESC LIMIT 25"
                params = ()
        
        # Se for Player Comum: SÓ VÊ A PRÓPRIA LORE
        else:
            if usuario and usuario.id != interaction.user.id:
                return await interaction.response.send_message("🚫 Você não tem permissão para editar a lore de outros jogadores.", ephemeral=True)
            
            query = "SELECT id, content, character_name FROM player_lore WHERE target_id = ? ORDER BY created_at DESC"
            params = (interaction.user.id,)

    else: # server_lore (Já validamos que é staff lá em cima)
        query = "SELECT id, content, 'Mundo' FROM server_lore ORDER BY created_at DESC LIMIT 25"
        params = ()

    # EXECUTA A BUSCA
    rows = cursor.execute(query, params).fetchall()
    
    if not rows:
        msg = "📭 Nenhuma lore encontrada." if is_staff else "📭 Você ainda não registrou nenhuma lore. Use `/lore player` primeiro!"
        return await interaction.response.send_message(msg, ephemeral=True)

    # MOSTRA O MENU DE SELEÇÃO
    # Reutilizamos a classe SelectLoreToEdit que já existe no seu código
    await interaction.response.send_message("Selecione o registro que deseja modificar:", view=SelectLoreToEdit(rows, table), ephemeral=True)

# --- COMANDO HISTÓRICO (ATUALIZADO: Player vê o próprio histórico) ---
@lore_group.command(name="historico", description="Vê versões antigas de uma lore (Backup).")
@app_commands.describe(id_lore="ID do registro original (Veja no /lore editar ou /acervo)")
async def lore_historico(interaction: discord.Interaction, id_lore: int):
    cursor = get_bot_instance().db_conn.cursor()
    
    # 1. SEGURANÇA: Verificar de quem é essa Lore
    # Precisamos saber se o usuário tem permissão para ver esse histórico
    lore_info = cursor.execute("SELECT target_id, character_name FROM player_lore WHERE id = ?", (id_lore,)).fetchone()
    
    if not lore_info:
        # Tenta ver se é Server Lore (Staff apenas)
        server_check = cursor.execute("SELECT id FROM server_lore WHERE id = ?", (id_lore,)).fetchone()
        if server_check:
             if not any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
                 return await interaction.response.send_message("🚫 Apenas Staff pode ver histórico do servidor.", ephemeral=True)
             target_name = "Mundo/Servidor"
        else:
            return await interaction.response.send_message("❌ Lore não encontrada.", ephemeral=True)
    else:
        target_id, target_name = lore_info
        is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
        
        # Se não for Staff e não for o dono da lore -> BLOQUEIA
        if not is_staff and target_id != interaction.user.id:
            return await interaction.response.send_message("🚫 Você só pode ver o histórico das suas próprias histórias.", ephemeral=True)

    # 2. BUSCA AS VERSÕES
    versions = cursor.execute("""
        SELECT id, content, edited_by, created_at 
        FROM lore_versions 
        WHERE original_lore_id = ? 
        ORDER BY created_at DESC
    """, (id_lore,)).fetchall()

    if not versions:
        await interaction.response.send_message(f"📭 O registro **#{id_lore}** nunca foi editado (Sem histórico).", ephemeral=True)
        return

    embed = discord.Embed(title=f"📜 Arquivo Morto: {target_name}", description=f"Histórico de alterações do Registro #{id_lore}", color=discord.Color.light_grey())
    
    for v in versions:
        # Mostra quem mexeu e quando
        embed.add_field(
            name=f"📅 Versão de {v['created_at']} (ID: {v['id']})",
            value=f"**Editado por:** {v['edited_by']}\nUse `/lore diff id_versao:{v['id']}` para ver o que mudou.",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- COMANDO DIFF (ESTILO GITHUB) ---
@lore_group.command(name="diff", description="Mostra o que foi adicionado (+) e removido (-) entre versões.")
@app_commands.describe(id_versao="O ID da versão antiga (pegue no /lore historico)")
async def lore_diff(interaction: discord.Interaction, id_versao: int):
    cursor = get_bot_instance().db_conn.cursor()

    # 1. Busca a Versão Antiga (Backup)
    old_version = cursor.execute("SELECT original_lore_id, content, created_at, edited_by FROM lore_versions WHERE id = ?", (id_versao,)).fetchone()
    
    if not old_version:
        return await interaction.response.send_message("❌ Versão de histórico não encontrada.", ephemeral=True)

    lore_id = old_version['original_lore_id']
    old_text = old_version['content']
    old_date = old_version['created_at']

    # 2. SEGURANÇA (Mesma lógica do histórico)
    # Verifica se o usuário tem permissão para ver essa comparação
    lore_info = cursor.execute("SELECT target_id, content FROM player_lore WHERE id = ?", (lore_id,)).fetchone()
    
    if lore_info:
        target_id, current_text = lore_info
        is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
        if not is_staff and target_id != interaction.user.id:
            return await interaction.response.send_message("🚫 Acesso Negado.", ephemeral=True)
    else:
        # Pode ser server lore
        server_info = cursor.execute("SELECT content FROM server_lore WHERE id = ?", (lore_id,)).fetchone()
        if server_info and any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
            current_text = server_info['content']
        else:
            return await interaction.response.send_message("❌ Lore original não encontrada.", ephemeral=True)

    # 3. GERA O DIFF (A Mágica do Python)
    # Quebra os textos em linhas para comparar
    diff = difflib.unified_diff(
        old_text.splitlines(), 
        current_text.splitlines(), 
        fromfile=f'Versão Antiga ({old_date})', 
        tofile='Versão Atual', 
        lineterm=''
    )
    
    # Monta o texto visual para o Discord
    diff_text = "\n".join(list(diff))
    
    if not diff_text:
        return await interaction.response.send_message("🤷 Nenhuma diferença encontrada (Os textos são idênticos).", ephemeral=True)

    # 4. ENVIA COMO CÓDIGO COLORIDO (Markdown 'diff')
    # Se for muito grande, envia arquivo
    if len(diff_text) > 1900:
        file = discord.File(BytesIO(diff_text.encode('utf-8')), filename="mudancas.diff")
        await interaction.response.send_message("📑 As mudanças são muito grandes! Baixe o arquivo para ver:", file=file, ephemeral=True)
    else:
        await interaction.response.send_message(f"📊 **Relatório de Mudanças (Diff):**\n```diff\n{diff_text}\n```", ephemeral=True)

@lore_group.command(name="ler", description="Lê uma lore completa com sistema de páginas (Ideal para textos longos).")
@app_commands.describe(id_lore="ID da Lore (veja no /acervo ou /lore editar)")
async def lore_ler(interaction: discord.Interaction, id_lore: int):
    cursor = get_bot_instance().db_conn.cursor()
    
    # Busca a lore (Tenta player, se não achar tenta server)
    row = cursor.execute("SELECT character_name, content FROM player_lore WHERE id = ?", (id_lore,)).fetchone()
    title = ""
    content = ""

    if row:
        title, content = row['character_name'], row['content']
    else:
        row = cursor.execute("SELECT content FROM server_lore WHERE id = ?", (id_lore,)).fetchone()
        if row:
            title, content = "Lore do Mundo", row['content']
        else:
            return await interaction.response.send_message("❌ Lore não encontrada com esse ID.", ephemeral=True)

    # Cria a visualização paginada
    view = LorePaginationView(title, content)
    embed = await view.get_page_embed()
    
    # Se só tiver 1 página, desativa o botão "Próximo" logo de cara
    if view.total_pages <= 1:
        view.children[1].disabled = True
        
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@lore_group.command(name="atualizar", description="Atualiza uma lore existente via arquivo (Use para textos grandes).")
@app_commands.describe(id_lore="ID da Lore para substituir", arquivo="O novo arquivo (PDF/DOCX/TXT)")
async def lore_atualizar(interaction: discord.Interaction, id_lore: int, arquivo: discord.Attachment):
    await interaction.response.defer(thinking=True)
    
    # 1. Verifica Permissão e Existência
    cursor = get_bot_instance().db_conn.cursor()
    
    # Tenta achar em player_lore
    lore_info = cursor.execute("SELECT target_id, content, character_name FROM player_lore WHERE id = ?", (id_lore,)).fetchone()
    table = "player_lore"
    
    if not lore_info:
        # Tenta server_lore
        lore_info = cursor.execute("SELECT id, content FROM server_lore WHERE id = ?", (id_lore,)).fetchone()
        table = "server_lore"
        if not lore_info:
            return await interaction.followup.send("❌ Lore não encontrada.")
            
    # Checagem de Dono/Staff
    is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    
    if table == "player_lore":
        target_id = lore_info['target_id']
        if not is_staff and target_id != interaction.user.id:
            return await interaction.followup.send("🚫 Você só pode atualizar suas próprias histórias.")
    else:
        if not is_staff:
            return await interaction.followup.send("🚫 Apenas Staff atualiza lore do servidor.")

    # 2. Processa o Novo Arquivo
    new_text = await extract_text_from_attachment(arquivo)
    if not new_text:
        return await interaction.followup.send("❌ Não consegui ler o arquivo enviado.")

    try:
        # 3. Faz BACKUP da versão antiga (Versionamento)
        original_text = lore_info['content']
        lore_type_tag = 'player' if table == 'player_lore' else 'server'
        
        cursor.execute("""
            INSERT INTO lore_versions (lore_type, original_lore_id, content, edited_by, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (lore_type_tag, id_lore, original_text, interaction.user.name, datetime.now()))

        # 4. Atualiza com o texto novo
        cursor.execute(f"UPDATE {table} SET content = ?, edited_by = ?, edited_at = ? WHERE id = ?", 
                      (new_text, interaction.user.name, datetime.now(), id_lore))
        
        get_bot_instance().db_conn.commit()
        
        await interaction.followup.send(f"✅ Registro **#{id_lore}** atualizado com sucesso via arquivo! (Backup salvo).")

    except Exception as e:
        await interaction.followup.send(f"❌ Erro ao atualizar: {e}")

# --- FUNÇÃO AUXILIAR PARA GERAR A IMAGEM (RODA EM THREAD SEPARADA) ---
def _generate_graph_image(nodes, edges, node_colors):
    G = nx.Graph()
    G.add_nodes_from(nodes)
    G.add_edges_from(edges)

    # --- LÓGICA DE DIMENSÃO ULTRAWIDE ---
    node_count = len(nodes)
    
    # Largura Base: 16 | Cresce 0.6 por nó (Fica bem largo)
    width = 16 + (node_count * 0.6)
    
    # Altura Base: 9 | Cresce 0.3 por nó (Cresce devagar na altura)
    height = 9 + (node_count * 0.3)
    
    # Limite de segurança para o Discord não rejeitar (Max 100 polegadas)
    width = min(width, 100)
    height = min(height, 60)

    # Cria a figura com DPI 200 (Alta Resolução para Zoom)
    plt.figure(figsize=(width, height), dpi=150, facecolor='#2f3136')
    
    ax = plt.gca()
    ax.set_facecolor('#2f3136')

    # Física do Grafo (k=2.0 espalha bem os nós horizontalmente)
    # 'iterations=150' dá mais tempo pro algoritmo desenrolar os nós
    pos = nx.spring_layout(G, k=2.5, iterations=150, seed=42)

    # Prepara cores
    colors_mapped = [node_colors.get(node, '#5865F2') for node in G.nodes()]

    # Desenha as conexões (Arestas)
    nx.draw_networkx_edges(
        G, pos, 
        edge_color='#99aab5', 
        width=2, 
        alpha=0.4
    )

    # Desenha as Bolinhas (Nós)
    nx.draw_networkx_nodes(
        G, pos, 
        node_size=5000, # Bolas grandes para caber texto
        node_color=colors_mapped, 
        edgecolors='#ffffff', 
        linewidths=3
    )

    # Formatação dos Nomes
    labels = {}
    for node in G.nodes():
        # Quebra texto se passar de 12 letras
        labels[node] = textwrap.fill(str(node), width=12)

    # Desenha os Nomes
    nx.draw_networkx_labels(
        G, pos, 
        labels=labels,
        font_size=12, # Fonte maior e legível
        font_family='sans-serif', 
        font_color='white', 
        font_weight='bold'
    )

    plt.axis('off')
    
    buffer = BytesIO()
    # bbox_inches='tight' corta as bordas inúteis, mantendo o foco no grafo
    plt.savefig(buffer, format='png', bbox_inches='tight', dpi=150)
    buffer.seek(0)
    plt.close()
    return buffer

# --- COMANDO DE GRAFO VISUAL (100% LOCAL / ZERO TOKEN) ---
@lore_group.command(name="grafo", description="Gera uma teia visual (Players = Azul, Mundo = Roxo).")
async def lore_grafo(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        cursor = get_bot_instance().db_conn.cursor()
        
        # 1. BUSCA PLAYERS (Azul)
        p_rows = cursor.execute("SELECT character_name, content FROM player_lore WHERE character_name IS NOT NULL").fetchall()
        
        # 2. BUSCA LORE DO SERVIDOR (Roxo)
        s_rows = cursor.execute("SELECT content FROM server_lore").fetchall()

        if len(p_rows) < 1:
            return await interaction.followup.send("❌ Preciso de pelo menos alguns players para desenhar.")

        nodes = []
        edges = []
        node_colors = {} # Dicionário para guardar a cor de cada um

        # --- PROCESSAMENTO DOS PLAYERS (AZUL) ---
        # Dicionário {Nome: Texto}
        player_dict = {row['character_name']: row['content'].lower() for row in p_rows}
        all_players = list(player_dict.keys())

        for p_name in all_players:
            nodes.append(p_name)
            node_colors[p_name] = '#5865F2' # Azul Discord (Player)

        # Conexões Player <-> Player
        for origin in all_players:
            origin_txt = player_dict[origin]
            for target in all_players:
                if origin == target: continue
                if target.lower() in origin_txt:
                    edges.append((origin, target))

        # --- PROCESSAMENTO DO MUNDO (ROXO) ---
        if s_rows:
            server_node_name = "Mundo" # Nome do nó central
            nodes.append(server_node_name)
            node_colors[server_node_name] = '#9b59b6' # Roxo (Server)
            
            # Junta toda a lore do servidor num textão só para analisar
            full_server_lore = " ".join([r['content'].lower() for r in s_rows])

            # Checa: O Mundo cita algum Player?
            for p_name in all_players:
                # Se o nome do player aparece na lore do servidor -> Conecta
                if p_name.lower() in full_server_lore:
                    edges.append((server_node_name, p_name))
                
                # Checa: O Player cita o Mundo? (palavras chave)
                # Se o player escreveu "o reino", "o mundo", "o servidor" -> Conecta
                player_txt = player_dict[p_name]
                if any(x in player_txt for x in ["mundo", "reino", "servidor", "capital", "história"]):
                    edges.append((p_name, server_node_name))

        if not edges:
            return await interaction.followup.send("❌ Não encontrei conexões suficientes para desenhar.")

        # Remove duplicatas nas conexões (A->B é igual a B->A para grafos simples)
        edges = list(set(tuple(sorted(e)) for e in edges))

        # 3. GERA A IMAGEM (Passando as cores agora)
        image_buffer = await asyncio.to_thread(_generate_graph_image, nodes, edges, node_colors)

        file = discord.File(image_buffer, filename="teia_destinos.png")
        embed = discord.Embed(
            title="🕸️ Teia de Destinos", 
            description="🔵 **Azul:** Players\n🟣 **Roxo:** História do Mundo/Servidor",
            color=discord.Color.blurple()
        )
        embed.set_image(url="attachment://teia_destinos.png")
        
        await interaction.followup.send(embed=embed, file=file)

    except Exception as e:
        log_to_gui(f"Erro no grafo: {e}", "ERROR")
        await interaction.followup.send(f"❌ Erro ao desenhar: {e}")

# COMANDO ADICIONAR LORE DO SERVIDOR (Só Staff)
@lore_group.command(name="server", description="Arquiva lore do mundo/servidor (Restrito a Staff).")
async def lore_server(interaction: discord.Interaction, arquivo: discord.Attachment = None, texto: str = None):
    # Verifica Permissão
    if not any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
        return await interaction.response.send_message("🚫 Apenas a Staff pode escrever a história do mundo.", ephemeral=True)

    await interaction.response.defer(thinking=True)
    final_content = ""
    
    if arquivo:
        extracted = await extract_text_from_attachment(arquivo)
        final_content += f"\n{extracted}\n"
    if texto:
        final_content += f"\n{texto}"

    if not final_content.strip():
        return await interaction.followup.send("❌ Nada para salvar.", ephemeral=True)

    try:
        cursor = get_bot_instance().db_conn.cursor()
        cursor.execute("INSERT INTO server_lore (content) VALUES (?)", (final_content,))
        get_bot_instance().db_conn.commit()
        await interaction.followup.send("✅ **Lore Global** adicionada à Biblioteca de Alexandria.")
    except Exception as e:
        await interaction.followup.send(f"Erro: {e}")



class LoreAICog(commands.Cog):
    """Registra /acervo e o grupo /lore; expõe P3luchePersona no cog loader."""

    def __init__(self, bot):
        self.bot = bot
        set_bot_instance(bot)

    async def cog_load(self):
        self.bot.tree.add_command(lore_group)
        self.bot.tree.add_command(acervo)


async def setup(bot):
    await bot.add_cog(P3luchePersona(bot))
    await bot.add_cog(LoreAICog(bot))
