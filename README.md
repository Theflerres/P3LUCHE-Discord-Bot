<div align="center">

# 🧸 PelucheGPT

### A Inteligência Artificial Definitiva para Gestão de Lore e Comunidades de RPG

![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Tauri](https://img.shields.io/badge/Tauri-2.x-FFC131?style=for-the-badge&logo=tauri&logoColor=black)
![Rust](https://img.shields.io/badge/Rust-Bridge-000000?style=for-the-badge&logo=rust&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-1.5%20Pro-4285F4?style=for-the-badge&logo=google&logoColor=white)
![LLM](https://img.shields.io/badge/AI-H%C3%ADbrida-FF4B4B?style=for-the-badge)
![Status](https://img.shields.io/badge/Status-Em%20Produ%C3%A7%C3%A3o-brightgreen?style=for-the-badge)

**Versão 6.0** · Desenvolvido por [@Theflerres](https://github.com/Theflerres)

</div>

---

## O Projeto

O **PelucheGPT** é a evolução definitiva do projeto [P3LUCHE](https://github.com/Theflerres/P3LUCHE-Discord-Bot). O que nasceu como um bot de automação para Discord agora é uma **aplicação desktop standalone** robusta para gerenciamento de lores complexas de RPG.

A **v6.0** marca a transição para uma interface nativa via Tauri, eliminando a dependência de IDEs e otimizando o consumo de recursos (RAM/CPU) com a ponte Rust. O backend Python continua operando como núcleo de inteligência, agora com suporte a **LLM local** para processamento completamente offline.

---

## Sumário

- [Stack Tecnológica](#-stack-tecnológica)
- [Arquitetura Geral](#-arquitetura-geral)
- [Módulos de Engenharia](#-módulos-de-engenharia)
- [Estrutura do Projeto](#-estrutura-do-projeto)
- [Instalação e Configuração](#-instalação-e-configuração)
- [Variáveis de Ambiente](#-variáveis-de-ambiente)
- [Executando o Projeto](#-executando-o-projeto)
- [Licença](#-licença)

---

## 🛠️ Stack Tecnológica

| Camada | Tecnologias |
|---|---|
| **Frontend (Desktop)** | Tauri 2.x, Rust, Node.js |
| **Backend (Inteligência)** | Python 3.13, AsyncIO, Arquitetura Modular c/ Cogs |
| **AI Híbrida** | Google Gemini 1.5 Pro + LLM Local (processamento offline) |
| **Data Science** | NetworkX, Matplotlib, Pandas, SciPy |
| **Banco de Dados** | SQLite3 (WAL mode, Soft Delete, Migrations automáticas) |
| **Discord** | discord.py 2.7.1 (Slash Commands + Hot-Reload de Cogs) |
| **Cloud & Storage** | Google Drive API, Google Auth |
| **Mídia** | yt-dlp, PyNaCl, FFmpeg |
| **Ingestão de Docs** | pypdf, python-docx, lxml |

---

## 🏗️ Arquitetura Geral

```
┌─────────────────────────────────────────────────┐
│              PelucheGPT Desktop                 │
│         Interface Tauri (Rust + Node.js)        │
│  ┌──────────────┐        ┌────────────────────┐ │
│  │  Bot Switch  │        │    Log Stream      │ │
│  │ Liga/Desliga │        │ Monitoramento RT   │ │
│  └──────┬───────┘        └────────┬───────────┘ │
└─────────┼────────────────────────┼─────────────┘
          │   Ponte Nativa (Rust)  │
          ▼                        ▼
┌─────────────────────────────────────────────────┐
│              Backend Python 3.13                │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  lore_ai │  │  musica  │  │  moderacao    │  │
│  │  Gemini  │  │ yt-dlp   │  │  Soft Delete  │  │
│  │   RAG    │  │  GDrive  │  │  Auditoria    │  │
│  └──────────┘  └──────────┘  └───────────────┘  │
│       │              SQLite3 (WAL Mode)          │
└───────┼─────────────────────────────────────────┘
        ▼
   Discord API / LLM Local
```

---

## 🔬 Módulos de Engenharia

### 1. PelucheGPT Lore Assistant (RAG)

O bot atua como um "Bibliotecário Inteligente" do universo de RPG do servidor.

- **Ingestão:** Aceita arquivos `.pdf`, `.docx` e `.txt` não estruturados
- **Indexação semântica:** Recupera contexto relevante em tempo real antes de cada resposta
- **Coerência:** A IA responde respeitando os fatos históricos do servidor, sem alucinar fora do corpus

### 2. Visualização de Rede de Personagens (NetworkX)

Algoritmo local de custo zero em tokens que processa lore textual e mapeia relações entre personagens e facções como um grafo de rede.

- **Input:** Milhares de linhas de lore dos jogadores
- **Processamento:** Co-ocorrência + NER (reconhecimento de entidades)
- **Output:** Grafo relacional exportado em 4K

### 3. Governança de Dados & Auditoria (Soft Delete)

Nenhuma informação é deletada fisicamente — o sistema segue princípios estritos de integridade de dados.

- **Soft Delete:** Advertências e logs arquivados com `is_active=0`, mantendo trilha de auditoria
- **Versionamento de Lore:** Snapshot automático antes de qualquer edição (similar ao Git)
- **Diff Check:** Relatório visual de alterações linha a linha

```diff
--- Versão Antiga (2024-10-15)
+++ Versão Atual
@@ -12,1 +12,1 @@
- O personagem tem medo de altura.
+ O personagem superou seu medo e agora pilota dragões.
```

### 4. Interface Desktop Standalone (Tauri)

A GUI nativa elimina a dependência de terminais ou IDEs:

- **Bot Switch:** Ligar/desligar o bot do Discord com um clique
- **Log Stream:** Monitoramento em tempo real do processamento da IA e eventos do servidor
- **Consumo otimizado:** A ponte Rust reduz o overhead significativamente em relação a soluções Electron

### 5. AI Híbrida (Online + Offline)

- **Online:** Google Gemini 1.5 Pro para consultas com acesso à internet
- **Offline:** LLM Local como fallback — o sistema permanece funcional sem conexão externa

### 6. Arquitetura Modular & Hot-Reload

- **Separação de contextos (SoC):** cada domínio em um Cog isolado
- **Hot-Reload:** módulos recarregáveis individualmente em runtime sem derrubar o WebSocket do bot
- **Migrations automáticas:** o banco atualiza sua estrutura no startup sem intervenção manual

---

## 📁 Estrutura do Projeto

```
peluchegpt/
├── backend/                # Core Python
│   ├── cogs/               # Módulos do bot (lore_ai, musica, moderacao, economia...)
│   ├── main.py             # Ponto de entrada do bot
│   ├── config.py           # Configurações globais
│   ├── database.py         # Gerenciador de banco (conexão, migrate, WAL)
│   └── utils.py            # Funções utilitárias compartilhadas
│
├── frontend/               # Interface Desktop (Tauri)
│   ├── src-tauri/          # Ponte de comunicação nativa em Rust
│   │   ├── src/            # Comandos Tauri e lógica de processo
│   │   └── tauri.conf.json # Configuração do app desktop
│   └── src/                # UI da aplicação (HTML/CSS/JS)
│
├── database/               # Persistência local (ignorada no Git)
│   └── peluchegpt.db       # SQLite — gerado automaticamente no primeiro run
│
├── requirements.txt        # Dependências Python
├── .env                    # Variáveis de ambiente (não commitado)
└── README.md
```

---

## ⚙️ Instalação e Configuração

### Pré-requisitos

Certifique-se de ter instalado:

- [Python 3.13+](https://www.python.org/downloads/)
- [Node.js 18+](https://nodejs.org/)
- [Rust (via rustup)](https://rustup.rs/)
- [FFmpeg](https://ffmpeg.org/download.html) disponível no PATH
- Conta de serviço no Google Cloud com as APIs habilitadas:
  - **Google Drive API**
  - **Gemini API** (Google AI Studio)

### 1. Clone o repositório

```bash
git clone https://github.com/Theflerres/P3LUCHE-Discord-Bot.git
cd P3LUCHE-Discord-Bot
```

### 2. Configure o backend Python

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure o frontend Tauri

```bash
cd ../frontend
npm install
```

### 4. Configure as variáveis de ambiente

Crie o arquivo `.env` na raiz conforme a seção abaixo.

---

## 🔑 Variáveis de Ambiente

Crie um arquivo `.env` na raiz do projeto:

```env
# Token do bot (Discord Developer Portal)
DISCORD_TOKEN=seu_token_aqui

# Chave da API do Google Gemini (Google AI Studio)
GEMINI_API_KEY=sua_chave_aqui

# ID da pasta no Google Drive usada como cache de músicas
GDRIVE_FOLDER_ID=id_da_pasta_aqui

# Caminho para o arquivo de credenciais da conta de serviço Google
GOOGLE_CREDENTIALS_PATH=credentials.json

# (Opcional) Caminho ou endpoint do LLM Local para modo offline
LOCAL_LLM_PATH=./models/model.gguf

# (Opcional) ID do servidor Discord principal
GUILD_ID=id_do_servidor
```

> ⚠️ Nunca commite o `.env` ou o `credentials.json`. Eles já estão no `.gitignore`.

---

## ▶️ Executando o Projeto

### Modo Desktop (Tauri)

```bash
cd frontend
npm run tauri dev       # Desenvolvimento
npm run tauri build     # Gera o executável final (.exe / .dmg / .AppImage)
```

A interface desktop inicializa o backend Python automaticamente via ponte Rust.

### Modo Headless (apenas bot)

Para rodar o bot sem a interface desktop:

```bash
cd backend
python main.py
```

O bot irá:
1. Conectar ao banco SQLite e executar migrations pendentes
2. Carregar todos os Cogs em ordem de dependência
3. Sincronizar os Slash Commands com o Discord
4. Verificar e popular o cache de músicas do Google Drive

---

## 📄 Licença

Distribuído sob a licença presente no arquivo [LICENSE](LICENSE).

---

<div align="center">
  Desenvolvido com 🖤 por <a href="https://github.com/Theflerres">@Theflerres</a><br/>
  <sub>Evoluído a partir do <a href="https://github.com/Theflerres/P3LUCHE-Discord-Bot">P3LUCHE Discord Bot v3.0</a></sub>
</div>