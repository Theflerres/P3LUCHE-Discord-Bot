# 🧸 PelucheGPT - Ecossistema de Inteligência e Gestão de RPG (v6.0)

> **Status:** Em Produção | **Versão:** 6.0 | **Dev:** @Theflerres

O **PelucheGPT** é a evolução definitiva do projeto P3LUCHE. O que nasceu como um bot de automação para Discord agora é uma **aplicação desktop standalone** robusta, projetada para gerenciar lores complexas de RPG através de Inteligência Artificial Híbrida.

A v6.0 marca a transição para uma interface nativa, eliminando a dependência de IDEs e otimizando o consumo de recursos (RAM/CPU) através da arquitetura Tauri.

---

# 🧸 PelucheGPT v6.0
> **A Inteligência Artificial Definitiva para Gestão de Lore e Comunidades de RPG.**

![Python](https://img.shields.io/badge/python-3.13-blue.svg)
![Tauri](https://img.shields.io/badge/Tauri-Desktop-orange.svg)
![LLM](https://img.shields.io/badge/AI-Híbrida-red.svg)
![Status](https://img.shields.io/badge/Status-Em%20Produção-green.svg)


## 🛠️ Stack Tecnológica de Elite

O projeto utiliza uma arquitetura moderna de sistemas distribuídos:

* **Frontend (Desktop):** [Tauri](https://tauri.app/) (Rust + Node.js) - Interface leve e segura.
* **Backend (Intelligence):** Python 3.13 (AsyncIO & Modular Cogs).
* **AI Híbrida:** Google Gemini 1.5 Pro + Integração com **LLM Local** para processamento offline.
* **Data Science:** `NetworkX` (Teoria dos Grafos) para mapeamento de relações.
* **Banco de Dados:** SQLite3 (Modo WAL) com arquitetura de **Soft Delete** e auditoria.

---

## 🧠 Principais Módulos de Engenharia

### 1. PelucheGPT Lore Assistant (RAG)
O sistema atua como um "Bibliotecário Inteligente". Através de um indexador semântico, a IA consulta o banco de dados de lore em tempo real para garantir que as respostas respeitem os fatos históricos do servidor.

### 2. Visualização de Dados (Network Graph)
Implementação de algoritmos que processam linguagem natural para mapear relações entre personagens e facções, exportando grafos relacionais em alta definição (4K).

### 3. Governança e Auditoria de Dados
* **Soft Delete:** Nenhuma informação é deletada fisicamente (`is_active=0`), garantindo trilha de auditoria completa.
* **Versionamento de Conteúdo:** Sistema similar ao Git para textos de RPG, permitindo visualizar `diffs` entre versões de histórias dos players.

### 4. Interface Standalone (.exe)
Uma GUI dedicada que permite:
* **Bot Switch:** Ligar/Desligar o bot do Discord com um clique.
* **Log Stream:** Monitoramento em tempo real do processamento da IA e eventos do servidor.

---

## 📂 Estrutura do Projeto

```text
peluchegpt/
├── backend/            # Core Python (LLMs, Classificadores, Cogs)
├── frontend/           # Interface Desktop (Tauri + Rust/JS)
│   ├── src-tauri/      # Ponte de comunicação nativa em Rust
│   └── src/            # UI da aplicação
└── database/           # (Local) Persistência de dados e logs (Ignorado no Git)

---


## 📊 Exemplo de Fluxo (Lore Diff)
--- Versão Antiga (2024-10-15)
+++ Versão Atual
@@ -12,4 +12,4 @@
- O personagem tem medo de altura.
+ O personagem superou seu medo e agora pilota dragões.


