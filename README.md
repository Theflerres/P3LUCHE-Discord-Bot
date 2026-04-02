# P3LUCHE - Sistema Operacional de Comunidade & RPG

> **Versão:** 3.0 | **Status:** Em Produção | **Dev:** @Theflerres

O **P3LUCHE** é uma aplicação robusta de engenharia de software desenvolvida em Python para atuar como o núcleo operacional de servidores de Discord focados em RPG.

Diferente de bots comerciais genéricos, este projeto foi arquitetado ao longo de 1 ano para resolver problemas complexos de **Governança de Dados**, **Visualização de Redes** e **Inteligência Artificial Contextual**.

---

## 🛠️ Stack Tecnológica

Este projeto não utiliza apenas wrappers básicos. A arquitetura envolve:

* **Core:** Python 3.13 (AsyncIO, Multithreading & **Arquitetura Modular c/ Cogs**)
* **Banco de Dados:** SQLite3 Relacional (c/ Migrations personalizadas & WAL mode)
* **Data Science:** `NetworkX` (Teoria dos Grafos), `Matplotlib`, `Pandas`, `Scipy`
* **AI & NLP:** Google Gemini API (1.5 Pro) + RAG (Retrieval-Augmented Generation)
* **Cloud & Storage:** Google Drive API + YoutubeDL (Cache Híbrido)

---

## 🧠 Principais Módulos de Engenharia

### 1. Visualização de Dados (Network Graph)
Implementação de um algoritmo local ("Zero-Token Cost") que processa linguagem natural para mapear relações entre personagens.
* **Input:** Milhares de linhas de texto (Lore dos jogadores).
* **Processamento:** Análise de co-ocorrência e reconhecimento de entidades (NER).
* **Output:** Renderização de um Grafo de Rede (Nós e Arestas) exportado em 4K.

### 2. Governança e Auditoria (Soft Delete)
O sistema segue princípios estritos de integridade de dados.
* **Soft Delete:** Nenhuma informação (advertência ou log) é deletada fisicamente. O sistema usa flags (`is_active=0`) para arquivamento, mantendo trilha de auditoria.
* **Versionamento de Conteúdo:** Sistema proprietário de controle de versão para textos de RPG.
    * *Funcionalidade:* Se um usuário edita uma história, o bot salva o snapshot anterior.
    * *Diff Check:* Gera relatórios visuais (`diff`) mostrando exatamente o que foi alterado (adicionado/removido).

### 3. Integração AI & RAG
O bot atua como um "Bibliotecário Inteligente".
* **Ingestão de Dados:** Aceita upload de arquivos não estruturados (`.pdf`, `.docx`, `.txt`).
* **Contexto Dinâmico:** Utiliza a API do Gemini para responder perguntas sobre a história do mundo ("Quem é o Rei?", "O que aconteceu na batalha X?") baseando-se apenas nos documentos ingeridos.

### 4. Gestão de Mídia High-End
Sistema de rádio desenvolvido para superar a instabilidade de streaming direto.
* O bot realiza o download do áudio, normaliza o volume e faz upload para um contêiner privado no **Google Drive**.
* Isso garante persistência dos arquivos e reprodução sem *buffering*.

### 5. Arquitetura Modular e Escalável (Cogs)
Refatoração do sistema monolítico para um *design pattern* focado em alta disponibilidade e manutenibilidade.
* **Separação de Contextos (SoC):** Funcionalidades divididas em domínios de aplicação estritamente isolados (`economia`, `lore_ai`, `musica`, `moderacao`).
* **Hot-Reloading:** Capacidade de carregar, descarregar e atualizar componentes específicos em tempo de execução, sem interromper o WebSocket e a operação geral do bot.

---

## 📊 Exemplo de Estrutura de Versionamento

O sistema de versionamento de Lore funciona de forma similar ao Git. Exemplo de output do comando `/lore diff`:

```diff
--- Versão Antiga (2024-10-15)
+++ Versão Atual
@@ -12,4 +12,4 @@
- O personagem tem medo de altura.
+ O personagem superou seu medo e agora pilota dragões.
  Ele carrega uma espada de ferro.