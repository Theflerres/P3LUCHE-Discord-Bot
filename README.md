<div align="center">

# 🧸 P3LUCHE

### Bot de Discord para economia de pesca, ilha pessoal, moderação e lore de RPG

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7.1-5865F2?style=for-the-badge&logo=discord&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite3-Migrations%20autom%C3%A1ticas-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Status](https://img.shields.io/badge/Status-Em%20Produção-brightgreen?style=for-the-badge)

Desenvolvido por [@Theflerres](https://github.com/Theflerres)

</div>

---

## O Projeto

O **P3LUCHE** é um bot de Discord (via [discord.py](https://discordpy.readthedocs.io/), arquitetura modular em Cogs) para um único servidor. Ele roda como um processo Python comum conectado ao Gateway do Discord — sem interface gráfica própria, sem suporte a múltiplos servidores.

O núcleo é um sistema de economia/pesca (Sachês, varas, clima, ilha pessoal), complementado por moderação com trilha de auditoria, um acervo de lore de RPG com versionamento e grafo de relações, e um sistema de música com catálogo próprio no Google Drive.

> Existe também `peluchegpt/`, um protótipo de reescrita (FastAPI + desktop Tauri) — veja a nota no final deste documento. Ele **não** está conectado ao bot descrito aqui e não roda no estado atual do repositório.

---

## Sumário

- [Stack Tecnológica](#️-stack-tecnológica)
- [Funcionalidades](#-funcionalidades)
- [Estrutura do Projeto](#-estrutura-do-projeto)
- [Instalação e Configuração](#️-instalação-e-configuração)
- [Variáveis de Ambiente](#-variáveis-de-ambiente)
- [Executando o Projeto](#️-executando-o-projeto)
- [Testes](#-testes)
- [Sobre o protótipo peluchegpt/](#-sobre-o-protótipo-peluchegpt)
- [Licença](#-licença)

---

## 🛠️ Stack Tecnológica

| Camada | Tecnologias |
|---|---|
| **Discord** | discord.py 2.7.1 (Slash Commands, arquitetura modular em Cogs) |
| **Grafo de Lore** | NetworkX + Matplotlib (`/lore grafo`) |
| **Banco de Dados** | SQLite3 — migrations automáticas no startup, soft delete (advertências, memórias, faixas de música) |
| **Cloud & Storage** | Google Drive API (catálogo de músicas, backup do banco) |
| **Mídia** | yt-dlp, PyNaCl, FFmpeg |
| **Ingestão de Docs (Lore)** | pypdf, python-docx, lxml |

---

## 🎮 Funcionalidades

### Economia & Pesca
`/eco pescar` sorteia peixes/lixo com valor variável conforme a vara equipada e o clima do momento (Céu Limpo, Tempestade Sombria ou Brisa Dourada, cada um com efeito próprio em sorte e chance de lixo). Inclui loja diária rotativa, upgrades de vara (sorte/cooldown), armadilha AFK, missões de grupo e um QTE de tensão para capturas de tier alto. Toda leitura/escrita de saldo, inventário e progressão passa por helpers atômicos (`BEGIN IMMEDIATE`) — sem condição de corrida em ações concorrentes.

### Ilha Pessoal
`/ilha` — cada jogador tem uma ilha privada e isolada (sem visita de outros jogadores), com progressão linear por tier. Construções custam Sachê + sucata e levam tempo real para ficar prontas (máquina de estados idle/building/pronta). Benefícios mecânicos das construções ainda não foram decididos/implementados — hoje é só progressão e existência.

### Cassino & Minigames
Corrida de peixes, jogo da memória, batalha naval e leilões (`/eco craftar`, `/eco corrida`, `/eco memoria`, `/eco batalhar`) estão ativos. O cog de cassino (`cogs/casino.py`) está **desativado** no momento (crash pendente de correção) — não carregado em produção.

### Música
Dois sistemas complementares: um catálogo persistente no Google Drive (`/musica adicionar`, `buscar`, `biblioteca`, com edição/ocultação restrita a staff) e um player de fila em canal de voz (`/tocar`, `/cardapio`, `/fila`, `/pausar`, `/pular`), restrito a canais específicos.

### Moderação
`/mod advertencia`, `/mod historico`, `/mod perdoar` — advertências nunca são deletadas fisicamente (soft delete: `status='revoked'` + quem revogou e quando), mantendo trilha de auditoria completa.

### Lore
Cada jogador registra a lore do seu personagem (`/lore player`), com um Bibliotecário-Chefe (staff) podendo editar lore de terceiros com histórico de versões (`/lore diff`). `/lore grafo` gera um grafo visual de conexões entre personagens via NetworkX, detectando o tipo de relação (aliado/inimigo/família/mestre) por palavras-chave no texto. Staff/criador ainda podem mencionar `@P3LUCHE` para anotar memórias rápidas (`/ia memoria_ver`, `/ia memoria_esquecer`).

### Admin
`/admin economia` (consultar/corrigir/dar/remover/resetar), `/admin sistema` (mensagens manuais, reset de cooldowns, reset global com backup automático + confirmação por modal + log de auditoria) e `/admin debug` (inspeção somente-leitura). Toda a camada é restrita ao Criador do bot via `is_bot_owner()` — nunca por lista de IDs hardcoded.

### Onboarding
Mensagem de boas-vindas em canal dedicado (opcional) e dica visível em `/ajuda` para o caso mais comum de "os comandos não aparecem": o usuário precisa clicar em **Adicionar App** para autorizar o bot para si mesmo, mesmo já estando no servidor — uma particularidade do modelo de permissões do Discord que não é detectável por código (a falha acontece inteiramente do lado do cliente, antes de qualquer interação chegar ao bot).

---

## 📁 Estrutura do Projeto

```
P3-LUCH3/
├── src/p3luche/             # Código-fonte real do bot
│   ├── main.py              # Ponto de entrada (bot.run) — execute a partir daqui
│   ├── config.py            # Constantes, IDs de canal, variáveis de ambiente
│   ├── database.py          # Conexão SQLite + migrations automáticas
│   ├── economy_db.py        # Helpers atômicos de economia/pesca/ilha (BEGIN IMMEDIATE)
│   ├── economy_constants.py # Catálogo de peixes (FISH_DB)
│   ├── permissions.py       # is_bot_owner() — única fonte de verdade de autorização admin
│   ├── utils.py             # Helpers compartilhados (logging, sanitização, etc.)
│   └── cogs/                # Um módulo por domínio
│       ├── economia.py      # /eco (pesca, loja, guilda, clima)
│       ├── ilha.py          # /ilha (ilha pessoal)
│       ├── minigames.py     # /eco craftar|corrida|memoria|batalhar, leilões
│       ├── casino.py        # Desativado (não carregado em main.py)
│       ├── musica.py        # /musica (catálogo no Drive)
│       ├── jukebox.py       # /tocar, /fila (player em canal de voz)
│       ├── moderacao.py     # /mod (advertências com soft delete)
│       ├── lore_ai.py       # /lore, /acervo, grafo NetworkX, persona P3LUCHE
│       ├── admin.py         # /admin (exclusivo do Criador)
│       ├── onboarding.py    # Boas-vindas (on_member_join)
│       ├── sistema.py       # /ajuda, /stats, /apoiadores, /ia
│       ├── backup.py        # Backup diário do bot.db no Google Drive
│       ├── logs.py          # Auditoria de eventos (mensagens, erros, conexão)
│       └── erros.py         # Log de stack traces em canal privado
│
├── assets/pesca/            # Imagens/GIFs usados nos embeds de pesca
├── database/                # bot.db, credentials.json, logs (gitignorado)
├── tests/                   # Suíte de testes automatizados (unittest)
│
├── config.py, utils.py, database.py, economy_db.py,
│   economy_constants.py, permissions.py, main.py    # Shims na raiz (`from src.p3luche.X import *`)
├── cogs/                    # Redireciona __path__ para src/p3luche/cogs
│                            # (os dois acima existem só para os testes
│                            #  rodarem a partir da raiz do repo — não são
│                            #  o código real, que vive em src/p3luche/)
│
├── peluchegpt/              # Protótipo desconectado — ver seção própria
├── requirements.txt
└── README.md
```

---

## ⚙️ Instalação e Configuração

### Pré-requisitos

- [Python 3.12+](https://www.python.org/downloads/)
- [FFmpeg](https://ffmpeg.org/download.html) disponível no PATH (necessário para `/tocar`)
- Um app no [Discord Developer Portal](https://discord.com/developers/applications) com o bot criado
- (Opcional, só para música/backup) Um projeto no Google Cloud com a **Google Drive API** habilitada

### 1. Clone e instale as dependências

```bash
git clone https://github.com/Theflerres/P3LUCHE-Discord-Bot.git
cd P3LUCHE-Discord-Bot
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure as variáveis de ambiente

Crie um arquivo `.env` na raiz do projeto — veja a seção abaixo para a lista completa.

---

## 🔑 Variáveis de Ambiente

Só `DISCORD_TOKEN` é obrigatória. Todas as outras são opcionais — cada uma tem um comportamento gracioso quando ausente (o recurso associado simplesmente fica desligado, sem erro).

```env
# Obrigatória — token do bot no Discord Developer Portal
DISCORD_TOKEN=seu_token_aqui

# Opcional — pasta do Drive usada como cache de músicas do Jukebox.
# Sem ela, cai no fallback DRIVE_FOLDER_ID (constante em config.py).
JUKEBOX_DRIVE_FOLDER_ID=id_da_pasta_aqui

# Opcional — credenciais de Service Account do Google (JSON inline ou
# caminho de arquivo). Sem ela, o Drive usa o fluxo OAuth legado
# (client_secret.json + database/credentials.json).
GOOGLE_CREDENTIALS_JSON=

# Opcional — canal privado para stack traces técnicos. Sem ela, erros só
# vão para database/bot_erros.log (nunca cai de volta num canal público).
ERROR_LOG_CHANNEL_ID=

# Opcional — canal onde o banner de mudança de clima da pescaria é
# publicado. Sem ela, o banner simplesmente não é enviado.
FISHING_CHANNEL_ID=

# Opcional — canal de boas-vindas para novos membros (on_member_join).
# Sem ela, nenhuma mensagem de boas-vindas é enviada.
WELCOME_CHANNEL_ID=
```

Além do `.env`, dois arquivos de credenciais do Google (gitignorados) são necessários só para as funcionalidades de Drive: `client_secret.json` (OAuth) e `database/credentials.json` (token gerado no primeiro login — apagar e reautenticar se expirar/for revogado).

> ⚠️ Nunca commite `.env`, `client_secret.json` ou `database/credentials.json`.

---

## ▶️ Executando o Projeto

```bash
python src/p3luche/main.py
```

O bot irá conectar ao SQLite (executando migrations pendentes), carregar os Cogs listados em `src/p3luche/main.py`, sincronizar os Slash Commands com o Discord e iniciar os loops em background (clima, mercado, backup diário).

> ⚠️ **Não use `python main.py` a partir da raiz do repositório** — esse arquivo é só um shim de import (`from src.p3luche.main import *`) para os testes funcionarem a partir da raiz; como ele é *importado* em vez de executado diretamente, o bloco `if __name__ == "__main__": bot.run(...)` nunca dispara. O bot "roda" sem erro nenhum e sai imediatamente, sem conectar a lugar nenhum — use sempre o comando acima.

---

## 🧪 Testes

```bash
python -m unittest discover -s tests
```

Suíte com testes automatizados cobrindo os helpers atômicos de economia/pesca/ilha, permissões admin, fluxo de reset (individual e global, incluindo o cenário de falha de backup) e conteúdo dos embeds de onboarding/ajuda.

---

## 🧩 Sobre o protótipo `peluchegpt/`

O diretório `peluchegpt/` contém uma exploração de uma reescrita do projeto como aplicação desktop (backend FastAPI + frontend Tauri). **Não é o bot em produção** descrito neste README, e no estado atual do repositório não está funcional: `peluchegpt/backend/main.py` mistura import absoluto e relativo de um jeito que quebra ao ser executado (`from chat_engine import ...` junto com `from .config import ...`), não tem bloco de entrada (`uvicorn.run`), e seu schema de banco não é compatível com o do bot real. Documentado aqui só para clareza sobre o que está e o que não está em produção.

---

## 📄 Licença

Distribuído sob a licença presente no arquivo [LICENSE](LICENSE).

---

<div align="center">
  Desenvolvido com 🖤 por <a href="https://github.com/Theflerres">@Theflerres</a>
</div>
