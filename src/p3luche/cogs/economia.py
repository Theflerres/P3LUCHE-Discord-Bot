
"""
Economia Scrap Seas — pescaria, loja, guilda, exploração, AFK traps e clima.
"""
import sqlite3
import threading
import json
import os
import random
import re
import time
from collections import Counter
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    get_bot_instance,
    set_bot_instance,
    CATCHES_LOCK,
    CATCHES_SINCE_RESTART,
    CATCHES_TTL_SECONDS,
    FISHING_CHANNEL_ID,
)
from utils import get_local_file, log_to_gui
from economy_constants import FISH_DB, TRASH_ITEMS, TRASH_ROLL_RATIO
from cogs.pesca_visuals import (
    resolve_fishing_asset,
    resolve_weather_asset,
)
# cogs.ilha não importa cogs.economia (só config/utils/economy_db), então esta
# direção não fecha ciclo. O catálogo de construções mora lá, e é de lá que
# vem o único ponto de leitura dos bônus.
from cogs.ilha import get_island_bonuses
from economy_db import (
    FORGE_REQUIRED_RANK,
    MISSION_DAILY_CAP,
    add_guild_xp,
    forge_level_cost,
    forge_luck_multiplier,
    get_forge_level,
    add_inventory_item,
    ensure_user,
    ensure_v4_tables,
    get_cooldowns,
    get_current_rod,
    get_fish_count,
    get_guild_rank,
    get_inventory,
    get_rod_upgrades,
    get_scrap,
    get_top_players,
    get_trap,
    get_user_names,
    get_wallet,
    has_account,
    log_fish_sale,
    modify_scrap,
    modify_wallet,
    seed_market_prices,
    set_cooldown,
    set_current_rod,
    set_guild_rank,
    set_trap,
    sync_user_to_economy,
    try_register_mission_completion,
    try_spend_wallet,
    try_upgrade_forge,
    try_upgrade_rod,
    mission_slots_left,
    missions_completed_today,
)


def _cleanup_stale_catches() -> None:
    now = time.time()
    expired = [uid for uid, (_, ts) in CATCHES_SINCE_RESTART.items() if now - ts > CATCHES_TTL_SECONDS]
    for uid in expired:
        CATCHES_SINCE_RESTART.pop(uid, None)


# --- SISTEMA DE ECONOMIA V3.1 (CORREÇÃO DE DATAS PYTHON 3.12) ---
# --- 1. CONFIGURAÇÃO DE ITENS E PEIXES ---
# ==============================================================================
# SISTEMA DE ECONOMIA COMPLETO (V4.0 - VARAS, RARIDADE, DATAS E NOVOS COMANDOS)
# ==============================================================================

# --- GRUPO DE COMANDOS ---
eco_group = app_commands.Group(name="eco", description="Economia, Loja e Pescaria do P3LUCHE.")

# 1. CONFIGURAÇÃO DE VARAS (STATS & LOJA)
# Tier: Nível máximo de peixe que pode pegar (0=Lixo, 5=Cósmico)
# CD: Multiplicador de tempo (1.0 = 5 min, 0.5 = 2.5 min)
# Trash: Chance de pegar lixo (%)
# Luck: Multiplicador de valor do peixe

ROD_STATS = {
    # --- TIER 0: INICIANTE (Grátis/Barato) ---
    "vara_galho": {
        "name": "Galho Amarrado", 
        "price": 0, 
        "tier": 0, 
        "cd": 0.5, "trash": 90, "luck": 0.8,
        "desc": "Rápida, mas pega mais bota que peixe."
    },
    "vara_bambu": {
        "name": "Vara de Bambu",
        "price": 0,
        "tier": 0,
        # trash 60 -> 35: com o fallback do tier 0 devolvendo lixo, a Bambu
        # entregava 40% de lixo efetivo e 15,4 Sachês por lance. Corrigido o
        # fallback, 35 põe a taxa efetiva em ~16% e o lance em ~20,4.
        "cd": 1.0, "trash": 35, "luck": 1.0,
        "desc": "A clássica. Conffiável e humilde."
    },
    "vara_treino": {
        "name": "Vara de Treino",
        "price": 250,
        # tier 0 -> 1. Era a única vara COMPRÁVEL de tier 0, o que a deixava
        # fora da escada: mesmo pool da Bambu (que é grátis), com cooldown
        # 20% mais lento. Resultado: a primeira compra do jogo rendia 159,8/h
        # contra 185,2/h da vara inicial — o jogador pagava 250 Sachês para
        # ganhar menos. Como tier 1 ela abre o pool de peixe de verdade;
        # cd 1,3 e trash 55 a mantêm abaixo da Reciclada (600), preservando a
        # ordem da escada: 245 -> 403 -> 618 -> 691 -> 704 Sachês/h.
        "tier": 1,
        "cd": 1.3, "trash": 55, "luck": 1.0,
        "desc": "Lenta e suja, mas finalmente pega peixe de verdade."
    },

    # --- TIER 1: AMADOR (Começando a lucrar) ---
    "vara_plastico": {
        "name": "Vara Reciclada", 
        "price": 600, 
        "tier": 1, 
        "cd": 0.8, "trash": 60, "luck": 1.0,
        "desc": "Feita de garrafas pet. Ecológica e rápida."
    },
    "vara_fibra": {
        "name": "Vara de Fibra", 
        "price": 900, 
        "tier": 1, 
        "cd": 1.0, "trash": 40, "luck": 1.1,
        "desc": "Padrão da indústria. Resistente."
    },
    "vara_pesada": {
        "name": "Vara de Chumbo", 
        "price": 1200, 
        "tier": 1, 
        "cd": 1.5, "trash": 15, "luck": 1.3,
        "desc": "Afunda rápido. Evita lixo da superfície."
    },

    # --- TIER 2: PROFISSIONAL (Especializações) ---
    "vara_veloz": {
        "name": "Vara Aerodinâmica", 
        "price": 2500, 
        "tier": 2, 
        "cd": 0.6, "trash": 55, "luck": 1.0,
        "desc": "Corta o vento. Para quem tem pressa."
    },
    "vara_ouro": {
        "name": "Vara de Ouro", 
        "price": 3500, 
        "tier": 2, 
        "cd": 1.2, "trash": 35, "luck": 1.6,
        "desc": "Atrai peixes que gostam de brilho."
    },
    "vara_sonar": {
        "name": "Vara com Sonar", 
        "price": 4200, 
        "tier": 2, 
        "cd": 1.3, "trash": 5, "luck": 1.1,
        "desc": "Detecta peixes. Quase zero lixo."
    },

    # --- TIER 3: MESTRE (Alta Performance) ---
    "vara_sortuda": {
        "name": "Vara do Trevo", 
        "price": 8000, 
        "tier": 3, 
        "cd": 1.1, "trash": 45, "luck": 2.2,
        "desc": "Sorte absurda, mas o fio é fraco."
    },
    "vara_iridium": {
        "name": "Vara de Iridium", 
        "price": 12000, 
        "tier": 3, 
        "cd": 0.9, "trash": 15, "luck": 1.5,
        "desc": "Metal espacial. O melhor equilíbrio."
    },
    "vara_minigun": {
        "name": "Vara Metralhadora", 
        "price": 18000, 
        "tier": 3, 
        "cd": 0.3, "trash": 80, "luck": 0.9,
        "desc": "DRRRT! Pesca 3x mais rápido, mas destrói tudo."
    },

    # --- TIER 4: LENDÁRIO (Tecnologia Militar) ---
    "vara_magnetica": {
        "name": "Vara Magnética", 
        "price": 45000, 
        "tier": 4, # UPGRADE DE TIER!
        "cd": 0.8, "trash": 10, "luck": 1.8,
        "desc": "Campo magnético que puxa monstros do fundo."
    },
    "vara_sniper": {
        "name": "Vara Sniper .50", 
        "price": 60000, 
        "tier": 4, # UPGRADE DE TIER!
        "cd": 2.0, "trash": 0, "luck": 2.5,
        "desc": "Um tiro, um peixe. Nunca erra (0% Lixo)."
    },

    # --- TIER 5: CÓSMICO (Deuses) ---
    "vara_quantum": {
        "name": "Vara Quântica", 
        "price": 80000, 
        "tier": 5, # UPGRADE DE TIER!
        "cd": 0.5, "trash": 5, "luck": 4.0,
        "desc": "Pesca em realidades paralelas simultaneamente."
    },
    "vara_void": { # NOVA VARA
        "name": "Devoradora do Vazio", 
        "price": 350000, 
        "tier": 5, 
        "cd": 0.8, "trash": 0, "luck": 6.6,
        "desc": "Olhe para ela e ela olhará de volta..."
    }
}

# 1.1 MANUTENÇÃO DE EQUIPAMENTO (sink complementar da Forja do Abismo)
#
# Vara de ponta não é só um custo de compra: a partir do tier 4 ela cobra
# 0,5% do próprio preço a cada lance, descontado do resultado da pescaria —
# não é uma cobrança separada, então o jogador nunca fica devendo por pescar.
# É o contrapeso da escada de sorte: a Devoradora rende 6,6x, mas queima
# 1.750 Sachês por lance, e o tier 3 (a faixa em que a maioria vive) segue
# sem custo nenhum.
#
#   Magnética  (45.000)  ->   225/lance
#   Sniper .50 (60.000)  ->   300/lance
#   Quântica   (80.000)  ->   400/lance
#   Devoradora (350.000) -> 1.750/lance
ROD_MAINTENANCE_MIN_TIER = 4
ROD_MAINTENANCE_RATE = 0.005


def rod_maintenance_cost(rod_key: str) -> int:
    """Custo de manutenção por lance da vara `rod_key` (0 se abaixo do tier 4).

    Derivado do preço em ROD_STATS em vez de uma tabela paralela de valores:
    mexer no preço de uma vara na loja tem que mexer na manutenção dela junto,
    senão as duas divergem no primeiro reajuste.
    """
    stats = ROD_STATS.get(rod_key)
    if not stats or stats["tier"] < ROD_MAINTENANCE_MIN_TIER:
        return 0
    return int(stats["price"] * ROD_MAINTENANCE_RATE)


# 2. CONFIGURAÇÃO DA LOJA E ITENS
SHOP_ITEMS = {
    # --- CONSUMÍVEIS (Buffs para Pesca) ---
    "isca": {"name": "Isca Minhoca", "price": 25, "type": "consumable", "rarity": "common", "desc": "Reduz lixo pela metade (+Valor)."},
    "energetico": {"name": "Energético", "price": 900, "type": "buff", "rarity": "common", "desc": "Reseta cooldown imediatamente."},
    "rede": {"name": "Rede de Mão", "price": 400, "type": "consumable", "rarity": "uncommon", "desc": "Pega 3 itens de uma vez (Consumível)."},
    "caixa_misteriosa": {"name": "Caixa Misteriosa", "price": 500, "type": "box", "rarity": "rare", "desc": "Pode ter dinheiro, itens ou nada."},
    
    # Novos Consumíveis (Para funcionar com o novo comando /pescar)
    "ima_saches": {"name": "Ímã de Sachês", "price": 300, "type": "buff", "rarity": "uncommon", "desc": "Duplica o valor da próxima pesca."},
    "firewall": {"name": "Firewall Portátil", "price": 100, "type": "buff", "rarity": "common", "desc": "Bloqueia 100% de Lixo na próxima pesca."},
    "chip_sorte": {"name": "Chip da Sorte", "price": 6000, "type": "buff", "desc": "Consumível. Se vier peixe Comum, tenta de novo.", "rarity": "legendary"},
    "isca_brilhante": {"name": "Isca Brilhante", "price": 200, "type": "consumable", "rarity": "uncommon", "desc": "Aumenta em 20% a chance de peixes de Tier 2."},
    "isca_fedorenta": {"name": "Isca Fedorenta", "price": 150, "type": "consumable", "rarity": "common", "desc": "Atrai peixes comuns, reduz lixo."},
    "isca_eletrica": {"name": "Isca Elétrica", "price": 400, "type": "consumable", "rarity": "rare", "desc": "Chance extra de peixes elétricos/raros."},

    # --- VARAS (Sincronizado com ROD_STATS) ---
    # TIER 0 & 1
    "vara_treino":   {"name": "Vara de Treino", "price": 250, "type": "rod", "key": "vara_treino", "tier": 1, "rarity": "common", "desc": "Para iniciantes aprenderem."},
    "vara_plastico": {"name": "Vara Reciclada", "price": 600, "type": "rod", "key": "vara_plastico", "tier": 1, "rarity": "uncommon", "desc": "Feita de garrafas. Barata."},
    "vara_fibra":    {"name": "Vara de Fibra",  "price": 900, "type": "rod", "key": "vara_fibra", "tier": 1, "rarity": "uncommon", "desc": "Equilibrada e resistente."},
    "vara_pesada":   {"name": "Vara de Chumbo", "price": 1200, "type": "rod", "key": "vara_pesada", "tier": 1, "rarity": "uncommon", "desc": "Afunda rápido."},
    
    # TIER 2 (Profissional)
    "vara_veloz":    {"name": "Vara Aerodinâmica", "price": 2500, "type": "rod", "key": "vara_veloz", "tier": 2, "rarity": "rare", "desc": "Muito rápida (CD Baixo)."},
    "vara_ouro":     {"name": "Vara de Ouro",   "price": 3500, "type": "rod", "key": "vara_ouro", "tier": 2, "rarity": "rare", "desc": "Atrai peixes valiosos."},
    "vara_sonar":    {"name": "Vara Sonar",     "price": 4200, "type": "rod", "key": "vara_sonar", "tier": 2, "rarity": "rare", "desc": "Quase zero lixo."},
    
    # TIER 3 (Mestre)
    "vara_sortuda":  {"name": "Vara do Trevo",  "price": 8000, "type": "rod", "key": "vara_sortuda", "tier": 3, "rarity": "epic", "desc": "Sorte x2.2."},
    "vara_iridium":  {"name": "Vara de Iridium","price": 12000, "type": "rod", "key": "vara_iridium", "tier": 3, "rarity": "epic", "desc": "A melhor all-rounder."},
    "vara_minigun":  {"name": "Vara Metralhadora", "price": 18000, "type": "rod", "key": "vara_minigun", "tier": 3, "rarity": "epic", "desc": "Pesca insanamente rápido."},
    
    # TIER 4 (Lendário)
    "vara_magnetica": {"name": "Vara Magnética", "price": 45000, "type": "rod", "key": "vara_magnetica", "tier": 4, "rarity": "legendary", "desc": "Puxa monstros do fundo."},
    "vara_sniper":   {"name": "Vara Sniper .50", "price": 60000, "type": "rod", "key": "vara_sniper", "tier": 4, "rarity": "legendary", "desc": "100% Precisão. Lenta."},

    # TIER 5 (Cósmico)
    "vara_quantum":  {"name": "Vara Quântica",  "price": 80000, "type": "rod", "key": "vara_quantum", "tier": 5, "rarity": "mythic", "desc": "Multidimensional."},
    "vara_void":     {"name": "Devoradora do Vazio", "price": 350000, "type": "rod", "key": "vara_void", "tier": 5, "rarity": "mythic", "desc": "O fim da pescaria."},

    # --- FLEX (Ostentação) ---
    "certificado": {"name": "Certificado de Dono", "price": 5000, "type": "flex", "rarity": "rare", "desc": "Papel inútil de rico."},
    "bota_gucci": {"name": "Bota de Marca", "price": 8000, "type": "flex", "rarity": "rare", "desc": "Etiqueta cara."},
    "nft_peixe": {"name": "NFT de Peixe", "price": 15000, "type": "flex", "rarity": "epic", "desc": "Blockchain garantida."},
    "bigode_cosmico": {"name": "Bigode Cósmico", "price": 50000, "type": "flex", "rarity": "epic", "desc": "Brilha no escuro."},

    # --- SECRETOS (Inalterados) ---
    "item_criador": {"name": "Teclado do Arquiteto", "price": 0, "type": "flex", "rarity": "mythic", "desc": "Exclusivo do Criador."},
    "item_dono": {"name": "Coroa do Imperador", "price": 0, "type": "flex", "rarity": "mythic", "desc": "Exclusivo do Dono."},

    # --- ITENS DE QUEST (Inalterados) ---
    "garrafa_incrustada": {"name": "Garrafa Incrustada", "price": 0, "type": "quest", "rarity": "quest", "desc": "Tem algo dentro... Use /ler_garrafa"},
    "selo_capitao": {"name": "Selo do Capitão", "price": 0, "type": "quest", "rarity": "epic", "desc": "Permite entrar em Porto Solare."}
}

# 3. VENDA DE PEIXE DA MOCHILA (/eco vender)
#
# Peixe pescado por /eco pescar vira Sachê na hora e nunca entra na mochila —
# só o lixo entra. O que enche a mochila de peixe é a armadilha AFK, e até
# aqui esse peixe não tinha destino nenhum: metade a 60% de cada coleta era
# item morto. Este é o escoadouro.
#
# A taxa cai conforme o tier sobe de propósito: peixe raro tem que valer a
# pena PESCAR, não farmar em armadilha. Sem essa inclinação, a Rede de
# Arrasto (que alcança tier 2) viraria a melhor fonte de renda do meio de
# jogo. Calibrado para o Covo render ~40% da pesca ativa da faixa em que ele
# é destravado.
SELL_RATES = {0: 0.35, 1: 0.30, 2: 0.22, 3: 0.18, 4: 0.15}

# Espécie -> (tier, v_min, v_max). Lixo fica fora: ele não se vende aqui, vai
# para o Galdino virar sucata, que é o único destino que ele sempre teve.
FISH_BY_NAME = {
    p[0]: (p[4], p[1], p[2]) for p in FISH_DB if p[0] not in TRASH_ITEMS
}


# Sucata por peixe vendido (Item 5). A sucata só vinha de lixo, e as varas
# boas existem justamente para evitar lixo — Sniper .50 e Devoradora têm 0% e
# não produziam nenhuma, para sempre, enquanto os upgrades pagos em sucata são
# o melhor retorno por unidade de recurso do jogo. Isto dá à sucata uma
# segunda fonte que não anda contra a progressão.
SCRAP_PER_FISH = {0: 1, 1: 1, 2: 3, 3: 8, 4: 20}


def fish_scrap_yield(fish_name: str) -> int:
    dados = FISH_BY_NAME.get(fish_name)
    return SCRAP_PER_FISH.get(dados[0], 0) if dados else 0


def grant_scrap(conn, user_id: int, bruto: int) -> int:
    """Credita sucata aplicando o multiplicador da ilha. Retorna o creditado.

    Ponto único: toda fonte de sucata passa por aqui, senão o Baú da Maré
    valeria só onde alguém lembrou de multiplicar.
    """
    if bruto <= 0:
        return 0
    mult = get_island_bonuses(conn, user_id)["sucata_mult"]
    total = int(bruto * mult)
    modify_scrap(conn, user_id, total)
    return total


def fish_sell_price(fish_name: str) -> int:
    """Preço de venda de uma unidade, ou 0 se não for peixe vendável.

    Reusa o valor-base do FISH_DB (a mediana do intervalo que /eco pescar
    sorteia) em vez de uma tabela de preços paralela — duas tabelas para o
    mesmo conceito divergem no primeiro peixe novo que alguém adicionar.
    """
    dados = FISH_BY_NAME.get(fish_name)
    if not dados:
        return 0
    tier, v_min, v_max = dados
    return int((v_min + v_max) / 2 * SELL_RATES.get(tier, 0.0))


#4 --- SISTEMA DE CLIMA ---
WEATHER_EFFECTS = {
    "normal": {"name": "Céu Limpo", "desc": "Um dia perfeito para pescar.", "luck_mod": 1.0, "trash_mod": 1.0, "tier_bonus": 0},
    "bad":    {"name": "Tempestade Sombria", "desc": "Rugidos vêm do mar... Os peixes se escondem!", "luck_mod": 0.5, "trash_mod": 2.0, "tier_bonus": 0},
    "good":   {"name": "Brisa Dourada", "desc": "Uma correnteza mística atrai peixes raros!", "luck_mod": 1.5, "trash_mod": 0.5, "tier_bonus": 1}
}

def get_current_weather():
    cursor = get_bot_instance().db_conn.cursor()
    row = cursor.execute("SELECT current_weather FROM world_state WHERE id = 1").fetchone()
    w_key = row['current_weather'] if row else "normal"
    return w_key, WEATHER_EFFECTS.get(w_key, WEATHER_EFFECTS["normal"])

# --- SISTEMA DE RANKS DA GUILDA (COM TRAVA NO RANK A) ---
GUILD_RANKS = {
    "F": {
        "name": "Novato do Anzol", 
        "req_xp": 0, 
        "next": "E", 
        "badge": "🐟",
        "desc": "Acabou de pegar na vara."
    },
    "E": {
        "name": "Rato de Cais", 
        "req_xp": 500, 
        "next": "D", 
        "badge": "🐀",
        "desc": "Já sabe diferenciar bota de peixe."
    },
    "D": {
        "name": "Mergulhador", 
        "req_xp": 1500, 
        "next": "C", 
        "badge": "🤿",
        "desc": "Não tem medo de molhar o pé."
    },
    "C": {
        "name": "Caçador", 
        "req_xp": 4000, 
        "next": "B", 
        "badge": "🏹",
        "desc": "Especialista em peixes perigosos."
    },
    "B": {
        "name": "Veterano dos Mares", 
        "req_xp": 10000, 
        "next": "A", 
        "badge": "⚓",
        "desc": "Respeitado em qualquer porto."
    },
    "A": {
        "name": "Mestre Pescador", 
        "req_xp": 25000, 
        "next": None, # <--- TRAVA AQUI! Não sobe para S automaticamente.
        "badge": "🔱",
        "desc": "O ápice humano. O Rank S é apenas um mito antigo..."
    },
    
    # --- CONTEÚDO FUTURO (BLOQUEADO) ---
    "S": {
        "name": "Herói de Solare",
        "req_xp": 999999, # Valor simbólico impossível
        "next": None,
        "badge": "👑",
        "desc": "??? (Requer Feito Heroico - Em Breve)"
    }
}


def next_rank_requirement(rank_key):
    """(chave do próximo rank, XP para alcançá-lo) a partir de `rank_key`.

    `req_xp` de uma entrada é o custo para CHEGAR naquele rank, não para sair
    dele — é assim que a credencial da guilda (`card_btn`) sempre leu a
    tabela, com `GUILD_RANKS[next]['req_xp']` como meta da barra de
    progresso. Os dois pontos que resolviam promoção, porém, comparavam o XP
    com o `req_xp` do rank ATUAL, o que deslocava a escada inteira um degrau
    para baixo: rank F tem `req_xp` 0, então a promoção F→E saía no primeiro
    lance, e chegar ao rank A custava 16.000 XP acumulados em vez dos 41.000
    que a tabela define (500+1500+4000+10000+25000).

    Existe para que a regra viva num lugar só: antes ela estava escrita duas
    vezes, em `/eco pescar` e no diálogo da Capitã, e as duas cópias tinham
    o mesmo erro.

    Retorna (None, None) para quem já está no topo da escada.
    """
    data = GUILD_RANKS.get(rank_key, GUILD_RANKS["F"])
    next_key = data["next"]
    if not next_key:
        return None, None
    return next_key, GUILD_RANKS[next_key]["req_xp"]

# --- BANCO DE DADOS DE MISSÕES (COMPLETO & BALANCEADO) ---
# Tipos: 'fish_count', 'fish_specific', 'earn_money', 'explore_count'

MISSION_DB = {
    # RANK F: INICIANTE (Fácil e Rápido)
    "F": [
        {"id": "f1", "title": "Primeiros Passos", "desc": "Pesque 5 peixes (Qualquer tipo).", "type": "fish_count", "target": 5, "xp": 40, "reward": 50},
        {"id": "f2", "title": "Caça ao Lambari", "desc": "Traga 3 Lambaris para a senhora do gato.", "type": "fish_specific", "target_fish": "Lambari", "target": 3, "xp": 50, "reward": 80},
        {"id": "f3", "title": "Sardinha em Lata", "desc": "Pesque 5 Sardinhas.", "type": "fish_specific", "target_fish": "Sardinha", "target": 5, "xp": 50, "reward": 100},
        {"id": "f4", "title": "Limpeza da Praia", "desc": "Retire 3 Botas, Latas ou Sacolas do mar.", "type": "fish_specific", "target_fish": ["Bota Velha", "Lata Vazia", "Sacola Plástica"], "target": 3, "xp": 30, "reward": 150},
        {"id": "f5", "title": "Lucro Inicial", "desc": "Acumule 100 Sachês pescando.", "type": "earn_money", "target": 100, "xp": 40, "reward": 50},
        {"id": "f6", "title": "Drone Curioso", "desc": "Use o comando /eco explorar 1 vez.", "type": "explore_count", "target": 1, "xp": 60, "reward": 0},
        {"id": "f7", "title": "Tilápia Fresca", "desc": "Pesque 2 Tilápias.", "type": "fish_specific", "target_fish": "Tilápia", "target": 2, "xp": 45, "reward": 70},
        {"id": "f8", "title": "Fugitivo", "desc": "Encontre 1 Peixe Dourado (O de aquário).", "type": "fish_specific", "target_fish": "Peixe Dourado", "target": 1, "xp": 60, "reward": 100},
    ],
    
    # RANK E: RATO DE CAIS (Grind Leve)
    "E": [
        {"id": "e1", "title": "Estoque do Mercado", "desc": "Entregue 10 Sardinhas.", "type": "fish_specific", "target_fish": "Sardinha", "target": 10, "xp": 100, "reward": 200},
        {"id": "e2", "title": "Perigo Dentuço", "desc": "Pesque 3 Piranhas.", "type": "fish_specific", "target_fish": "Piranha", "target": 3, "xp": 120, "reward": 300},
        {"id": "e3", "title": "Sopa de Bagre", "desc": "Pesque 5 Bagres.", "type": "fish_specific", "target_fish": "Bagre", "target": 5, "xp": 110, "reward": 250},
        {"id": "e4", "title": "Jornada de Trabalho", "desc": "Pesque 25 vezes.", "type": "fish_count", "target": 25, "xp": 150, "reward": 400},
        {"id": "e5", "title": "Investidor", "desc": "Acumule 500 Sachês em vendas.", "type": "earn_money", "target": 500, "xp": 130, "reward": 100},
        {"id": "e6", "title": "Coquetel de Camarão", "desc": "Pesque 5 Camarões.", "type": "fish_specific", "target_fish": "Camarão", "target": 5, "xp": 130, "reward": 350},
        {"id": "e7", "title": "Andando de Lado", "desc": "Capture 4 Caranguejos.", "type": "fish_specific", "target_fish": "Caranguejo", "target": 4, "xp": 125, "reward": 300},
        {"id": "e8", "title": "Tesouro do Lixo", "desc": "Pesque 10 lixos (Limpando o oceano).", "type": "fish_specific", "target_fish": ["Bota Velha", "Lata Vazia", "Pneu Furado"], "target": 10, "xp": 100, "reward": 500},
    ],

    # RANK D: MERGULHADOR (Exóticos)
    "D": [
        {"id": "d1", "title": "Brilho do Sol", "desc": "Capture 2 Dourados do Mar.", "type": "fish_specific", "target_fish": "Dourado do Mar", "target": 2, "xp": 200, "reward": 600},
        {"id": "d2", "title": "Procurando Nemo", "desc": "Ache 3 Peixes-Palhaço.", "type": "fish_specific", "target_fish": "Peixe-Palhaço", "target": 3, "xp": 210, "reward": 550},
        {"id": "d3", "title": "Pesca Intensiva", "desc": "Pesque 50 peixes.", "type": "fish_count", "target": 50, "xp": 300, "reward": 800},
        {"id": "d4", "title": "Oito Braços", "desc": "Pesque 2 Polvos.", "type": "fish_specific", "target_fish": "Polvo", "target": 2, "xp": 180, "reward": 500},
        {"id": "d5", "title": "Regeneração", "desc": "Capture 2 Axolotes raros.", "type": "fish_specific", "target_fish": "Axolote", "target": 2, "xp": 250, "reward": 700},
        {"id": "d6", "title": "Magnata D", "desc": "Lucre 1500 Sachês.", "type": "earn_money", "target": 1500, "xp": 200, "reward": 300},
        {"id": "d7", "title": "Rio Amazonas", "desc": "Capture 3 Tucunarés.", "type": "fish_specific", "target_fish": "Tucunaré", "target": 3, "xp": 220, "reward": 450},
    ],

    # RANK C: CAÇADOR (Difícil)
    "C": [
        {"id": "c1", "title": "Tubarão à Vista", "desc": "Capture 1 Tubarão Martelo.", "type": "fish_specific", "target_fish": "Tubarão Martelo", "target": 1, "xp": 400, "reward": 1000},
        {"id": "c2", "title": "Duelo de Espadas", "desc": "Pesque 2 Peixes-Espada.", "type": "fish_specific", "target_fish": "Peixe-Espada", "target": 2, "xp": 450, "reward": 1200},
        {"id": "c3", "title": "Maratona C", "desc": "Pesque 100 peixes.", "type": "fish_count", "target": 100, "xp": 600, "reward": 2000},
        {"id": "c4", "title": "Luz na Escuridão", "desc": "Pesque 2 Peixes-Lanterna.", "type": "fish_specific", "target_fish": "Peixe-Lanterna", "target": 2, "xp": 500, "reward": 1500},
        {"id": "c5", "title": "Alta Voltagem", "desc": "Capture 3 Enguias Elétricas.", "type": "fish_specific", "target_fish": "Enguia Elétrica", "target": 3, "xp": 480, "reward": 1300},
    ],

    # RANK B: VETERANO (Grandes Feras - Tier 3)
    "B": [
        {"id": "b1", "title": "Predador Apex", "desc": "Capture 1 Tubarão Branco.", "type": "fish_specific", "target_fish": "Tubarão Branco", "target": 1, "xp": 800, "reward": 2500},
        {"id": "b2", "title": "Gigante Gentil", "desc": "Encontre 1 Baleia Azul.", "type": "fish_specific", "target_fish": "Baleia Azul", "target": 1, "xp": 900, "reward": 3000},
        {"id": "b3", "title": "Lenda do Unicórnio", "desc": "Capture 1 Narval.", "type": "fish_specific", "target_fish": "Narval", "target": 1, "xp": 750, "reward": 2200},
        {"id": "b4", "title": "Pescador de Elite", "desc": "Acumule 10.000 Sachês.", "type": "earn_money", "target": 10000, "xp": 1000, "reward": 1000},
        {"id": "b5", "title": "Free Willy", "desc": "Pesque 1 Orca.", "type": "fish_specific", "target_fish": "Orca", "target": 1, "xp": 850, "reward": 2800},
    ],

    # RANK A: MESTRE (Míticos - Tier 3 e 4)
    "A": [
        {"id": "a1", "title": "Extinção Cancelada", "desc": "Capture 1 Megalodon.", "type": "fish_specific", "target_fish": "Megalodon", "target": 1, "xp": 2000, "reward": 5000},
        {"id": "a2", "title": "Vingança de Ahab", "desc": "Capture a Moby Dick.", "type": "fish_specific", "target_fish": "Moby Dick", "target": 1, "xp": 2500, "reward": 6000},
        {"id": "a3", "title": "O Pesadelo", "desc": "Capture 1 Lula Gigante.", "type": "fish_specific", "target_fish": "Lula Gigante", "target": 1, "xp": 1800, "reward": 4000},
        {"id": "a4", "title": "Canto da Sereia", "desc": "Encontre 1 Sereia.", "type": "fish_specific", "target_fish": "Sereia", "target": 1, "xp": 3000, "reward": 8000},
        {"id": "a5", "title": "Colecionador Sombrio", "desc": "Pesque 300 peixes no total.", "type": "fish_count", "target": 300, "xp": 1500, "reward": 5000},
    ],

    # RANK S: LENDA (Cósmicos - Tier 4+)
    "S": [
        {"id": "s1", "title": "O Chamado", "desc": "Capture o CTHULHU.", "type": "fish_specific", "target_fish": "CTHULHU", "target": 1, "xp": 10000, "reward": 50000},
        {"id": "s2", "title": "Rei dos Monstros", "desc": "Capture o Godzilla (Aquático).", "type": "fish_specific", "target_fish": "Godzilla (Aquático)", "target": 1, "xp": 8000, "reward": 30000},
        {"id": "s3", "title": "Apocalipse", "desc": "Capture 1 Leviatã.", "type": "fish_specific", "target_fish": "Leviatã", "target": 1, "xp": 5000, "reward": 15000},
        {"id": "s4", "title": "A Fenda do Biquíni", "desc": "Pesque o Bob Esponja.", "type": "fish_specific", "target_fish": "Bob Esponja", "target": 1, "xp": 4000, "reward": 10000},
        {"id": "s5", "title": "Multimilionário", "desc": "Acumule 100.000 Sachês.", "type": "earn_money", "target": 100000, "xp": 5000, "reward": 10000},
    ]
}

# --- BANCO DE DADOS DE LORE & DIÁLOGOS (v3.1 - THE PRIMORDIAL RIFT) ---
NPC_DIALOGUES = {
    "jenna": {
        "intro": [
            "🛡️ **Capitã Jenna:** 'Desculpa a bagunça, novato(a). Muita coisa acontecendo recentemente...'",
            "🛡️ **Capitã Jenna:** 'Sou a Capitã Jenna Boldwind. Eu comando essa guilda e tento manter a ordem em Porto Solare.'"
        ],
        "about_leader": [
            "🛡️ **Capitã Jenna:** 'Desde quando? Hahaha! Pode se dizer que nasci aqui. Estou seguindo os passos do meu pai.'",
            "🛡️ **Capitã Jenna:** 'Liderança não é só dar ordens, é garantir que vocês não virem comida de peixe.'"
        ],
        "rank_up_info": "🛡️ **Capitã Jenna:** (Aponta para o quadro) 'Pegue uma missão, faça o trabalho. Simples assim.'",
        "rank_s_lock": "🛡️ **Capitã Jenna:** 'O Rank S? Ah... isso exige um **Feito Histórico** que salve a cidade. Ainda não vi isso em você.'"
    },
    "galdino": {
        "intro": [
            "🔧 **Galdino:** 'Humm... sangue fresco? Se veio trocar sucata, jogue na mesa.'",
            "🔧 **Galdino:** 'Sou Galdino II. Conserto o que você quebra. Nada muito especial, apenas o essencial.'"
        ],
        "about_time": [
            "🔧 **Galdino:** 'Velhote?! Sua mãe não te deu modos, moleque?! (Bufa) 30 anos batendo martelo aqui. Respeita!'",
            "🔧 **Galdino:** 'Aqueles nobres jogam fora processadores inteiros porque saiu um modelo novo. Sorte a nossa.'"
        ],
        "afk_machine_intro": "🔧 **Galdino:** (Chuta uma engrenagem) 'Aquilo? Protótipos de armadilhas automáticas. Nunca terminei... Olha, se me trouxer **50 Peças de Lixo** para reciclar, eu te vendo uma.'",
        "afk_success": "🔧 **Galdino:** 'É disso que eu tô falando! Tome, consertei esse covo pra você.'",
        "recycle_success": "🔧 **Galdino:** 'Bom material! Isso vai virar um motor V8.'",
        "recycle_fail": "🔧 **Galdino:** 'Sua mochila tá limpa demais. Suma daqui!'"
    },
    "valerius": {
        "intro": "💰 **Valerius:** 'Saudações! O cheiro de ouro no seu bolso me atrai como tubarão! Valerius Chrome ao seu dispor.'",
        "shop_open": "💰 **Valerius:** 'Eu vendo sonhos... em formato de varas de pesca. Escolha com sabedoria.'"
    },
    "tavern": {
        "rumors": [
            "🍺 **Taberneiro:** 'Vi um navio da Guarda Real voltar ontem... ou o que sobrou dele. Parecia mastigado.'",
            "🍺 **Taberneiro:** 'O mar recuou 2 metros essa semana. O que diabos está bebendo toda essa água?'",
            "🍺 **Taberneiro:** 'Dizem que a Capitã Jenna chora escondida. Ela perdeu muitos amigos na última expedição.'",
            "🍺 **Taberneiro:** 'Cuidado com as luzes de neon na área nobre. Os autômatos estão ficando... agressivos.'"
        ]
    }
}

WORLD_LORE = {
    "island": {
        "title": "🏝️ Ilha do Náufrago (Lar)",
        "description": "Sua ilha é vasta, mas isolada por uma muralha eterna de neblina.\nAqui a vida é simples: recursos básicos e o som do mar.\nNinguém entra, ninguém sai... até agora."
    },
    "city": {
        "title": "🏙️ Porto Solare (Sob Lei Marcial)",
        "description": (
            "A joia costeira de Malrest, agora tomada por tensão e soldados.\n\n"
            "🏰 **Visual:** Ruas de paralelepípedo medieval iluminadas por neons mágicos.\n"
            "🛡️ **Crise:** A Guarda Real está em alerta máximo. Transportes marítimos chegam danificados.\n"
            "⚖️ **Sociedade:** Nobres desfilam com robôs, enquanto aventureiros lotam o porto para caçar bestas."
        )
    },
    "sea": {
        "title": "🌊 A Fenda (Mar Aberto)",
        "description": (
            "O horizonte está mudando. O nível do mar recua dia após dia.\n"
            "🕳️ **A Anomalia:** Um vórtice gigantesco, uma 'Fenda', parece drenar a vida do mundo.\n"
            "⚠️ **Perigo Extremo:** Criaturas carnívoras e bestas colossais agora caçam na superfície."
        )
    }
}

def _mission_blocked_msg(reserva: dict, titulo: str) -> str:
    """Texto para quando a missão fechou mas o teto diário não deixa pagar.

    O progresso é zerado de qualquer forma (a missão foi cumprida), então o
    jogador precisa saber que ele não perdeu nada por bug — bateu num limite.
    """
    if reserva["reason"] == "daily_cap":
        return (
            f"\n🏁 **{titulo}** concluída, mas o grupo já bateu o teto de "
            f"{MISSION_DAILY_CAP} missões hoje. Recompensa não paga — volte amanhã."
        )
    return (
        f"\n🔁 **{titulo}** já tinha sido concluída hoje por este grupo. "
        "Recompensa não paga; escolha outra missão no quadro."
    )


def get_dialogue(npc, key):
    res = NPC_DIALOGUES.get(npc, {}).get(key, "...")
    return random.choice(res) if isinstance(res, list) else res
def get_daily_shop():
    # 1. Lista de Itens ESSENCIAIS (Sempre aparecem fixos)
    # O Chip da Sorte NÃO está aqui (agora ele é rotativo/raro)
    essential_keys = [
        "isca", "energetico", "rede", "caixa_misteriosa", 
        "ima_saches", "firewall"
    ]
    
    # 2. LISTA NEGRA (Ban List) - Segurança Máxima
    # Coloque aqui o ID (key) exato do item de quest que vazou na loja
    ban_list = [
        "chave_antiga", "mapa_tesouro", "item_de_quest_aqui", # Exemplo
        "vara_void", "admin_item", # Outros itens proibidos
        # isca_eletrica descontinuada: efeito idêntico ao chip_sorte por
        # 1/15 do preço (mesma flag used_chip, mesmo pool tier>=2). Fica na
        # SHOP_ITEMS só pra quem já possui cópias continuar usando/vendendo.
        "isca_eletrica",
    ]

    final_shop = []
    
    # Adiciona os Essenciais
    for key in essential_keys:
        if key in SHOP_ITEMS:
            item = SHOP_ITEMS[key].copy()
            item['key'] = key
            final_shop.append(item)

    # 3. Cria o Pool de Rotação (Destaques)
    rotation_pool = []
    
    for key, item in SHOP_ITEMS.items():
        # Filtro 1: Já é essencial? Pula.
        if key in essential_keys: continue
        
        # Filtro 2: Está na Lista Negra? Pula!
        if key in ban_list: continue

        # Filtro 3: Checa Raridade e Tipo
        # Se for Mítico, Quest ou se o preço for 0 ou negativo (item inestimável)
        if item.get("rarity") in ["mythic", "quest", "admin"]: continue
        if item.get("type") == "quest_item": continue
        if item.get("price", 0) <= 0: continue 

        # Se passou por tudo, pode ir pra loja
        item_data = item.copy()
        item_data['key'] = key
        rotation_pool.append(item_data)

    # 4. Sorteio Sincronizado (Seed Diária)
    hoje = datetime.now().strftime("%Y-%m-%d")
    rng = random.Random(hoje)
    
    qtd_extras = 6
    if rotation_pool:
        # Ordena antes de sortear para garantir que a Seed funcione igual pra todos
        rotation_pool.sort(key=lambda x: x['key']) 
        random_picks = rng.sample(rotation_pool, min(len(rotation_pool), qtd_extras))
        final_shop.extend(random_picks)
    
    return final_shop

# --- GRUPO DE COMANDOS ---
@eco_group.command(name="loja", description="Abre o P3LUCHE Market (Visual Clássico).")
async def loja(interaction: discord.Interaction):
    await interaction.response.defer()

    # 1. Pega os dados
    daily_items = get_daily_shop()
    
    # 2. Separa em duas listas: Essenciais e Destaques
    # Lista de chaves que consideramos "Essenciais" (fixos)
    essential_keys = ["isca", "energetico", "rede", "caixa_misteriosa", "ima_saches", "firewall", "chip_sorte"]
    
    lista_essenciais = []
    lista_destaques = []

    for item in daily_items:
        if item['key'] in essential_keys:
            lista_essenciais.append(item)
        else:
            lista_destaques.append(item)

    # Ordena os destaques por preço
    lista_destaques.sort(key=lambda x: x['price'])

    # 3. Função de Formatação Visual (Igual à foto antiga)
    def format_row(item):
        rarity_map = {'common': '⚪', 'uncommon': '🟢', 'rare': '🔵', 'epic': '🟣', 'legendary': '🟠'}
        type_icons = {'rod': '🎣', 'consumable': '🧪', 'flex': '💎', 'buff': '⚡', 'box': '📦'}
        
        icon = rarity_map.get(item.get('rarity', 'common'), '⚪')
        type_ic = type_icons.get(item.get('type'), '📦')
        
        # Formato: > ⚪ 🎣 **Nome** — 💰 500
        #          > *Descrição curta*
        return f"> {icon} {type_ic} **{item['name']}** — 💰 {item['price']}\n> *{item['desc']}*\n"

    # 4. Monta o Embed Bonito
    embed = discord.Embed(
        title="💾 P3LUCHE Market_v3.2", 
        description="*\"Sachês aceitos. Fiado? Negativo.\"* 🐱\nSelecione um produto no menu abaixo.",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3081/3081840.png")

    # Campo 1: Essenciais
    if lista_essenciais:
        text_essencial = "".join([format_row(i) for i in lista_essenciais])
        embed.add_field(name="📦 __Suprimentos Essenciais__", value=text_essencial, inline=False)

    # Campo 2: Separador Invisível (opcional, para dar respiro)
    embed.add_field(name="⠀", value="⠀", inline=False)

    # Campo 3: Destaques (Rotação)
    if lista_destaques:
        text_destaque = "".join([format_row(i) for i in lista_destaques])
        embed.add_field(name="♻️ __Ofertas Rotativas (Destaques)__", value=text_destaque, inline=False)

    # Rodapé com Timer
    midnight = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ts = int(midnight.timestamp())
    embed.set_footer(text="Legenda: ⚪Comum 🟢Incomum 🔵Raro 🟣Épico 🟠Lendário")
    embed.add_field(name="🕒 Próxima Rotação:", value=f"<t:{ts}:R>", inline=False)

    # 5. Menu Dropdown (Lógica Nova de Modal)
    view = discord.ui.View()
    select_options = []
    
    # Junta tudo de novo só para o menu ficar na ordem certa (Essenciais primeiro)
    all_menu_items = lista_essenciais + lista_destaques
    
    for item in all_menu_items[:25]: 
        emoji = "🔹"
        if item.get('rarity') == 'legendary': emoji = "🟠"
        if item.get('rarity') == 'epic': emoji = "🟣"
        
        select_options.append(discord.SelectOption(
            label=f"{item['name']} ({item['price']} $)",
            value=item['key'],
            description=item.get('desc', '')[:95],
            emoji=emoji
        ))

    select = discord.ui.Select(placeholder="🛒 Selecione o item para comprar...", options=select_options)

    # Callback Inteligente (Igual ao anterior que funcionou)
    async def shop_callback(inter: discord.Interaction):
        item_key = select.values[0]
        item_stats = SHOP_ITEMS.get(item_key)
        
        if not item_stats: return await inter.response.send_message("❌ Item sumiu.", ephemeral=True)

        conn = get_bot_instance().db_conn
        if not has_account(conn, inter.user.id):
            return await inter.response.send_message("❌ Crie conta com /eco pescar.", ephemeral=True)

        tipo = item_stats.get('type')

        # ROTA A: VARAS (Compra 1x Direto e Salva no Inventário) — atômico:
        # relê saldo fresco na hora de gravar em vez de um valor capturado
        # antes.
        if tipo == 'rod':
            custo = item_stats['price']
            if not try_spend_wallet(conn, inter.user.id, custo, inter.user.name):
                return await inter.response.send_message(f"💸 Falta grana ({custo}).", ephemeral=True)

            add_inventory_item(conn, inter.user.id, item_key, 1)
            set_current_rod(conn, inter.user.id, item_key)

            await inter.response.send_message(f"🎣 **Compra Efetuada!**\n**{item_stats['name']}** foi adicionada à mochila e equipada.", ephemeral=True)

        # ROTA B: CONSUMÍVEIS (Abre Modal de Quantidade)
        else:
            modal = CompraQuantidadeModal(item_key, item_stats, inter.user.id, get_bot_instance())
            await inter.response.send_modal(modal)

    select.callback = shop_callback
    view.add_item(select)

    await interaction.followup.send(embed=embed, view=view)

@eco_group.command(name="comprar", description="Compre itens para sua mochila.")
@app_commands.describe(item="Código do item (ex: isca, energetico)")
async def comprar(interaction: discord.Interaction, item: str):
    ID_CRIADOR = 299323165937500160
    ID_DONO = 541680099477422110
    user_id = interaction.user.id

    # --- CHECAGEM DE ITENS ESPECIAIS/SECRETOS ---
    if item == "item_criador":
        if user_id != ID_CRIADOR: return await interaction.response.send_message("⛔ Acesso Negado.", ephemeral=True)
    elif item == "item_dono":
        if user_id != ID_DONO: return await interaction.response.send_message("🔥 Pesado demais para você.", ephemeral=True)
    else:
        # Checa se está na loja do dia
        daily_shop = get_daily_shop()
        # Isca e Energético sempre disponíveis
        if item not in daily_shop and item not in ["isca", "energetico"]:
            return await interaction.response.send_message(f"🚫 O item `{item}` não está na loja hoje.", ephemeral=True)

    if item not in SHOP_ITEMS: return await interaction.response.send_message("❌ Item inválido.", ephemeral=True)

    data = SHOP_ITEMS[item]
    price = data['price']
    conn = get_bot_instance().db_conn

    # Exige conta já existente (não cria uma nova aqui). has_account() lê
    # `users` e, de propósito, não chama ensure_user — senão o próprio
    # portão criaria a conta que ele deveria estar barrando.
    if not has_account(conn, user_id):
        return await interaction.response.send_message("❌ Use /eco pescar primeiro.", ephemeral=True)

    # --- COMPRA E ARMAZENAMENTO (atômico: relê saldo fresco na hora de gravar) ---
    if not try_spend_wallet(conn, user_id, price, interaction.user.name):
        wallet_atual = get_wallet(conn, user_id)
        return await interaction.response.send_message(f"💸 Sem saldo ({wallet_atual} < {price}).", ephemeral=True)

    # Adiciona o item na mochila (SOMA +1). A coluna legada 'baits' é
    # derivada automaticamente do inventário por sync_user_to_economy —
    # não precisa mais de tratamento manual aqui.
    add_inventory_item(conn, user_id, item, 1)

    # Mensagem de confirmação
    emoji_tipo = "🎒"
    if data['type'] == 'rod': emoji_tipo = "🎣"
    if data['type'] == 'buff': emoji_tipo = "⚡"
    
    await interaction.response.send_message(f"✅ **Compra realizada!**\n{emoji_tipo} **{data['name']}** foi guardado na sua mochila.\nUse `/eco saldo` para ver ou usar.", ephemeral=True)


# --- CAIXA MISTERIOSA ---
# Custava 500 e pagava randint(100, 1000): EV 550, ou seja, +10% de margem
# PARA O JOGADOR. Um "sink" com valor esperado positivo é uma impressora de
# dinheiro — comprar em lote era renda, não gasto, e o modal de compra aceita
# até 4 dígitos de quantidade.
#
# A distribuição abaixo devolve a margem para a casa (EV ~451, -9,8%) e de
# quebra conserta um problema de sensação: a faixa antiga era estreita demais
# para a caixa ser emocionante. Agora existe um prêmio grande de verdade em
# 2,5% das aberturas, e a maioria das aberturas é pequena.
CAIXA_PREMIO_ITENS = ("isca", "firewall", "isca_fedorenta", "isca_brilhante", "ima_saches")

CAIXA_FAIXAS = (
    (0.600, "dinheiro", 50, 400),
    (0.295, "dinheiro", 400, 900),
    (0.080, "item", None, None),
    (0.025, "jackpot", 3000, 6000),
)


def abrir_caixa_misteriosa(rng=random) -> dict:
    """Sorteia o resultado de uma Caixa Misteriosa.

    Devolve {"tipo", "valor", "item"} em vez de já creditar: separar o sorteio
    do efeito é o que permite medir o EV da tabela num teste sem falsear
    saldo, e o EV é justamente a propriedade que estava errada.
    """
    ponto = rng.random()
    acumulado = 0.0
    for peso, tipo, lo, hi in CAIXA_FAIXAS:
        acumulado += peso
        if ponto < acumulado:
            if tipo == "item":
                return {"tipo": tipo, "valor": 0, "item": rng.choice(CAIXA_PREMIO_ITENS)}
            return {"tipo": tipo, "valor": rng.randint(lo, hi), "item": None}
    # Só alcançável por erro de arredondamento no topo da faixa.
    return {"tipo": "dinheiro", "valor": rng.randint(50, 400), "item": None}


def desmanche_yield(tier: int) -> int:
    """Sucata que uma captura de `tier` rende se for desmanchada."""
    return tier * 4 + 2


class DesmancharView(discord.ui.View):
    """Troca a captura recém-paga por sucata, à escolha do jogador.

    Existe porque a sucata só vinha de lixo e as varas boas evitam lixo: a
    Sniper .50 e a Devoradora têm 0% e não produziam nenhuma, para sempre.
    A troca é MANUAL e por lance — automatizá-la escolheria pelo jogador em
    todo lance de tier alto, que é justamente onde o Sachê vale mais.
    """

    def __init__(self, user_id: int, nome: str, tier: int, valor: int):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.nome = nome
        self.tier = tier
        self.valor = valor
        self.usado = False
        self.message = None
        self.desmanchar.label = f"Desmanchar (+{desmanche_yield(tier)} ⚙️)"

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Desmanchar", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def desmanchar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Essa captura não é sua.", ephemeral=True)
        # Guarda anti-replay, mesmo padrão de BlackjackView/MemoriaView: sem
        # ela, dois cliques rápidos estornariam o Sachê duas vezes e pagariam
        # sucata duas vezes pela mesma captura.
        if self.usado:
            return await interaction.response.send_message("❌ Esta captura já foi desmanchada.", ephemeral=True)
        self.usado = True

        conn = get_bot_instance().db_conn
        # Estorna o Sachê ANTES de creditar a sucata: se a segunda metade
        # falhar, o jogador fica sem os dois em vez de com os dois.
        modify_wallet(conn, self.user_id, -self.valor, interaction.user.name)
        sucata = grant_scrap(conn, self.user_id, desmanche_yield(self.tier))

        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"⚙️ **{self.nome}** desmanchado: −{self.valor} Sachês, +{sucata} sucata.",
            ephemeral=True,
        )
        self.stop()


async def _finalize_pescar(interaction: discord.Interaction, ctx: dict):
    """Persiste captura e envia embed final."""
    conn = get_bot_instance().db_conn
    user_id = ctx["user_id"]
    inv = ctx["inv"]
    valor = ctx["valor"]
    nome = ctx["nome"]
    emoji = ctx["emoji"]
    tier_p = ctx["tier_p"]
    frase = ctx["frase"]
    rod_data = ctx["rod_data"]
    actual_cd = ctx["actual_cd"]
    mission_msg = ctx["mission_msg"]
    mission_completed = ctx["mission_completed"]
    quest_trigger = ctx["quest_trigger"]
    new_xp_total = ctx["new_xp_total"]
    current_rank = ctx["current_rank"]
    used_bait = ctx["used_bait"]
    w_key = ctx["w_key"]
    w_stats = ctx["w_stats"]

    if used_bait and inv.get("isca", 0) <= 0:
        inv.pop("isca", None)

    # Aplica a pescaria como DELTA em cima do estado fresco do banco, em vez de
    # sobrescrever com o snapshot lido no início de pescar(). Entre a leitura do
    # snapshot e este ponto há awaits, e qualquer outro comando do mesmo usuário
    # (ex: /eco comprar) pode ter alterado saldo/inventário nesse meio —
    # sobrescrever com o snapshot reverteria essa mudança e duplicaria saldo.
    # modify_wallet/add_inventory_item já releem o valor atual dentro de sua
    # própria transação atômica, então não é preciso computar o diff aqui.
    novo_saldo = modify_wallet(conn, user_id, valor, interaction.user.name)

    inv_before = ctx["inv_before"]
    for key in set(inv_before) | set(inv):
        delta = inv.get(key, 0) - inv_before.get(key, 0)
        if delta != 0:
            add_inventory_item(conn, user_id, key, delta)
    fresh_inv = get_inventory(conn, user_id)

    # Rank/XP saem pelo helper v4 (BEGIN IMMEDIATE + sync), igual ao resto da
    # camada. fish_count continua sendo delta e user_name é idempotente, então
    # esses dois seguem no UPDATE direto.
    set_guild_rank(conn, user_id, current_rank, new_xp_total)
    conn.execute(
        """
        UPDATE users SET fish_count = fish_count + 1, user_name = ?
        WHERE user_id = ?
        """,
        (interaction.user.name, user_id),
    )
    sync_user_to_economy(conn, user_id)
    if valor > 0 and not ctx.get("is_trash"):
        log_fish_sale(conn, nome, valor, user_id)
    conn.commit()

    embed_color = discord.Color.from_rgb(46, 204, 113)
    if tier_p == 0:
        embed_color = discord.Color.light_grey()
    if tier_p == 2:
        embed_color = discord.Color.blue()
    if tier_p >= 3:
        embed_color = discord.Color.purple()

    embed = discord.Embed(title=f"{emoji} P3LUCHE Fishing OS", color=embed_color)
    embed.add_field(name="Capturado:", value=f"**{emoji} {nome}**", inline=False)
    embed.add_field(name="P3LUCHE diz:", value=f"*{frase}*", inline=False)
    cd_minutos = int(actual_cd / 60)
    stats_info = f"**{rod_data['name']}**\n(⏱️ {cd_minutos}m | 🎲 x{rod_data['luck']})"
    embed.add_field(name="Detalhes", value=stats_info, inline=True)
    # `valor` já é líquido; o bruto é reconstruído só para exibir a conta,
    # de modo que o jogador veja de onde saiu a diferença em vez de achar
    # que a vara cara está rendendo menos sem motivo.
    manutencao = ctx.get("manutencao", 0)
    if manutencao > 0:
        lucro_txt = (
            f"```diff\n+ {valor + manutencao} Sachês\n"
            f"- {manutencao} manutenção\n= {valor} líquido\n```"
        )
    else:
        lucro_txt = f"```diff\n+ {valor} Sachês\n```"
    embed.add_field(name="Lucro", value=lucro_txt, inline=True)
    iscas_restantes = fresh_inv.get("isca", 0)
    weather_icon = "☀️" if w_key == "normal" else ("⛈️" if w_key == "bad" else "✨")
    embed.set_footer(
        text=f"Saldo: {novo_saldo} | Iscas: {iscas_restantes} | Clima: {weather_icon} {w_stats['name']}"
    )
    if mission_msg:
        embed.description = (embed.description or "") + mission_msg
        if mission_completed:
            embed.color = discord.Color.gold()
    if quest_trigger:
        bottle_msg = "\n🧴 **Você encontrou uma Garrafa Incrustada!** Use /ler_garrafa para ver o conteúdo."
        embed.description = (embed.description or "") + bottle_msg

    # Todo tier usa o asset genérico do seu tier (ou o de lixo).
    asset_path = resolve_fishing_asset(nome, tier_p, ctx.get("is_trash", False))
    img_file, img_url = get_local_file(asset_path, os.path.basename(asset_path)) if asset_path else (None, None)
    if img_file:
        embed.set_thumbnail(url=img_url)

    # Desmanche: só faz sentido quando houve peixe pago. Lixo já vai para a
    # mochila e vira sucata no Galdino pelo caminho de sempre.
    view = None
    if valor > 0 and not ctx.get("is_trash"):
        view = DesmancharView(user_id, nome, tier_p, valor)

    if img_file:
        mensagem = await interaction.followup.send(embed=embed, file=img_file, view=view, wait=True)
    else:
        mensagem = await interaction.followup.send(embed=embed, view=view, wait=True)
    if view is not None:
        view.message = mensagem


@eco_group.command(name="pescar", description="Pesca usando itens da sua mochila.")
async def pescar(interaction: discord.Interaction):
    # Defer para evitar erro de tempo limite
    try:
        await interaction.response.defer()
    except (discord.NotFound, discord.HTTPException) as e:
        log_to_gui(f"interaction.defer() falhou: {e}", "WARNING")

    user_id = interaction.user.id
    conn = get_bot_instance().db_conn
    cursor = conn.cursor()

    # 1. BUSCA DADOS COMPLETOS (camada v4)
    # A checagem de conta nova usa a tabela legada `economy` (não `users`)
    # porque comandos ainda não migrados (saldo, rank...) só leem `economy`.
    # Fase 8: não interrompe mais o primeiro contato do jogador com um
    # "conta criada, tente de novo" — ensure_user() já deixa todas as
    # tabelas v4 (users/user_rods/rod_upgrades/user_cooldowns) com as linhas
    # e defaults corretos (wallet=0, vara_bambu, cooldown livre etc.), então
    # o fluxo cai direto na primeira pescaria de verdade, na mesma chamada.
    is_new_account = not has_account(conn, user_id)
    ensure_user(conn, user_id, interaction.user.name)
    if is_new_account:
        sync_user_to_economy(conn, user_id)

    row = cursor.execute("""
        SELECT u.wallet, u.fish_count, u.guild_rank, u.guild_xp, u.scrap,
               ur.current_rod, ru.luck_level, ru.cd_level, ru.forge_level,
               uc.last_fish,
               q.current_chapter
        FROM users u
        LEFT JOIN user_rods ur ON u.user_id = ur.user_id
        LEFT JOIN rod_upgrades ru ON u.user_id = ru.user_id
        LEFT JOIN user_cooldowns uc ON u.user_id = uc.user_id
        LEFT JOIN quest_progress q ON u.user_id = q.user_id
        WHERE u.user_id = ?
    """, (user_id,)).fetchone()

    # 2. CARREGA INVENTÁRIO E VARA
    inv = get_inventory(conn, user_id)
    # Snapshot do inventário no momento da leitura: usado para persistir a
    # pescaria como DELTA (ganho/perda), não como substituição total do
    # estado — evita apagar mudanças feitas por outros comandos do mesmo
    # usuário entre esta leitura e a gravação final.
    inv_before = dict(inv)

    current_rod_key = row['current_rod'] if row['current_rod'] else 'vara_bambu'
    if current_rod_key not in ROD_STATS: current_rod_key = 'vara_bambu'
    rod_data = ROD_STATS[current_rod_key]

    # 3. CARREGA UPGRADES (do Galdino) E BÔNUS DA ILHA
    upgrades = {"luck": row['luck_level'] or 0, "cd": row['cd_level'] or 0}
    # Ilha pessoal: Acampamento reduz cooldown (−2%/nível) e Farol soma sorte
    # (+10%). Somam com os upgrades do Galdino em vez de multiplicar — dois
    # sistemas multiplicativos empilhados escalam rápido demais no fim de jogo,
    # e a diferença é imperceptível na faixa em que a ilha é alcançável.
    ilha_bonus = get_island_bonuses(conn, user_id)

    luck_bonus = 1 + (upgrades.get("luck", 0) * 0.10) + ilha_bonus["sorte_bonus"]
    # Forja do Abismo: fator SEPARADO na cadeia, não somado ao luck_bonus.
    # Somar diluiria o efeito (o luck_bonus já é uma base 1 + acréscimos),
    # enquanto multiplicar mantém a promessa da mecânica: +1,5% por nível em
    # cima do que a vara e os upgrades já rendem, seja qual for esse valor.
    forge_level = row['forge_level'] or 0
    forge_mult = forge_luck_multiplier(forge_level)
    # Manutenção do equipamento: custo fixo por lance das varas de tier >= 4,
    # descontado do resultado logo abaixo (ver rod_maintenance_cost).
    manutencao = rod_maintenance_cost(current_rod_key)
    cd_reduction = 1 - (upgrades.get("cd", 0) * 0.05) - ilha_bonus["cd_reducao"]

    # 4. LÓGICA DE COOLDOWN
    base_cd = 300 # 5 minutos padrão
    actual_cd = int((base_cd * rod_data['cd']) * cd_reduction)

    agora = datetime.now()
    agora_str = agora.strftime("%Y-%m-%d %H:%M:%S.%f")

    if row['last_fish']:
        try:
            last_fish_time = datetime.strptime(row['last_fish'], "%Y-%m-%d %H:%M:%S.%f")
            diff = (agora - last_fish_time).total_seconds()
            if diff < actual_cd:
                wait_time = int(actual_cd - diff)
                ts = int((datetime.now() + timedelta(seconds=wait_time)).timestamp())
                return await interaction.followup.send(f"⏳ **{rod_data['name']}:** Descansando... Volte <t:{ts}:R>.", ephemeral=True)
        except ValueError: pass

    # Reserva o cooldown IMEDIATAMENTE após a checagem passar, ANTES de
    # qualquer await. Sem isso, uma segunda chamada de /eco pescar do mesmo
    # usuário, enquanto a primeira ainda está suspensa em algum await antes de
    # gravar, leria o last_fish antigo, passaria pela checagem acima e abriria
    # um segundo fluxo em paralelo — duplicando a captura dentro do intervalo
    # de um único cooldown. Não mova isto para depois do processamento.
    set_cooldown(conn, user_id, "last_fish", agora_str)

    # 5. CONSUMO DE ITENS
    used_bait = False; used_magnet = False; used_firewall = False; used_chip = False
    # O fallback que lia `economy.baits` saiu: essa coluna é derivada de
    # inv['isca'] por sync_user_to_economy, então ela nunca podia divergir
    # do inventário v4 — o ramo `elif legacy_baits` era inalcançável.

    # Consome isca
    if inv.get("isca", 0) > 0:
        inv["isca"] -= 1
        used_bait = True
    
    if inv.get("isca", 0) <= 0: inv.pop("isca", None)

    # Consome outros itens
    if inv.get("firewall", 0) > 0: inv["firewall"] -= 1; used_firewall = True
    if inv.get("firewall", 0) <= 0: inv.pop("firewall", None)
    
    if inv.get("chip_sorte", 0) > 0: inv["chip_sorte"] -= 1; used_chip = True
    if inv.get("chip_sorte", 0) <= 0: inv.pop("chip_sorte", None)

    used_brilhante = False
    used_fedorenta = False
    if inv.get("isca_brilhante", 0) > 0:
        inv["isca_brilhante"] -= 1
        used_brilhante = True
    if inv.get("isca_brilhante", 0) <= 0:
        inv.pop("isca_brilhante", None)

    if inv.get("isca_fedorenta", 0) > 0:
        inv["isca_fedorenta"] -= 1
        used_fedorenta = True
    if inv.get("isca_fedorenta", 0) <= 0:
        inv.pop("isca_fedorenta", None)

    if inv.get("isca_eletrica", 0) > 0:
        inv["isca_eletrica"] -= 1
        used_chip = True
    if inv.get("isca_eletrica", 0) <= 0:
        inv.pop("isca_eletrica", None)

    # ==========================================================
    # 6. PESCA (RNG + CLIMA ATUALIZADO)
    # ==========================================================
    
    # Pega o clima atual do banco
    w_key, w_stats = get_current_weather()
    
    # Modifica chance de lixo com base no clima
    trash_chance = rod_data['trash'] * w_stats['trash_mod']
    
    if used_bait: trash_chance /= 2
    if used_firewall: trash_chance = 0
    if used_fedorenta: trash_chance = max(0, trash_chance - 15)

    roll = random.randint(1, 100)
    
    pool = []
    if used_chip: 
        pool = [p for p in FISH_DB if p[4] >= 2]
        if not pool: pool = [p for p in FISH_DB if p[4] > 0]
    elif roll <= trash_chance: 
        # O tier 0 mistura lixo e peixe inicial, então sortear uniformemente
        # entre eles amarraria a taxa de lixo à quantidade de linhas de cada
        # tipo no FISH_DB. Separamos os dois e usamos TRASH_ROLL_RATIO, para
        # que adicionar peixe ou lixo novo na tabela não mexa no balanceamento.
        lixo_pool = [p for p in FISH_DB if p[4] == 0 and p[0] in TRASH_ITEMS]
        iniciais_pool = [p for p in FISH_DB if p[4] == 0 and p[0] not in TRASH_ITEMS]
        if lixo_pool and iniciais_pool:
            pool = lixo_pool if random.random() < TRASH_ROLL_RATIO else iniciais_pool
        else:
            pool = lixo_pool or iniciais_pool
        if not pool: pool = [("Bota Velha", 0, 5, "👢", 0, "Que nojo!")]
    else: 
        # Modifica o Tier Máximo com base no bônus do clima
        max_tier_possible = rod_data['tier'] + w_stats['tier_bonus']
        pool = [p for p in FISH_DB if p[4] <= max_tier_possible and p[4] > 0]
        if used_brilhante and random.random() < 0.2:
            tier2_pool = [p for p in pool if p[4] == 2]
            if tier2_pool:
                pool = tier2_pool
        # Fallback do tier 0: só peixe inicial, nunca lixo. Uma vara de tier 0
        # deixa o filtro acima vazio (`0 < tier <= 0`), e o fallback antigo
        # devolvia TODAS as 20 entradas de tier 0 — metade delas lixo. O
        # resultado é que este ramo, que é justamente o "não deu lixo", pagava
        # lixo em ~50% das vezes: a Vara de Bambu entregava 40% de lixo no
        # total, contra os 60% do stat dela, e ninguém conseguia ler de onde
        # vinha a diferença. O roll de lixo de verdade continua sendo o ramo
        # de cima, governado por TRASH_ROLL_RATIO.
        if not pool:
            pool = [p for p in FISH_DB if p[4] == 0 and p[0] not in TRASH_ITEMS]

    catch_data = random.choice(pool)
    nome, v_min, v_max, emoji, tier_p, frase = catch_data
    
    # Cálculo de Valor (Aplicando Clima)
    is_trash = nome in TRASH_ITEMS

    # Fórmula: ValorBase * SorteVara * (Upgrades+Ilha) * Forja * Clima
    base_val = int(
        random.randint(v_min, v_max)
        * rod_data['luck']
        * luck_bonus
        * forge_mult
        * w_stats['luck_mod']
    )
    if used_bait: base_val = int(base_val * 1.5)

    valor = 0
    manutencao_aplicada = 0
    if is_trash:
        inv[nome] = inv.get(nome, 0) + 1
    else:
        valor = base_val
        if inv.get("ima_saches", 0) > 0:
            valor *= 2
            inv["ima_saches"] -= 1
            used_magnet = True
        
        if inv.get("ima_saches", 0) <= 0:
            inv.pop("ima_saches", None)

        # Manutenção: desconto no resultado, DEPOIS de todos os multiplicadores
        # (sorte da vara, upgrades, ilha, forja, clima, Ímã). Descontar antes
        # faria o Ímã dobrar o desconto junto com o ganho, o que transformaria
        # um consumível de ganho num consumível de custo.
        #
        # Piso em zero de propósito: o enunciado é "descontado do resultado",
        # não uma cobrança à parte — um lance ruim rende zero, nunca dívida.
        # Pelo mesmo motivo o lance que traz LIXO não cobra nada: não houve
        # resultado do qual descontar.
        if manutencao > 0:
            manutencao_aplicada = min(valor, manutencao)
            valor -= manutencao_aplicada

    # 7. PROGRESSO DE MISSÃO EM GRUPO
    mission_msg = ""
    mission_completed = False
    
    all_parties = cursor.execute("SELECT leader_id, members_json, active_mission_id, mission_progress, mission_target FROM parties").fetchall()
    my_party = None
    
    for p in all_parties:
        is_leader = (p['leader_id'] == user_id)
        try: mems = json.loads(p['members_json'])
        except: mems = []
        is_member = (user_id in mems)
        if is_leader or is_member:
            my_party = p
            break
    
    if my_party and my_party['active_mission_id']:
        m_id = my_party['active_mission_id']
        progress = my_party['mission_progress']
        target = my_party['mission_target']
        
        m_data = None
        for r_list in MISSION_DB.values():
            for m in r_list:
                if m['id'] == m_id: m_data = m; break
            if m_data: break
        
        if m_data:
            inc = 0
            if m_data['type'] == 'fish_count': inc = 1
            elif m_data['type'] == 'earn_money': inc = valor
            elif m_data['type'] == 'fish_specific':
                alvos = m_data['target_fish']
                if isinstance(alvos, list):
                    if nome in alvos: inc = 1
                else:
                    if nome == alvos: inc = 1
            
            if inc > 0:
                new_prog = progress + inc
                cursor.execute("UPDATE parties SET mission_progress = ? WHERE leader_id = ?", (new_prog, my_party['leader_id']))
                mission_msg = f"\n📈 **Missão de Grupo:** {new_prog}/{target} (+{inc})"
                
                if new_prog >= target:
                    mission_completed = True
                    leader_id = my_party['leader_id']
                    # Reserva a conclusão de hoje ANTES de pagar. Sem isso a
                    # missão era infinitamente repetível: o bloco abaixo zera
                    # active_mission_id e nada registrava que ela já tinha sido
                    # feita, então bastava reaceitá-la no quadro.
                    reserva = try_register_mission_completion(conn, leader_id, m_id)
                    cursor.execute(
                        "UPDATE parties SET active_mission_id = NULL, mission_progress = 0 WHERE leader_id = ?",
                        (leader_id,),
                    )

                    if not reserva["success"]:
                        mission_msg = _mission_blocked_msg(reserva, m_data['title'])
                    else:
                        reward_money = m_data['reward']
                        reward_xp = m_data['xp']

                        members_ids = json.loads(my_party['members_json'])
                        members_ids.append(leader_id)
                        unique_members = set(members_ids)
                        # Divide a recompensa igualmente; resto (se reward_money
                        # não for divisível) vai para o líder, não é descartado.
                        base_share = reward_money // len(unique_members)
                        remainder = reward_money % len(unique_members)

                        for member_id in unique_members:
                            share = base_share + remainder if member_id == leader_id else base_share
                            modify_wallet(conn, member_id, share)
                            # add_guild_xp relê o XP dentro da própria transação.
                            # O UPDATE cru + sync_user_to_economy que havia aqui só
                            # não propagava dado obsoleto porque o modify_wallet
                            # acima chama ensure_user por acaso — dependência frágil
                            # da linha anterior.
                            add_guild_xp(conn, member_id, reward_xp)

                        mission_msg = (
                            f"\n🎉 **MISSÃO CUMPRIDA!**\nGrupo completou: **{m_data['title']}**\n"
                            f"Prêmio: 💰 {reward_money} | ⭐ {reward_xp} XP!"
                            f"\n📋 *Restam {reserva['restantes']} de {MISSION_DAILY_CAP} missões hoje.*"
                        )

    # 8. XP DE GUILDA E RANK UP
    xp_ganho = 0
    if row['current_chapter'] in ['acesso_liberado', 'city_spotted', 'garrafa_encontrada']:
        xp_table = {0: 2, 1: 10, 2: 25, 3: 100, 4: 500}
        xp_ganho = xp_table.get(tier_p, 2)

    # Rank/XP vêm do estado FRESCO, não do snapshot lido no início de
    # pescar(): a recompensa de missão de grupo logo acima já incrementou
    # guild_xp deste mesmo jogador, e uma promoção da Jenna pode ter entrado
    # na janela de await do lance. Somar sobre `row` e gravar o absoluto em
    # _finalize_pescar descartava as duas coisas.
    _guild_atual = get_guild_rank(conn, user_id)
    new_xp_total = _guild_atual['xp'] + xp_ganho
    
    current_rank = _guild_atual['rank']
    proximo_rank, custo_promocao = next_rank_requirement(current_rank)

    if proximo_rank and new_xp_total >= custo_promocao:
        if proximo_rank == 'S':
             pass
        else:
            current_rank = proximo_rank
            new_xp_total -= custo_promocao
            mission_msg += f"\n🌟 **RANK UP!** Agora você é Rank {current_rank}!"

    # 9. QUEST DA GARRAFA
    quest_trigger = False
    prev_fish_count = row['fish_count'] if row['fish_count'] else 0
    new_fish_count = prev_fish_count + 1
    already_has = inv.get('garrafa_incrustada', 0) > 0

    if not already_has:
        async with CATCHES_LOCK:
            _cleanup_stale_catches()
            previous_count, _ = CATCHES_SINCE_RESTART.get(user_id, (0, 0.0))
            session_count = previous_count + 1
            CATCHES_SINCE_RESTART[user_id] = (session_count, time.time())

        try:
            cursor.execute("""
                INSERT INTO persistent_catches(user_id, catch_count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET catch_count = persistent_catches.catch_count + 1, updated_at = excluded.updated_at
            """, (user_id, agora_str))
            get_bot_instance().db_conn.commit()
        except sqlite3.Error: pass

        if session_count == 2: quest_trigger = True
        elif (new_fish_count % 5) == 0: quest_trigger = True
        elif random.randint(1, 4) == 1: quest_trigger = True

        if quest_trigger:
            inv['garrafa_incrustada'] = 1
            # INSERT ... ON CONFLICT (mesmo padrão de /ler_garrafa em
            # cogs/sistema.py) em vez dos dois ramos UPDATE/INSERT OR IGNORE
            # que havia aqui. ensure_user() materializa as linhas v4 do
            # jogador (users/user_rods/rod_upgrades/user_cooldowns) mas NÃO a
            # de quest_progress, então numa conta nova o UPDATE não achava
            # linha nenhuma e voltava afetando 0 linhas em silêncio: o
            # capítulo continuava NULL e o portão de XP de guilda (o `if
            # row['current_chapter'] in [...]` do passo 8) ficava fechado
            # para sempre, por mais que o jogador pescasse. O ramo do else
            # tinha o problema espelhado — INSERT OR IGNORE é no-op quando a
            # linha já existe, que é exatamente o caso em que ele rodava.
            #
            # O WHERE do DO UPDATE preserva a única regra que os dois ramos
            # antigos codificavam junto: gravar 'garrafa_encontrada' só por
            # cima de capítulo ausente ou 'inicio', nunca regredindo quem já
            # está em city_spotted/acesso_liberado.
            cursor.execute(
                """
                INSERT INTO quest_progress (user_id, current_chapter)
                VALUES (?, 'garrafa_encontrada')
                ON CONFLICT(user_id) DO UPDATE SET current_chapter = 'garrafa_encontrada'
                WHERE quest_progress.current_chapter IS NULL
                   OR quest_progress.current_chapter = 'inicio'
                """,
                (user_id,),
            )
            # Commit explícito, igual ao bloco de persistent_catches logo
            # acima: sem ele a gravação fica pendente e só é fechada pelo
            # commit() interno do primeiro ensure_user() de _finalize_pescar
            # — dependência silenciosa da ordem de chamadas de outro módulo.
            conn.commit()


    # 10. SALVA E ENTREGA O RESULTADO
    catch_ctx = {
        "user_id": user_id,
        "inv": inv,
        "inv_before": inv_before,
        "valor": valor,
        "nome": nome,
        "emoji": emoji,
        "tier_p": tier_p,
        "frase": frase,
        "rod_data": rod_data,
        "actual_cd": actual_cd,
        "mission_msg": mission_msg,
        "mission_completed": mission_completed,
        "quest_trigger": quest_trigger,
        "new_xp_total": new_xp_total,
        "current_rank": current_rank,
        "used_bait": used_bait,
        "manutencao": manutencao_aplicada,
        "agora_str": agora_str,
        "w_key": w_key,
        "w_stats": w_stats,
        "is_trash": is_trash,
    }

    await _finalize_pescar(interaction, catch_ctx)

# --- VIEW DE ESCOLHA DE EXPLORAÇÃO (ILHA vs CIDADE) ---
class ExplorationView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.choice = None

    @discord.ui.button(label="Farmar na Ilha", style=discord.ButtonStyle.secondary, emoji="🌴")
    async def island(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        self.choice = "farm"
        await interaction.response.defer() # Apenas fecha o wait()
        self.stop()

    @discord.ui.button(label="Ir para a Cidade", style=discord.ButtonStyle.primary, emoji="🏙️")
    async def city(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        self.choice = "city"
        await interaction.response.defer()
        self.stop()

class TavernView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = user_id

    @discord.ui.button(label="Ouvir Fofocas", style=discord.ButtonStyle.secondary, emoji="🍺")
    async def gossip(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Pega uma fofoca aleatória do banco de diálogos
        rumor = get_dialogue("tavern", "rumors")
        await interaction.response.send_message(f"🍺 **Taberneiro:** '{rumor}'", ephemeral=True)

    @discord.ui.button(label="Falar com Valerius (Loja)", style=discord.ButtonStyle.success, emoji="💰")
    async def valerius(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🎒 Empório do Valerius", description=get_dialogue("valerius", "intro"), color=discord.Color.gold())
        
        # GIF do Valerie/Valerius
        file, url = get_local_file("assets/npcs/valerie.gif", "valerie.gif")
        if file: embed.set_thumbnail(url=url)

        # Cria a loja (Selector)
        view = discord.ui.View()
        
        view.add_item(ValeriusShopSelect(self.user_id))
        
        if file: await interaction.response.send_message(embed=embed, file=file, view=view, ephemeral=True)
        else: await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# --- VIEW DO HUB DA CIDADE (PORTO SOLARE) ---
class CityHubView(discord.ui.View):
    def __init__(self, user_id, user_name):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.user_name = user_name

    @discord.ui.button(label="Entrar na Guilda", style=discord.ButtonStyle.primary, emoji="🏢")
    async def guild_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🏛️ Recepção da Guilda", description="O salão é vasto e cheio de mapas. A Capitã Jenna observa do mezanino.", color=discord.Color.dark_blue())
        
        # Imagem da Guilda
        file, url = get_local_file("assets/locais/guilda.jpg", "guilda.jpg")
        if file: embed.set_image(url=url)
        
        # Abre o menu da Guilda
        if file: await interaction.response.send_message(embed=embed, file=file, view=GuildView(self.user_id, self.user_name), ephemeral=True)
        else: await interaction.response.send_message(embed=embed, view=GuildView(self.user_id, self.user_name), ephemeral=True)

    @discord.ui.button(label="Oficina do Galdino", style=discord.ButtonStyle.secondary, emoji="🔧")
    async def galdino_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🔧 Oficina de Sucata", description=get_dialogue("galdino", "intro"), color=discord.Color.orange())
        
        files = []
        # Fundo: Ferreiro
        file_bg, url_bg = get_local_file("assets/locais/ferreiro.jpg", "ferreiro.jpg")
        if file_bg: 
            embed.set_image(url=url_bg)
            files.append(file_bg)

        # NPC: Galdino
        file_npc, url_npc = get_local_file("assets/npcs/galdino.gif", "galdino.gif")
        if file_npc: 
            embed.set_thumbnail(url=url_npc)
            files.append(file_npc)

        if files: await interaction.response.send_message(embed=embed, files=files, view=GaldinoView(self.user_id, self.user_name), ephemeral=True)
        else: await interaction.response.send_message(embed=embed, view=GaldinoView(self.user_id, self.user_name), ephemeral=True)

    @discord.ui.button(label="Taverna (Fofocas)", style=discord.ButtonStyle.secondary, emoji="🍺")
    async def tavern_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🍺 Taverna 'O Anzol Torto'", description="O cheiro de rum barato e madeira velha preenche o ar.", color=discord.Color.dark_gold())
        
        # Imagem da Taverna
        file, url = get_local_file("assets/locais/taverna.jpg", "taverna.jpg")
        if file: embed.set_image(url=url)

        # Agora chamamos a TavernView (que vamos criar abaixo)
        if file: await interaction.response.send_message(embed=embed, file=file, view=TavernView(self.user_id), ephemeral=True)
        else: await interaction.response.send_message(embed=embed, view=TavernView(self.user_id), ephemeral=True)

#(--- COMANDO DE EXPLORAÇÃO --- Atualizado para incluir lógica de missão de guilda)
@eco_group.command(name="explorar", description="Envia o drone para a Ilha, Cidade ou Mar.")
async def explorar(interaction: discord.Interaction):
    user_id = interaction.user.id
    conn = get_bot_instance().db_conn
    cursor = conn.cursor()

    # 1. VERIFICAÇÕES BÁSICAS
    if not has_account(conn, user_id):
        return await interaction.response.send_message("❌ Crie uma conta pescando primeiro.", ephemeral=True)
    ensure_user(conn, user_id, interaction.user.name)

    quest = cursor.execute("SELECT current_chapter, inventory FROM quest_progress WHERE user_id = ?", (user_id,)).fetchone()
    city_spotted = quest and quest['current_chapter'] not in ['inicio', 'locked', None]

    # 2. CUSTO E COOLDOWN — checados e RESERVADOS antes de qualquer await
    # (inclusive antes de mostrar a ExplorationView, que fica aberta até
    # 60s esperando o jogador escolher o destino). Igual ao fix já aplicado
    # em /eco pescar: sem reservar aqui, uma 2ª chamada durante a janela do
    # view.wait() leria o cooldown/saldo antigos e passaria pela checagem
    # em paralelo com a 1ª.
    custo = 80
    agora = datetime.now()
    agora_str = agora.strftime("%Y-%m-%d %H:%M:%S.%f")

    last_explore = get_cooldowns(conn, user_id)["last_explore"]
    if last_explore:
        try:
            last_exp = datetime.strptime(last_explore, "%Y-%m-%d %H:%M:%S.%f")
            if (agora - last_exp).total_seconds() < 600:
                ts = int((last_exp + timedelta(minutes=10)).timestamp())
                return await interaction.response.send_message(f"⏳ **Drone Recarregando!** <t:{ts}:R>.", ephemeral=True)
        except ValueError: pass

    if not try_spend_wallet(conn, user_id, custo, interaction.user.name):
        return await interaction.response.send_message(f"🔋 Precisa de {custo} Sachês para operar o drone.", ephemeral=True)
    set_cooldown(conn, user_id, "last_explore", agora_str)

    # 3. DECISÃO (VIEW DE ESCOLHA) — só depois de custo+cooldown já reservados.
    modo_exploracao = "farm"

    # Se já viu a cidade, pergunta pra onde quer ir
    if city_spotted:
        view = ExplorationView(user_id)
        await interaction.response.send_message("📡 **Painel de Controle do Drone:** Escolha o destino.", view=view, ephemeral=True)
        await view.wait()
        if view.choice is None:
            # Custo/cooldown já foram reservados antes da view abrir (pro
            # fix da race acima) — sem escolha, o drone "se perde" em vez de
            # reembolsar (cooldown gasto mesmo se o jogador não reage a tempo).
            await interaction.followup.send(
                "📡 O drone perdeu o sinal sem ordens claras e voltou vazio. (Custo e cooldown consumidos.)",
                ephemeral=True,
            )
            return
        modo_exploracao = view.choice
    else:
        # Se não viu a cidade, vai direto farmar na ilha
        await interaction.response.defer()

    # --- LÓGICA DE MISSÃO DE GUILDA (EXPLORE COUNT) ---
    mission_msg = ""
    # (Lógica da missão mantida igual para economizar espaço visual, ela estava correta)
    all_parties = cursor.execute("SELECT leader_id, members_json, active_mission_id, mission_progress, mission_target FROM parties").fetchall()
    my_party = None
    for p in all_parties:
        if p['leader_id'] == user_id or user_id in json.loads(p['members_json']):
            my_party = p; break
            
    if my_party and my_party['active_mission_id']:
        m_id = my_party['active_mission_id']
        m_data = None
        for r_list in MISSION_DB.values():
            for m in r_list:
                if m['id'] == m_id: m_data = m; break
            if m_data: break
        
        if m_data and m_data['type'] == 'explore_count':
            new_prog = my_party['mission_progress'] + 1
            cursor.execute("UPDATE parties SET mission_progress = ? WHERE leader_id = ?", (new_prog, my_party['leader_id']))
            mission_msg = f"\n🚁 **Missão:** {new_prog}/{my_party['mission_target']}"
            if new_prog >= my_party['mission_target']:
                leader_id = my_party['leader_id']
                # Mesma trava do caminho de pescar. As duas rotas de conclusão
                # são independentes e tinham o mesmo furo; a reserva é única
                # por (grupo, missão, dia), então elas não se atropelam.
                reserva = try_register_mission_completion(conn, leader_id, m_id)
                cursor.execute(
                    "UPDATE parties SET active_mission_id=NULL, mission_progress=0 WHERE leader_id=?",
                    (leader_id,),
                )

                if not reserva["success"]:
                    mission_msg = _mission_blocked_msg(reserva, m_data['title'])
                else:
                    rw, rx = m_data['reward'], m_data['xp']
                    mems = json.loads(my_party['members_json']) + [leader_id]
                    unique_mems = set(mems)
                    # Divide a recompensa igualmente; resto (se rw não for
                    # divisível) vai para o líder, não é descartado.
                    base_share = rw // len(unique_mems)
                    remainder = rw % len(unique_mems)
                    for mid in unique_mems:
                        share = base_share + remainder if mid == leader_id else base_share
                        modify_wallet(conn, mid, share)
                        # Mesmo motivo do caminho de pescar: releitura dentro da
                        # transação em vez de depender do ensure_user do
                        # modify_wallet acima.
                        add_guild_xp(conn, mid, rx)
                    mission_msg = (
                        f"\n🎉 **Missão Completa!** Ganharam {rw} Sachês!"
                        f"\n📋 *Restam {reserva['restantes']} de {MISSION_DAILY_CAP} missões hoje.*"
                    )

    # =========================================================================
    # ROTA 1: CIDADE (PORTO SOLARE) - AQUI ENTRA A IMAGEM DO PORTO
    # =========================================================================
    if modo_exploracao == "city":
        tem_selo = False
        if quest and quest['inventory']:
            try:
                q_inv = json.loads(quest['inventory'])
                if q_inv.get('selo_capitao'): tem_selo = True
            except: pass
            
        if tem_selo:
            # SUCESSO
            if quest['current_chapter'] != 'acesso_liberado':
                cursor.execute("UPDATE quest_progress SET current_chapter = 'acesso_liberado' WHERE user_id = ?", (user_id,))
                get_bot_instance().db_conn.commit()

            embed = discord.Embed(title=WORLD_LORE['city']['title'], description=WORLD_LORE['city']['description'], color=discord.Color.dark_magenta())
            
            # --- CARREGA IMAGEM DO PORTO ---
            file, url = get_local_file("assets/locais/porto solare.jpg", "porto.jpg")
            if file: embed.set_image(url=url)
            # -------------------------------

            if file: await interaction.followup.send(embed=embed, file=file, view=CityHubView(user_id, interaction.user.name))
            else: await interaction.followup.send(embed=embed, view=CityHubView(user_id, interaction.user.name))
                
        else:
            # ACESSO NEGADO
            embed = discord.Embed(title="🚫 ACESSO NEGADO", description="Os guardas exigem o **Selo do Capitão**.\nVolte quando tiver autorização.", color=discord.Color.red())
            embed.set_footer(text="Dica: Pesque a Garrafa na ilha e use /ler_garrafa.")
            set_cooldown(conn, user_id, "last_explore", None)  # Reembolsa só o cooldown (não o custo, igual antes)
            await interaction.followup.send(embed=embed)
        return

    # =========================================================================
    # ROTA 2: FARMAR NA ILHA - AQUI ENTRA A IMAGEM DA ILHA
    # =========================================================================
    
    # Lista de Cenários (RNG)
    cenarios = [
        ("💥 Falha Crítica", "O drone bateu num Firewall e explodiu.", 0, None),
        ("🐈 Gato de Rua", "Um gato de verdade roubou seu drone. Perdeu tudo.", 0, None),
        ("💾 Lixeira do Windows", "Você revirou arquivos deletados e achou uns trocados.", 80, None),
        ("🏦 Carteira de Crypto", "Você hackeou uma carteira abandonada! Stonks!", 300, None),
        ("💎 Mina de Dados", "Encontrou um servidor não protegido. Jackpot!", 500, None),
        ("📦 Amazon Drone", "Você interceptou uma entrega! Caiu uma caixa.", 0, "caixa_misteriosa"),
        ("⚡ Energético Perdido", "Achou uma latinha no servidor.", 0, "energetico")
    ]
    weights = [10, 10, 30, 20, 10, 5, 5]
    titulo, historia, valor_ganho, item_ganho = random.choices(cenarios, weights=weights, k=1)[0]
    
    msg = historia
    cor = discord.Color.red()
    
    # Custo e cooldown já foram cobrados/reservados antes da view — as
    # branches abaixo só aplicam o resultado do sorteio (dinheiro/item).
    if valor_ganho > 0:
        modify_wallet(conn, user_id, valor_ganho)
        msg += f"\n\n💰 **Resultado:** +{valor_ganho} Sachês (Lucro: {valor_ganho - custo})"
        cor = discord.Color.green()

    elif item_ganho:
        if item_ganho == "caixa_misteriosa":
             add_inventory_item(conn, user_id, "caixa_misteriosa", 1)
             msg += f"\n\n📦 **Loot Raro!** Você achou uma Caixa Misteriosa."
             cor = discord.Color.gold()
        elif item_ganho == "energetico":
             set_cooldown(conn, user_id, "last_fish", None)
             msg += f"\n\n⚡ **Energia Pura!** Seu cooldown de PESCA foi resetado."
             cor = discord.Color.blue()

    else:
        msg += f"\n\n💸 **Prejuízo:** -{custo} Sachês."

    # --- DESCOBERTA DA CIDADE ---
    if not city_spotted and random.randint(1, 100) <= 10: 
        cursor.execute("INSERT OR REPLACE INTO quest_progress (user_id, current_chapter) VALUES (?, 'city_spotted')", (user_id,))
        
        embed_discovery = discord.Embed(title="📡 SINAL INTERCEPTADO", color=discord.Color.magenta())
        embed_discovery.description = (
            "Durante a exploração, a câmera do drone captou algo estranho no horizonte...\n"
            "Além da neblina digital, existem luzes, torres e fumaça.\n\n"
            "📍 **Nova Localização Descoberta:** Sinais de Neon.\n"
            "*Use `/eco explorar` novamente para tentar viajar até lá.*"
        )
        await interaction.followup.send(embed=embed_discovery)

    # --- EMBED FINAL DO FARM (COM IMAGEM DA ILHA) ---
    embed = discord.Embed(title=f"🗺️ {titulo}", description=msg + mission_msg, color=cor)
    embed.set_footer(text="Cooldown do Drone: 10 minutos.")

    # CARREGA A IMAGEM DA ILHA AQUI
    file, url = get_local_file("assets/locais/ilha player.jpg", "ilha.jpg")
    if file: embed.set_image(url=url)
    
    if file: await interaction.followup.send(embed=embed, file=file)
    else: await interaction.followup.send(embed=embed)

    get_bot_instance().db_conn.commit()

# A vara inicial não é comprável nem fica guardada no inventário: ela é
# implícita (default de user_rods.current_rod e sempre injetada em
# `owned_rods` no /eco saldo). Não existe flag nem tier exclusivo marcando-a
# — o tier 0 é compartilhado com a vara_treino —, então a identificação é
# pela chave literal mesmo. Presenteá-la deixaria o remetente sem como pescar
# caso uma cópia tenha ido parar no inventário dele por algum fluxo legado.
INITIAL_ROD_KEY = "vara_bambu"
GIFT_BLOCKED_KEYS = frozenset({INITIAL_ROD_KEY})


def _item_display_name(key: str) -> str:
    """Nome que o jogador vê na mochila para uma chave de inventário."""
    return SHOP_ITEMS.get(key, {}).get("name", key)


def _resolve_gift_key(raw: str, inv: dict) -> str | None:
    """Traduz o que o jogador digitou para a chave interna do item.

    Aceita a chave interna ('caixa_misteriosa'), o nome de exibição
    ('Caixa Misteriosa' — o que o /eco saldo mostra, e por isso o que o
    jogador tenta digitar) e chaves livres de inventário que não existem na
    loja (peixes, lixo). Sem isso o comando só aceitava a chave interna e
    respondia "Item inválido" para o nome exibido na própria mochila.
    """
    if raw in SHOP_ITEMS:
        return raw
    alvo = raw.strip().casefold()
    for key, data in SHOP_ITEMS.items():
        if data["name"].casefold() == alvo:
            return key
    for key in inv:
        if key.casefold() == alvo:
            return key
    return None


def _owned_inventory_key(inv: dict, key: str) -> str | None:
    """Sob qual chave o remetente realmente guarda esse item, se guarda.

    O keyspace do inventário está partido em produção: /eco comprar sempre
    gravou pela chave interna, enquanto o presentear gravava itens flex pelo
    nome de exibição. Cópias antigas existem sob as duas grafias, então a
    remoção precisa achar a que o jogador tem de fato — a entrega, essa, é
    sempre pela chave interna (ver mais abaixo), o que vai convergindo o
    keyspace conforme os itens circulam.
    """
    if inv.get(key, 0) > 0:
        return key
    display = _item_display_name(key)
    if display != key and inv.get(display, 0) > 0:
        return display
    return None


async def _presentear_autocomplete(interaction: discord.Interaction, current: str):
    """Sugere só o que o remetente tem na mochila — presentear exige posse.

    A lista não inclui itens da loja: sugerir algo que o jogador não possui
    levaria direto a uma recusa, já que o comando não compra nada.
    """
    try:
        conn = get_bot_instance().db_conn
        inv = get_inventory(conn, interaction.user.id)
    except Exception:
        inv = {}

    termo = (current or "").strip().casefold()
    vistos = set()
    escolhas = []

    for key, qtd in sorted(inv.items(), key=lambda kv: _item_display_name(kv[0]).casefold()):
        if qtd <= 0 or key in GIFT_BLOCKED_KEYS:
            continue
        interno = _resolve_gift_key(key, inv) or key
        if interno in vistos or interno in GIFT_BLOCKED_KEYS:
            continue
        rotulo = f"{_item_display_name(interno)} (x{qtd})"
        if not termo or termo in rotulo.casefold() or termo in interno.casefold():
            vistos.add(interno)
            escolhas.append(app_commands.Choice(name=rotulo[:100], value=interno))

    return escolhas[:25]


@eco_group.command(name="presentear", description="Dê um item a um amigo.")
@app_commands.describe(item="Um item que você já tem na mochila (a Vara de Bambu não pode ser enviada).")
@app_commands.autocomplete(item=_presentear_autocomplete)
async def presentear(interaction: discord.Interaction, amigo: discord.Member, item: str):
    ID_CRIADOR = 299323165937500160
    ID_DONO = 541680099477422110

    if amigo.id == interaction.user.id: return await interaction.response.send_message("🎁 Use /eco comprar.", ephemeral=True)

    conn = get_bot_instance().db_conn
    sender_id = interaction.user.id
    sender_inv = get_inventory(conn, sender_id)

    chave = _resolve_gift_key(item, sender_inv)
    if chave is None:
        return await interaction.response.send_message("❌ Item inválido.", ephemeral=True)

    if chave in GIFT_BLOCKED_KEYS:
        return await interaction.response.send_message(
            "🎣 A **Vara de Bambu** é o equipamento inicial e não pode ser presenteada — sem ela ninguém consegue começar a pescar.",
            ephemeral=True,
        )

    # --- CHECAGEM DE ITENS ESPECIAIS/SECRETOS (mesma regra de /eco comprar) ---
    if chave == "item_criador" and sender_id != ID_CRIADOR:
        return await interaction.response.send_message("⛔ Acesso Negado.", ephemeral=True)
    if chave == "item_dono" and sender_id != ID_DONO:
        return await interaction.response.send_message("🔥 Pesado demais para você.", ephemeral=True)

    data = SHOP_ITEMS.get(chave, {})
    nome_item = _item_display_name(chave)

    # Presentear é SEMPRE transferência de uma cópia que o remetente já tem —
    # nunca uma compra disfarçada. Sem posse não há presente, e a carteira não
    # é tocada em nenhum caminho (nem para cobrar, nem para recusar por saldo).
    owned_key = _owned_inventory_key(sender_inv, chave)
    if owned_key is None:
        return await interaction.response.send_message("🚫 Você não possui esse item.", ephemeral=True)

    ensure_user(conn, amigo.id, amigo.name)
    sync_user_to_economy(conn, amigo.id)

    add_inventory_item(conn, sender_id, owned_key, -1)

    # Entrega SEMPRE pela chave interna — inclusive itens flex, que antes eram
    # gravados por data['name'] e acabavam criando uma segunda grafia do mesmo
    # item no inventário do destinatário.
    add_inventory_item(conn, amigo.id, chave, 1)

    # Vara não tem tratamento especial: entra na mochila como qualquer outro
    # item e o destinatário equipa quando quiser pelo menu do /eco saldo (o
    # `owned_rods` é montado a partir do inventário). Nada de set_current_rod()
    # aqui — trocar a vara equipada de outra pessoa sem ela pedir tirava dela
    # a escolha, e podia até rebaixar o equipamento em uso.
    if data.get('type') == 'flex':
        msg = f"💎 **Luxo:** {nome_item} entregue!"
    else:
        msg = f"🎁 **Presente:** {nome_item} entregue!"

    await interaction.response.send_message(
        f"🎁 **Enviado para {amigo.name}!**\n{msg}\n🎒 Saiu da sua mochila."
    )

VENDER_LOTES = {
    "tudo": "Tudo (todo o peixe da mochila)",
    "tier0": "Tier 0 — peixes iniciais",
    "tier1": "Tier 1",
    "tier2": "Tier 2",
    "tier3": "Tier 3",
    "tier4": "Tier 4 — míticos",
}


def _vender_selecao(inv: dict, o_que: str) -> tuple[list, str | None]:
    """Resolve o argumento em [(espécie, quantidade)] + rótulo do lote.

    Devolve ([], None) quando o argumento não corresponde a nada vendável, e
    quem chama decide a mensagem — aqui não se sabe se o caso é "mochila
    vazia" ou "termo inválido".
    """
    alvo = (o_que or "tudo").strip().casefold()

    def possui(nome):
        return inv.get(nome, 0) > 0

    if alvo == "tudo":
        itens = [(n, inv[n]) for n in FISH_BY_NAME if possui(n)]
        return itens, "toda a mochila"

    if alvo.startswith("tier") and alvo[4:].isdigit():
        tier = int(alvo[4:])
        itens = [(n, inv[n]) for n, (t, _, _) in FISH_BY_NAME.items() if t == tier and possui(n)]
        return itens, f"tier {tier}"

    for nome in FISH_BY_NAME:
        if nome.casefold() == alvo:
            return ([(nome, inv[nome])] if possui(nome) else []), nome

    return [], None


async def _vender_autocomplete(interaction: discord.Interaction, current: str):
    """Sugere os lotes que rendem algo AGORA, com o total já calculado.

    Mostrar um tier que o jogador não tem leva a uma recusa depois de ele já
    ter escolhido; mostrar o valor evita a ida e volta de "quanto vale?".
    """
    try:
        inv = get_inventory(get_bot_instance().db_conn, interaction.user.id)
    except Exception:
        inv = {}

    termo = (current or "").strip().casefold()
    escolhas = []

    for chave, descricao in VENDER_LOTES.items():
        itens, _ = _vender_selecao(inv, chave)
        total = sum(fish_sell_price(n) * q for n, q in itens)
        if not itens:
            continue
        rotulo = f"{descricao} — {total} Sachês"
        if not termo or termo in chave or termo in descricao.casefold():
            escolhas.append(app_commands.Choice(name=rotulo[:100], value=chave))

    for nome, qtd in sorted(inv.items()):
        if qtd <= 0 or nome not in FISH_BY_NAME:
            continue
        rotulo = f"{nome} (x{qtd}) — {fish_sell_price(nome) * qtd} Sachês"
        if not termo or termo in nome.casefold():
            escolhas.append(app_commands.Choice(name=rotulo[:100], value=nome))

    # O lixo aparece na lista mesmo não sendo vendável: é a única pista de
    # que ele tem OUTRO destino. Sem isso o jogador tenta vender, leva
    # "termo inválido" e conclui que o lixo não serve para nada.
    lixo = sum(inv.get(t, 0) for t in TRASH_ITEMS)
    if lixo and (not termo or termo in "lixo"):
        escolhas.append(app_commands.Choice(name=f"Lixo (x{lixo}) — recicle no Galdino", value="lixo"))

    return escolhas[:25]


@eco_group.command(name="vender", description="Vende o peixe da mochila em lote.")
@app_commands.describe(o_que="tudo, tier0..tier4, ou o nome de uma espécie")
@app_commands.autocomplete(o_que=_vender_autocomplete)
async def vender(interaction: discord.Interaction, o_que: str = "tudo"):
    user_id = interaction.user.id
    conn = get_bot_instance().db_conn

    if not has_account(conn, user_id):
        return await interaction.response.send_message("❌ Use /eco pescar primeiro.", ephemeral=True)
    ensure_user(conn, user_id, interaction.user.name)

    if (o_que or "").strip().casefold() == "lixo":
        return await interaction.response.send_message(
            "♻️ Lixo não se vende aqui — leve para o **Galdino** (`/eco explorar` → Cidade → Oficina) "
            "e troque por sucata.",
            ephemeral=True,
        )

    inv = get_inventory(conn, user_id)
    itens, rotulo = _vender_selecao(inv, o_que)

    if rotulo is None:
        return await interaction.response.send_message(
            f"❌ Não reconheço `{o_que}`. Use `tudo`, `tier0`–`tier4` ou o nome de um peixe.",
            ephemeral=True,
        )
    if not itens:
        return await interaction.response.send_message(
            f"🎒 Você não tem peixe de **{rotulo}** para vender.", ephemeral=True
        )

    # Debita a mochila ANTES de creditar a carteira: se a entrega falhar no
    # meio, o jogador perde o peixe sem o dinheiro — o inverso deixaria o
    # peixe na mochila com o Sachê já pago, que é o dupe.
    total = 0
    sucata_bruta = 0
    vendidos = []
    for nome, qtd in itens:
        preco = fish_sell_price(nome)
        if preco <= 0 or qtd <= 0:
            continue
        add_inventory_item(conn, user_id, nome, -qtd)
        total += preco * qtd
        sucata_bruta += fish_scrap_yield(nome) * qtd
        vendidos.append((nome, qtd, preco * qtd))

    if not vendidos:
        return await interaction.response.send_message("🎒 Nada vendável nessa seleção.", ephemeral=True)

    novo_saldo = modify_wallet(conn, user_id, total, interaction.user.name)
    sucata = grant_scrap(conn, user_id, sucata_bruta)

    vendidos.sort(key=lambda v: -v[2])
    linhas = [f"• **{n}** x{q} — {v} Sachês" for n, q, v in vendidos[:12]]
    if len(vendidos) > 12:
        linhas.append(f"• *…e mais {len(vendidos) - 12} espécies*")

    embed = discord.Embed(
        title="🐟 Peixaria",
        description=f"Vendido: **{rotulo}**\n\n" + "\n".join(linhas),
        color=discord.Color.teal(),
    )
    embed.add_field(name="Total", value=f"```diff\n+ {total} Sachês\n```", inline=True)
    embed.add_field(name="Peças", value=str(sum(q for _, q, _ in vendidos)), inline=True)
    if sucata:
        embed.add_field(name="Sucata", value=f"⚙️ +{sucata}", inline=True)
    embed.set_footer(text=f"Saldo: {novo_saldo}")
    await interaction.response.send_message(embed=embed)


# --- CLASSES DE INTERFACE DO INVENTÁRIO (DROPDOWN) ---

class RodSelect(discord.ui.Select):
    def __init__(self, user_id, owned_rods, current_rod_key):
        self.user_id = user_id
        
        options = []
        # Gera a lista de varas que o jogador tem
        for rod_key in owned_rods:
            if rod_key not in ROD_STATS: continue
            stats = ROD_STATS[rod_key]
            
            # Marca visualmente qual está equipada
            is_equipped = (rod_key == current_rod_key)
            emoji = "✅" if is_equipped else "🎣"
            label = stats['name']
            if is_equipped: label += " (Atual)"
            
            # Mostra stats rápidos na descrição
            desc = f"CD: {int(stats['cd']*5)}m | Sorte: x{stats['luck']}"
            
            options.append(discord.SelectOption(
                label=label, 
                value=rod_key, 
                description=desc, 
                emoji=emoji,
                default=is_equipped
            ))

        super().__init__(
            placeholder="🎣 Clique para equipar outra vara...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        # Segurança: Só o dono do inventário mexe
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("⛔ Esse inventário não é seu.", ephemeral=True)

        new_rod = self.values[0]
        rod_name = ROD_STATS[new_rod]['name']
        conn = get_bot_instance().db_conn

        # Garante que a vara equipada esteja presente no inventário (não deve
        # 'sumir'). Lê/escreve pela camada v4 (user_inventory) — escrever só
        # na tabela legada `economy` aqui não bastava: ensure_user() só
        # sincroniza user_rods/user_inventory da legada pra v4 na CRIAÇÃO da
        # conta (INSERT OR IGNORE), não a cada chamada. Era por isso que
        # trocar de vara "funcionava" (mensagem de sucesso) mas /eco pescar
        # continuava usando a vara antiga, lida da tabela v4 que nunca era
        # atualizada.
        if new_rod != 'vara_bambu':
            inv = get_inventory(conn, self.user_id)
            if inv.get(new_rod, 0) <= 0:
                add_inventory_item(conn, self.user_id, new_rod, 1)

        set_current_rod(conn, self.user_id, new_rod)

        await interaction.response.send_message(f"✅ **Pronto!** Você equipou a **{rod_name}**.", ephemeral=True)

# --- VIEW DE ESCOLHA DE EXPLORAÇÃO ---
class ExplorationView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.choice = None

    @discord.ui.button(label="Rondar a Ilha (Farm)", style=discord.ButtonStyle.secondary, emoji="🌴")
    async def farm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        self.choice = "farm"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Investigar Luzes (???)", style=discord.ButtonStyle.primary, emoji="🏙️")
    async def city_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        self.choice = "city"
        await interaction.response.defer()
        self.stop()


# Explicação mostrada quando o player seleciona uma isca no menu de consumo.
# Iscas não têm ação manual: o efeito é aplicado dentro de /eco pescar.
BAIT_USAGE_HINTS = {
    "isca": "🪱 **Isca Minhoca:** Mantenha na mochila. Ela é usada automaticamente quando você pesca — corta o lixo pela metade e aumenta o valor da captura.",
    "isca_brilhante": "✨ **Isca Brilhante:** Não precisa usar aqui! Deixe na mochila e use `/eco pescar` — ela é gasta sozinha e dá 20% de chance de vir um peixe de Tier 2.",
    "isca_fedorenta": "🦠 **Isca Fedorenta:** Não precisa usar aqui! Deixe na mochila e use `/eco pescar` — ela é gasta sozinha e reduz bastante a chance de vir lixo.",
    "isca_eletrica": "⚡ **Isca Elétrica:** Não precisa usar aqui! Deixe na mochila e use `/eco pescar` — ela é gasta sozinha e garante um peixe de Tier 2 ou maior.",
}


class ConsumeSelect(discord.ui.Select):
    # CORREÇÃO AQUI: Adicionei 'user_id' de volta nos parênteses para não dar erro
    def __init__(self, user_id, items_dict):
        options = []
        for key, qtd in items_dict.items():
            # Pega dados do item ou cria genérico se não achar
            data = SHOP_ITEMS.get(key, {'name': key, 'type': 'unknown', 'emoji': '❓'})
            
            # Filtra: Só mostra consumíveis e buffs no menu de usar
            if data.get('type') in ['consumable', 'buff', 'box']:
                options.append(discord.SelectOption(
                    label=f"{data['name']} (x{qtd})", 
                    value=key,
                    description="Clique para usar/ativar.",
                    emoji="🧪"
                ))

        # Se não tiver nada usável
        if not options:
            options.append(discord.SelectOption(label="Nada usável na mochila", value="empty", default=True))
            
        super().__init__(placeholder="🧪 Usar / Consumir Item...", min_values=1, max_values=1, options=options, disabled=not options)

    async def callback(self, interaction: discord.Interaction):
        item_key = self.values[0]
        
        if item_key == "empty":
            return await interaction.response.send_message("❌ Nada para usar.", ephemeral=True)

        # Pegamos o ID de quem clicou (Mais seguro que usar o salvo no init)
        user_id = interaction.user.id
        conn = get_bot_instance().db_conn

        # Lê da camada v4 (user_inventory), não da tabela legada `economy`.
        # Ler/gravar a legada aqui era o bug do "item usado que volta": a
        # remoção ficava só no JSON legado, e a v4 — que nunca era
        # decrementada — sobrescrevia esse JSON via sync_user_to_economy() no
        # comando seguinte, ressuscitando o item. Como sync_user_from_economy()
        # só SOMA (nunca apaga uma chave que sumiu do JSON), uma remoção
        # gravada apenas na legada não tem como chegar na v4.
        inv = get_inventory(conn, user_id)

        if inv.get(item_key, 0) <= 0:
            return await interaction.response.send_message("❌ Você não tem mais este item.", ephemeral=True)

        msg = ""
        item_data = SHOP_ITEMS.get(item_key, {})

        # Itens passivos (buffs e iscas) NÃO são gastos aqui — quem consome é
        # /eco pescar. Só os ramos abaixo chamam add_inventory_item().

        # --- LÓGICA DE USO DOS ITENS ---

        # 1. ENERGÉTICO (Reseta Cooldown)
        if item_key == "energetico":
            # Consome ANTES de aplicar o efeito: se o efeito falhar, o jogador
            # perde o item sem o bônus — o inverso (efeito antes do consumo)
            # devolveria o bônus com o item intacto, que é justamente o dupe.
            add_inventory_item(conn, user_id, item_key, -1)
            # Corrigido: escrevia em 'last_fish_time', uma coluna órfã nunca
            # lida pela checagem real de cooldown (que usa 'last_fish') —
            # o item não fazia NADA. Mesmo padrão de reset já usado
            # corretamente no evento "Energético Perdido" do drone.
            set_cooldown(conn, user_id, "last_fish", None)
            msg = "⚡ **Energético bebido!** Você está pilhado! O tempo de espera da pesca foi zerado."

        # 2. CAIXA MISTERIOSA (Sorteio)
        elif item_key == "caixa_misteriosa":
            add_inventory_item(conn, user_id, item_key, -1)
            resultado = abrir_caixa_misteriosa()
            if resultado["tipo"] == "item":
                add_inventory_item(conn, user_id, resultado["item"], 1)
                nome_item = SHOP_ITEMS.get(resultado["item"], {}).get("name", resultado["item"])
                msg = f"🎁 **Caixa Aberta!** Dentro havia: 🧪 **{nome_item}**."
            else:
                modify_wallet(conn, user_id, resultado["valor"], interaction.user.name)
                if resultado["tipo"] == "jackpot":
                    msg = (
                        f"🎰 **JACKPOT!** A caixa estava forrada de Sachês: "
                        f"💰 **{resultado['valor']}**!"
                    )
                else:
                    msg = f"🎁 **Caixa Aberta!** Você encontrou 💰 **{resultado['valor']} Sachês** dentro dela."

        # 3. REDE DE MÃO (Pesca 3 itens aleatórios instantâneos)
        elif item_key == "rede":
            add_inventory_item(conn, user_id, item_key, -1)
            # Sorteia 3 recompensas simples (dinheiro) para simular pesca
            lucro_rede = 0
            for _ in range(3):
                val = random.randint(10, 50)
                lucro_rede += val

            modify_wallet(conn, user_id, lucro_rede, interaction.user.name)
            msg = f"🕸️ **Rede lançada!** Você puxou um monte de tralha e peixes pequenos, lucrando 💰 **{lucro_rede} Sachês**."

        # 4. BUFFS NOVOS (Ímã, Firewall, Chip)
        elif item_key in ["ima_saches", "firewall", "chip_sorte"]:
            msg = f"ℹ️ **{item_data['name']}**: Este item é passivo! Mantenha ele no inventário e ele será usado automaticamente na próxima pescaria."

        # 5. ISCAS (Avisar que é automático)
        # Todas as iscas entram aqui, não só a comum: elas aparecem no dropdown
        # por serem type 'consumable', mas quem gasta e aplica o efeito é
        # /eco pescar. Sem cobrir as especiais elas caíam no else abaixo e o
        # player levava "não pode ser usado através deste menu" — parecia item
        # quebrado, e ninguém descobria que bastava pescar.
        elif item_key in BAIT_USAGE_HINTS:
            msg = BAIT_USAGE_HINTS[item_key]

        # 6. ELSE (Segurança)
        else:
            msg = f"❓ O item **{item_data.get('name', item_key)}** não pode ser usado através deste menu."

        # add_inventory_item()/modify_wallet() já commitam dentro da própria
        # transação atômica — não há nada pendente para gravar aqui.

        # SEGURANÇA FINAL
        if not msg:
            msg = "✅ Ação processada."

        await interaction.response.send_message(msg, ephemeral=True)

# Atualiza a View Principal para ter OS DOIS MENUS
class InventoryView(discord.ui.View):
    def __init__(self, user_id, owned_rods, current_rod_key, inventory):
        super().__init__(timeout=180)
        self.add_item(RodSelect(user_id, owned_rods, current_rod_key))
        self.add_item(ConsumeSelect(user_id, inventory))

@eco_group.command(name="saldo", description="Veja sua carteira, inventário e equipe varas.")
async def saldo(interaction: discord.Interaction, usuario: discord.Member = None):
    target = usuario or interaction.user
    conn = get_bot_instance().db_conn

    if not has_account(conn, target.id):
        return await interaction.response.send_message("❌ Usuário sem conta bancária. Use /eco pescar primeiro!", ephemeral=True)

    # Tudo da v4. `economy.baits` era derivada de inv['isca'], então a
    # contagem de iscas sai do próprio inventário.
    inv = get_inventory(conn, target.id)
    wallet = get_wallet(conn, target.id)
    fish_count = get_fish_count(conn, target.id)
    rod_key = get_current_rod(conn, target.id)
    baits = inv.get("isca", 0)

    # --- 1. PROCESSA INVENTÁRIO (VISUAL) ---
    inv_text = "Mochila vazia."
    try:
        if inv:
            rarity_map = {'common': '⚪', 'uncommon': '🟢', 'rare': '🔵', 'epic': '🟣', 'legendary': '🟠', 'mythic': '✨'}
            item_list = []
            # Mapeia nomes de itens para chaves (retrocompatibilidade com nomes antigos)
            name_to_key = {
                "Teclado do Arquiteto": "item_criador",
                "Coroa do Imperador": "item_dono"
            }
            for k, v in inv.items():
                # Se a chave é um nome antigo, mapeia para a chave correta
                actual_key = name_to_key.get(k, k)
                # Pega dados do item ou usa genérico se não achar
                item_data = SHOP_ITEMS.get(actual_key, {'name': k, 'rarity': 'common'})
                icon = rarity_map.get(item_data.get('rarity', 'common'), '⚪')
                rarity_label = ' [MÍTICO]' if item_data.get('rarity') == 'mythic' else ''
                item_list.append(f"{icon} **{item_data['name']}**{rarity_label} (x{v})")
            inv_text = "\n".join(item_list)
    except: inv_text = "Erro de leitura."

    # --- 2. PROCESSA VARA ATUAL ---
    if rod_key not in ROD_STATS: rod_key = "vara_bambu"
    rod_data = ROD_STATS[rod_key]

    # --- 3. MONTA O EMBED ---
    embed = discord.Embed(color=discord.Color.from_rgb(47, 49, 54))
    embed.set_author(name=f"Inventário de {target.name}", icon_url="https://cdn-icons-png.flaticon.com/512/3081/3081840.png")
    
    if target.avatar: embed.set_thumbnail(url=target.avatar.url)
    
    embed.add_field(name="💳 Finanças", value=f"💰 **{wallet}** Sachês\n🐟 **{fish_count}** Peixes", inline=False)
    
    stats_str = f"⏱️ CD: {int(rod_data['cd']*5)}m | 🎲 Sorte: x{rod_data['luck']}"
    embed.add_field(name="🎣 Equipado", value=f"**{rod_data['name']}**\n*{stats_str}*\n🪱 **{baits}** Iscas", inline=False)
    
    embed.add_field(name="🎒 Mochila", value=inv_text, inline=False)
    
    # --- 4. LÓGICA DO MENU DE EQUIPAR ---
    view = None
    # O menu só aparece se você estiver olhando seu próprio saldo
    if target.id == interaction.user.id:
        # Mesmo inventário v4 já lido acima.
        inv_data = inv
        
        # Cria lista de varas possuídas (Bambu é padrão + Varas compradas)
        owned_rods = ["vara_bambu"] 
        for k in inv_data.keys():
            # Se o item do inventário estiver na lista de varas ROD_STATS, adiciona na lista
            if k in ROD_STATS and k != "vara_bambu":
                owned_rods.append(k)
        
        # Ordena por Tier para ficar organizado (Bambu -> Ouro -> Iridium)
        owned_rods.sort(key=lambda k: ROD_STATS[k]['tier'])
        
        # Só cria o menu se tiver mais de uma vara (ou se quiser reequipar a de bambu)
        view = InventoryView(interaction.user.id, owned_rods, rod_key, inv_data)
        embed.set_footer(text="Use os menus abaixo para Equipar Varas ou Usar Itens!")
    else:
        embed.set_footer(text="Raridade: ⚪Comum 🟢Incomum 🔵Raro 🟣Épico 🟠Lendário ✨Mítico")

    # Envia a mensagem (view só é incluído se não for None)
    if view:
        await interaction.response.send_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed)

@eco_group.command(name="diario", description="Resgate diário com bônus de streak.")
async def diario(interaction: discord.Interaction):
    user_id = interaction.user.id
    conn = get_bot_instance().db_conn
    ensure_v4_tables(conn)
    # NÃO chamar sync_user_from_economy aqui: reimportar a legada a cada
    # /eco diario ressuscitava na v4 o que já tinha sido gasto/removido
    # (mesma raiz que foi tirada do ensure_user). A legada é cópia derivada.
    ensure_user(conn, user_id, interaction.user.name)

    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT last_daily, daily_streak FROM user_cooldowns WHERE user_id = ?", (user_id,)
    ).fetchone()
    agora = datetime.now()
    streak = 1

    if row and row["last_daily"]:
        last = None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                last = datetime.strptime(row["last_daily"], fmt)
                break
            except ValueError:
                continue
        if last is None:
            try:
                last = datetime.fromisoformat(row["last_daily"])
            except ValueError:
                last = None
        if last:
            diff_days = (agora.date() - last.date()).days
            if diff_days == 0:
                ts = int((last + timedelta(days=1)).timestamp())
                return await interaction.response.send_message(f"📅 Volte <t:{ts}:R>.", ephemeral=True)
            if diff_days == 1:
                streak = (row["daily_streak"] or 0) + 1

    base_reward = random.randint(100, 300)
    # Teto no bônus: sem isso, uma streak de 365 dias daria +18.250 (90x a
    # recompensa-base média de ~200) — inflação de longo prazo sem limite.
    # Streak em si (exibida ao jogador) continua sem teto, só o bônus
    # monetário é capado em 60 dias (+3.000 no máximo).
    STREAK_BONUS_CAP_DAYS = 60
    bonus = min(streak, STREAK_BONUS_CAP_DAYS) * 50
    total = base_reward + bonus
    agora_str = agora.strftime("%Y-%m-%d %H:%M:%S.%f")

    cursor.execute("UPDATE users SET wallet = wallet + ? WHERE user_id = ?", (total, user_id))
    cursor.execute(
        "UPDATE user_cooldowns SET last_daily = ?, daily_streak = ? WHERE user_id = ?",
        (agora_str, streak, user_id),
    )
    sync_user_to_economy(conn, user_id)
    conn.commit()

    # Bancada do Náufrago (ilha): 1 Isca Minhoca por dia. Entregue aqui em vez
    # de num resgate próprio porque /eco diario já É o portão diário do jogo —
    # um segundo cooldown para a mesma cadência seria estado duplicado, e o
    # jogador teria que lembrar de dois comandos para a mesma rotina.
    #
    # A quantidade vem do nível da Bancada (1 por nível), não de um literal:
    # a partir da Fase 3 a estrutura tem 5 níveis e o número precisa
    # acompanhar, senão evoluí-la não entrega nada aqui.
    extra_isca = ""
    iscas_da_ilha = get_island_bonuses(conn, user_id)["isca_diaria"]
    if iscas_da_ilha:
        add_inventory_item(conn, user_id, "isca", iscas_da_ilha)
        plural = "s" if iscas_da_ilha > 1 else ""
        extra_isca = f"\n🪱 *Bancada do Náufrago: +{iscas_da_ilha} Isca Minhoca{plural}.*"

    await interaction.response.send_message(
        f"📅 **Diário dia {streak}!** Recebeu **{total}** Sachês (bônus de streak: +{bonus}).{extra_isca}"
    )

@eco_group.command(name="rank", description="Hall da Fama.")
async def rank(interaction: discord.Interaction):
    conn = get_bot_instance().db_conn
    rows_m = get_top_players(conn, "wallet", 10)
    rows_f = get_top_players(conn, "fish_count", 10)

    def fmt(rows, type_v):
        txt = ""
        for i, r in enumerate(rows):
            v = r['wallet'] if type_v == 'm' else r['fish_count']
            txt += f"{'🥇🥈🥉'[i] if i<3 else f'**{i+1}.**'} **{r['user_name']}**: {v}\n"
        return txt or "Ninguém."

    embed = discord.Embed(title="🏆 Hall da Fama", color=discord.Color.gold())
    embed.add_field(name="💰 Magnatas", value=fmt(rows_m, 'm'), inline=True)
    embed.add_field(name="🎣 Pescadores", value=fmt(rows_f, 'f'), inline=True)
    await interaction.response.send_message(embed=embed)

 
# --- VIEW DE SELEÇÃO DE MISSÃO (ROTATIVO) ---
class MissionSelect(discord.ui.Select):
    def __init__(self, user_id, user_rank, feitas_hoje=None, vagas=None):
        self.user_id = user_id
        # Missões que este grupo já concluiu hoje e quantas ainda cabem no
        # teto diário. Vêm de fora porque quem monta a view já leu o banco —
        # reler aqui dentro seria uma segunda consulta pelo mesmo dado.
        self.feitas_hoje = feitas_hoje or set()
        self.vagas = MISSION_DAILY_CAP if vagas is None else vagas

        # 1. PEGA A DATA DE HOJE COMO SEMENTE
        today_seed = datetime.now().strftime("%Y%m%d") # Ex: "20251230"
        random.seed(today_seed) # Trava o aleatório na data de hoje

        # 2. SELECIONA 3 MISSÕES DO RANK DO JOGADOR
        # Se não tiver missões para o rank, pega do Rank F como fallback
        available_missions = MISSION_DB.get(user_rank, MISSION_DB["F"])

        # Garante que não quebra se tiver poucas missões na lista
        count = min(3, len(available_missions))
        daily_missions = random.sample(available_missions, count)

        random.seed() # Destrava o aleatório para o resto do bot

        # 3. CRIA AS OPÇÕES DO MENU
        options = []
        for m in daily_missions:
            # Já concluída hoje some do menu: aceitá-la de novo levaria o
            # jogador a cumprir o objetivo inteiro para receber uma recusa
            # no fim.
            if m['id'] in self.feitas_hoje:
                continue

            emoji_type = "🎣"
            if m['type'] == 'earn_money': emoji_type = "💰"
            if m['type'] == 'explore_count': emoji_type = "🚁"

            label = f"{m['title']} (+{m['xp']} XP)"
            desc = f"{m['desc']} | Prêmio: {m['reward']} Sachês"

            # Limita tamanho da descrição para não dar erro no Discord (max 100 chars)
            if len(desc) > 100: desc = desc[:97] + "..."

            options.append(discord.SelectOption(
                label=label,
                value=m['id'],
                description=desc,
                emoji=emoji_type
            ))

        # O Discord recusa um Select sem opções, então o estado "nada
        # disponível" precisa de uma opção-placeholder desabilitada.
        esgotado = self.vagas <= 0
        if esgotado or not options:
            texto = (
                "Teto diário atingido — volte amanhã"
                if esgotado
                else "Todas as missões de hoje já foram concluídas"
            )
            options = [discord.SelectOption(label=texto[:100], value="__indisponivel__", default=True)]

        super().__init__(
            placeholder=(
                "📜 Escolha a Missão Ativa de hoje..."
                if not esgotado
                else "🚫 Sem missões disponíveis hoje"
            ),
            min_values=1,
            max_values=1,
            options=options,
            disabled=esgotado or options[0].value == "__indisponivel__",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values and self.values[0] == "__indisponivel__":
            return await interaction.response.send_message(
                "📋 Nenhuma missão disponível para este grupo hoje.", ephemeral=True
            )

        conn = get_bot_instance().db_conn
        # Relê o teto na hora do clique: a view fica aberta e outro membro do
        # grupo pode ter fechado uma missão nesse meio-tempo.
        if mission_slots_left(conn, self.user_id) <= 0:
            return await interaction.response.send_message(
                f"🚫 Seu grupo já concluiu {MISSION_DAILY_CAP} missões hoje. Volte amanhã.",
                ephemeral=True,
            )
        if self.values and self.values[0] in missions_completed_today(conn, self.user_id):
            return await interaction.response.send_message(
                "🔁 Esta missão já foi concluída hoje pelo seu grupo.", ephemeral=True
            )

        # Verifica se é o líder
        cursor = get_bot_instance().db_conn.cursor()
        party = cursor.execute("SELECT leader_id, members_json FROM parties WHERE leader_id = ?", (self.user_id,)).fetchone()
        
        if not party:
            # Cria party solo se não existir
            cursor.execute("INSERT OR IGNORE INTO parties (leader_id, leader_name, members_json) VALUES (?, ?, '[]')", (self.user_id, interaction.user.name))
            get_bot_instance().db_conn.commit()
            party = {'leader_id': self.user_id}

        # Pega a missão escolhida
        mission_id = self.values[0]
        mission_data = None
        
        # Busca os dados da missão no DB
        for rank_key, missions in MISSION_DB.items():
            for m in missions:
                if m['id'] == mission_id:
                    mission_data = m
                    break
            if mission_data: break
            
        if not mission_data:
            return await interaction.response.send_message("❌ Erro ao carregar missão.", ephemeral=True)

        # SALVA A MISSÃO NA TABELA PARTIES
        cursor.execute("""
            UPDATE parties 
            SET active_mission_id = ?, mission_target = ?, mission_progress = 0 
            WHERE leader_id = ?
        """, (mission_id, mission_data['target'], self.user_id))
        
        get_bot_instance().db_conn.commit()
        
        embed = discord.Embed(title=f"📜 Missão Aceita: {mission_data['title']}", color=discord.Color.green())
        embed.description = f"**Objetivo:** {mission_data['desc']}\n\nAgora vá pescar/explorar para completar!\nO progresso é compartilhado com seu Grupo."
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class PartyKickSelect(discord.ui.Select):
    def __init__(self, leader_id, member_ids):
        self.leader_id = leader_id
        
        # Busca os nomes dos membros no banco para o menu ficar bonito
        # Traz apenas quem NÃO é o líder (não pode se auto-expulsar)
        nomes = get_user_names(
            get_bot_instance().db_conn,
            [m for m in member_ids if m != leader_id],
        )

        options = []
        for uid, nome in nomes.items():
            options.append(discord.SelectOption(
                label=nome,
                value=str(uid),
                description=f"ID: {uid}",
                emoji="👢"
            ))
            
        if not options:
            options.append(discord.SelectOption(label="Ninguém para expulsar", value="none", default=True))

        super().__init__(placeholder="Expulsar membro...", min_values=1, max_values=1, options=options, disabled=not options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.leader_id: return
        if self.values[0] == "none": return

        kick_id = int(self.values[0])
        cursor = get_bot_instance().db_conn.cursor()
        
        # Atualiza a lista de membros no banco
        party = cursor.execute("SELECT members_json FROM parties WHERE leader_id = ?", (self.leader_id,)).fetchone()
        if party:
            members = json.loads(party['members_json'])
            if kick_id in members:
                members.remove(kick_id)
                cursor.execute("UPDATE parties SET members_json = ? WHERE leader_id = ?", (json.dumps(members), self.leader_id))
                get_bot_instance().db_conn.commit()
                
                await interaction.response.send_message(f"👢 **Membro Expulso!** O jogador <@{kick_id}> foi removido do grupo.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Esse jogador já saiu.", ephemeral=True)

class PartyControlsView(discord.ui.View):
    def __init__(self, user_id, is_leader, party_row):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.party_row = party_row
        
        # --- SE FOR LÍDER ---
        if is_leader:
            # 1. Dropdown de Expulsar (Só aparece se tiver membros)
            members = json.loads(party_row['members_json'])
            if len(members) > 0:
                self.add_item(PartyKickSelect(user_id, members))
            
            # 2. Botão de Convidar
            # (Usaremos o UserSelect que já existe no seu código)
            self.add_item(PartyMemberSelect(user_id))

    # Botão de Ação Principal (Desfazer ou Sair)
    @discord.ui.button(label="Desfazer/Sair", style=discord.ButtonStyle.danger, emoji="🚪", row=2)
    async def leave_or_disband(self, interaction: discord.Interaction, button: discord.ui.Button):
        cursor = get_bot_instance().db_conn.cursor()
        
        # Lógica de LÍDER (Desfazer Grupo)
        if interaction.user.id == self.party_row['leader_id']:
            cursor.execute("DELETE FROM parties WHERE leader_id = ?", (self.user_id,))
            get_bot_instance().db_conn.commit()
            msg = "💥 **Grupo Desfeito!** Você encerrou as atividades do esquadrão."
            
        # Lógica de MEMBRO (Sair do Grupo)
        else:
            leader_id = self.party_row['leader_id']
            members = json.loads(self.party_row['members_json'])
            
            if self.user_id in members:
                members.remove(self.user_id)
                cursor.execute("UPDATE parties SET members_json = ? WHERE leader_id = ?", (json.dumps(members), leader_id))
                get_bot_instance().db_conn.commit()
                msg = "🏃 **Você saiu do grupo.** Agora está livre para seguir carreira solo."
            else:
                msg = "❌ Você já não estava no grupo."

        # Retorna para a Guilda
        embed_hub = discord.Embed(title="🏛️ Guilda de Porto Solare", description=msg, color=discord.Color.dark_blue())
        file, url = get_local_file("assets/locais/guilda.jpg", "guilda.jpg")
        if file:
            embed_hub.set_image(url=url)
            await interaction.response.edit_message(embed=embed_hub, attachments=[file], view=GuildView(self.user_id, interaction.user.name))
        else:
            await interaction.response.edit_message(embed=embed_hub, view=GuildView(self.user_id, interaction.user.name))

    # Botão Voltar
    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, row=2)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed_hub = discord.Embed(title="🏛️ Guilda de Porto Solare", description="Bem-vindo ao quartel general.", color=discord.Color.dark_blue())
        file, url = get_local_file("assets/locais/guilda.jpg", "guilda.jpg")
        if file:
            embed_hub.set_image(url=url)
            await interaction.response.edit_message(embed=embed_hub, attachments=[file], view=GuildView(self.user_id, interaction.user.name))
        else:
            await interaction.response.edit_message(embed=embed_hub, view=GuildView(self.user_id, interaction.user.name))

# --- VIEW DO PAINEL DA GUILDA (MENU PRINCIPAL) ---
class GuildView(discord.ui.View):
    def __init__(self, user_id, user_name):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.user_name = user_name

    # --- BOTÃO 1: FALAR COM A CAPITÃ (INTERAÇÃO NPC) ---
    @discord.ui.button(label="Falar com a Capitã", style=discord.ButtonStyle.primary, emoji="🛡️", row=0)
    async def talk_jenna(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Mostra um menu para o usuário escolher o tópico da conversa com a Capitã
        cursor = get_bot_instance().db_conn.cursor()
        _guilda = get_guild_rank(get_bot_instance().db_conn, self.user_id)
        rank = _guilda['rank']
        xp = _guilda['xp']

        # Checa automaticamente se o jogador tem o selo e registra acesso (não remove item)
        try:
            qrow = cursor.execute("SELECT inventory, current_chapter FROM quest_progress WHERE user_id = ?", (self.user_id,)).fetchone()
            has_seal = False
            if qrow and qrow['inventory']:
                try:
                    q_inv = json.loads(qrow['inventory'])
                    if q_inv.get('selo_capitao'):
                        has_seal = True
                except:
                    has_seal = False

            if not has_seal:
                # Mochila da v4 (o selo pode ter vindo da loja/presente, que
                # gravam em user_inventory, não no JSON legado).
                if get_inventory(get_bot_instance().db_conn, self.user_id).get('selo_capitao'):
                    has_seal = True

            # qrow é um sqlite3.Row, que não tem .get() — o AttributeError
            # caía no `except Exception: pass` lá embaixo e o registro do
            # selo nunca acontecia para quem já tinha linha em quest_progress.
            if has_seal and (not qrow or qrow['current_chapter'] != 'acesso_liberado'):
                cursor.execute("INSERT INTO quest_progress (user_id, inventory, current_chapter) VALUES (?, '{\"selo_capitao\": 1}', 'acesso_liberado') ON CONFLICT(user_id) DO UPDATE SET inventory = '{\"selo_capitao\": 1}', current_chapter = 'acesso_liberado'", (self.user_id,))
                get_bot_instance().db_conn.commit()
        except Exception:
            pass

        # Local classes: seletor de tópicos e view
        class JennaSelect(discord.ui.Select):
            def __init__(self, user_id, rank, xp):
                options = [
                    discord.SelectOption(label="Introdução", value="intro", description="Uma breve saudação da Capitã.", emoji="👋"),
                    discord.SelectOption(label="Sobre o Líder", value="about_leader", description="Pergunte sobre a Capitã e sua história.", emoji="📖"),
                    discord.SelectOption(label="Info de Rank", value="rank_info", description="Pergunte sobre seu rank e progresso.", emoji="🎖️"),
                    discord.SelectOption(label="Pedir Promoção", value="ask_promo", description="Solicitar promoção se tiver XP suficiente.", emoji="⚔️")
                ]
                super().__init__(placeholder="Selecione o tópico...", min_values=1, max_values=1, options=options)
                self.user_id = user_id
                self.rank = rank
                self.xp = xp

            async def callback(self, interaction: discord.Interaction):
                if interaction.user.id != self.user_id:
                    return await interaction.response.send_message("⛔ Essa seleção não é sua.", ephemeral=True)

                choice = self.values[0]

                if choice == 'intro':
                    text = get_dialogue('jenna', 'intro')
                    embed = discord.Embed(title="🛡️ Capitã Jenna", description=text, color=discord.Color.gold())
                    await interaction.response.edit_message(embed=embed, view=self.view)
                    return

                if choice == 'about_leader':
                    text = get_dialogue('jenna', 'about_leader')
                    embed = discord.Embed(title="🛡️ Sobre a Capitã", description=text, color=discord.Color.gold())
                    await interaction.response.edit_message(embed=embed, view=self.view)
                    return

                if choice == 'rank_info':
                    curr_rank = self.rank
                    xp_val = self.xp
                    rdata = GUILD_RANKS.get(curr_rank, GUILD_RANKS['F'])
                    # Mesma fonte de verdade da promoção logo abaixo e da barra
                    # de progresso do cartão: a meta exibida é o req_xp do rank
                    # SEGUINTE. Antes mostrava o do rank atual, ou seja, uma
                    # meta que o jogador já tinha batido só por estar nele.
                    next_key, req = next_rank_requirement(curr_rank)
                    if next_key:
                        embed = discord.Embed(title="📜 Informação de Rank", color=discord.Color.blue())
                        # 'name', não 'title': GUILD_RANKS não tem chave 'title'
                        # e este acesso levantava KeyError, derrubando o tópico
                        # inteiro no except genérico do menu.
                        embed.add_field(name="Rank Atual", value=f"**{curr_rank}** - {rdata['name']}", inline=False)
                        embed.add_field(name="XP", value=f"{xp_val}/{req}", inline=False)
                        await interaction.response.edit_message(embed=embed, view=self.view)
                    else:
                        embed = discord.Embed(title="📜 Informação de Rank", description="Você já atingiu o rank máximo.", color=discord.Color.blue())
                        await interaction.response.edit_message(embed=embed, view=self.view)
                    return

                if choice == 'ask_promo':
                    # Recarrega os dados da v4 (users): a legada economy é
                    # derivada e podia estar desatualizada na hora de decidir
                    # a promoção.
                    gr = get_guild_rank(get_bot_instance().db_conn, self.user_id)
                    curr_rank = gr['rank']
                    xp_val = gr['xp']
                    next_key, custo = next_rank_requirement(curr_rank)
                    if not next_key:
                        await interaction.response.edit_message(embed=discord.Embed(description="⚠️ Você já está no Rank máximo."), view=self.view)
                        return

                    if xp_val >= custo:
                        if next_key == 'S':
                            text = get_dialogue('jenna', 'rank_s_lock')
                            await interaction.response.edit_message(embed=discord.Embed(title="🛡️ Capitã Jenna", description=text, color=discord.Color.red()), view=self.view)
                            return
                        new_rank = next_key
                        new_xp = xp_val - custo
                        # Grava na v4: UPDATE só em economy era revertido pelo
                        # sync_user_to_economy do comando seguinte.
                        set_guild_rank(get_bot_instance().db_conn, self.user_id, new_rank, new_xp)
                        await interaction.response.edit_message(embed=discord.Embed(description=f"🛡️ **Promoção Concedida!** Agora você é **Rank {new_rank}**."), view=self.view)
                        return
                    else:
                        await interaction.response.edit_message(embed=discord.Embed(description="⏳ Você não tem XP suficiente para promoção."), view=self.view)
                        return

        class JennaView(discord.ui.View):
            def __init__(self, user_id, rank, xp):
                super().__init__(timeout=120)
                self.add_item(JennaSelect(user_id, rank, xp))

        # Envia o embed inicial com o seletor
        embed = discord.Embed(title="🛡️ Capitã Jenna - Terminal de Conversa", description="Escolha um tópico no menu abaixo.", color=discord.Color.gold())
        file, url = get_local_file("assets/npcs/Jenna.gif", "jenna.gif")
        if file: embed.set_thumbnail(url=url)
        view = JennaView(self.user_id, rank, xp)
        if file:
            await interaction.response.send_message(embed=embed, file=file, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # --- BOTÃO 2: MEU CARTÃO (PERFIL & RANK) ---
    @discord.ui.button(label="Meu Cartão", style=discord.ButtonStyle.success, emoji="🆔", row=0)
    async def card_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        user_name = interaction.user.display_name

        conn = get_bot_instance().db_conn

        if not has_account(conn, user_id):
            # Erro continua invisível pra não poluir o chat
            return await interaction.response.send_message("❌ Erro de registro. Use /eco pescar para criar conta.", ephemeral=True)

        _guilda = get_guild_rank(conn, user_id)
        current_rank = _guilda['rank']
        xp_atual = _guilda['xp']
        peixes = get_fish_count(conn, user_id)
        
        # Pega dados do rank atual
        rank_data = GUILD_RANKS.get(current_rank, GUILD_RANKS["F"])
        next_rank_key = rank_data['next']
        
        # --- Lógica da Barra de Progresso ---
        desc_progresso = "🏆 Nível Máximo Alcançado!"
        
        if next_rank_key:
            next_rank_data = GUILD_RANKS.get(next_rank_key)
            meta = next_rank_data['req_xp']
            if meta <= 0: meta = 100 
            
            porcentagem = min(100, int((xp_atual / meta) * 100))
            blocos_cheios = porcentagem // 10
            bar_fill = "🟩" * blocos_cheios
            bar_empty = "⬜" * (10 - blocos_cheios)
            
            desc_progresso = f"`{bar_fill}{bar_empty}` **{porcentagem}%**\n({xp_atual} / {meta} XP para Rank {next_rank_key})"

        # Cria o Embed
        embed = discord.Embed(title=f"🆔 Credencial da Guilda: {user_name}", color=discord.Color.green())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        embed.add_field(name="🎖️ Rank Atual", value=f"**{current_rank}** - {rank_data['name']}", inline=True)
        embed.add_field(name="🎣 Histórico", value=f"{peixes} Peixes", inline=True)
        embed.add_field(name="📈 Progresso", value=desc_progresso, inline=False)
        
        # Footer motivacional
        embed.set_footer(text=f"Solicitado por {user_name}")

        # --- MUDANÇA AQUI: Mensagem Pública! ---
        # Removi o 'ephemeral=True', então agora aparece para todos.
        await interaction.response.send_message(embed=embed)

    # --- BOTÃO 3: GRUPO (HUD DIFERENCIADA LÍDER vs MEMBRO) ---
    @discord.ui.button(label="Gerenciar Grupo", style=discord.ButtonStyle.primary, emoji="👥", row=1)
    async def party_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cursor = get_bot_instance().db_conn.cursor()
        
        # 1. Tenta achar o grupo do usuário (seja líder ou membro)
        party_row = None
        is_leader = False
        
        # Busca onde sou líder
        leader_check = cursor.execute("SELECT * FROM parties WHERE leader_id = ?", (self.user_id,)).fetchone()
        
        if leader_check:
            party_row = leader_check
            is_leader = True
        else:
            # Busca onde sou membro (varredura)
            all_parties = cursor.execute("SELECT * FROM parties").fetchall()
            for p in all_parties:
                if self.user_id in json.loads(p['members_json']):
                    party_row = p
                    break
        
        # 2. Se não tem grupo, CRIA UM NOVO (Vira Líder)
        if not party_row:
            cursor.execute("INSERT OR IGNORE INTO parties (leader_id, leader_name, members_json) VALUES (?, ?, '[]')", (self.user_id, self.user_name))
            get_bot_instance().db_conn.commit()
            # Recarrega
            party_row = cursor.execute("SELECT * FROM parties WHERE leader_id = ?", (self.user_id,)).fetchone()
            is_leader = True

        # --- PREPARAÇÃO DOS DADOS ---
        members_ids = json.loads(party_row['members_json'])
        member_count = len(members_ids) + 1 # +1 do líder
        
        # Busca Missão Ativa
        mission_text = "🛌 *Nenhuma missão ativa no momento.*"
        if party_row['active_mission_id']:
            m_id = party_row['active_mission_id']
            # Procura nome da missão no DB
            m_name = "Missão Secreta"
            for r_list in MISSION_DB.values():
                for m in r_list:
                    if m['id'] == m_id: m_name = m['title']
            
            prog = party_row['mission_progress']
            target = party_row['mission_target']
            pct = int((prog/target)*100) if target > 0 else 0
            # Barra de progresso visual
            bar = "█" * (pct // 10) + "░" * (10 - (pct // 10))
            mission_text = f"🔥 **{m_name}**\n`{bar}` {pct}%\nProgresso: **{prog}/{target}**"

        # --- MONTAGEM DO EMBED (HUD) ---
        if is_leader:
            # === HUD DO LÍDER ===
            embed = discord.Embed(title="👑 Painel do Capitão (Líder)", color=discord.Color.gold())
            embed.description = f"Você está no comando do esquadrão **{self.user_name}'s Party**."
            
            # Lista de Membros Formatada
            member_names = []
            if members_ids:
                nomes = get_user_names(get_bot_instance().db_conn, members_ids)
                member_names = [f"👤 {n}" for n in nomes.values()]
            
            list_txt = "\n".join(member_names) if member_names else "*Nenhum marinheiro recrutado.*"
            
            embed.add_field(name=f"👥 Tripulação ({member_count}/4)", value=list_txt, inline=False)
            embed.add_field(name="📜 Status da Missão", value=mission_text, inline=False)
            embed.set_footer(text="Use os menus abaixo para Expulsar ou Convidar.")
            
        else:
            # === HUD DO MEMBRO ===
            embed = discord.Embed(title="🛡️ Alojamento da Tripulação", color=discord.Color.blue())
            
            # Informação do Líder
            l_name = party_row['leader_name']
            embed.add_field(name="👑 Capitão do Grupo", value=f"**{l_name}**", inline=True)
            embed.add_field(name="👥 Tamanho", value=f"{member_count}/4 Pescadores", inline=True)
            
            # Destaque para a Missão (Foco do Membro)
            embed.add_field(name="🎯 OBJETIVO ATUAL", value=mission_text, inline=False)
            
            embed.set_footer(text="Se o líder ficar AFK, você pode sair clicando no botão vermelho.")

        # Chama a View de Controle
        view = PartyControlsView(self.user_id, is_leader, party_row)
        
        await interaction.response.edit_message(embed=embed, view=view, attachments=[])

    # --- BOTÃO 4: MISSÕES (QUADRO ROTATIVO) ---
    @discord.ui.button(label="Quadro de Missões", style=discord.ButtonStyle.secondary, emoji="📜", row=1)
    async def mission_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cursor = get_bot_instance().db_conn.cursor()
        user_rank = get_guild_rank(get_bot_instance().db_conn, self.user_id)['rank']

        party = cursor.execute("SELECT active_mission_id, mission_progress, mission_target FROM parties WHERE leader_id = ?", (self.user_id,)).fetchone()
        
        embed = discord.Embed(title=f"📜 Quadro de Missões (Rank {user_rank})", color=discord.Color.gold())
        
        if party and party['active_mission_id']:
            m_id = party['active_mission_id']
            m_title = "Missão Desconhecida"
            for ranks in MISSION_DB.values():
                for m in ranks:
                    if m['id'] == m_id: m_title = m['title']
            
            prog = party['mission_progress']
            targ = party['mission_target']
            pct = int((prog/targ)*100) if targ > 0 else 0
            embed.description = f"🔥 **Missão Ativa:** {m_title}\n**Progresso:** `{prog}/{targ}` ({pct}%)\n\n*Complete esta missão para pegar outra.*"
        else:
            embed.description = f"📅 **Missões de Hoje ({datetime.now().strftime('%d/%m')}):**\nSelecione uma abaixo para iniciar."

        conn = get_bot_instance().db_conn
        feitas = missions_completed_today(conn, self.user_id)
        vagas = mission_slots_left(conn, self.user_id)
        embed.add_field(
            name="📋 Conclusões de hoje",
            value=f"{len(feitas)}/{MISSION_DAILY_CAP} — restam **{vagas}**",
            inline=False,
        )

        view_mission = discord.ui.View()
        view_mission.add_item(MissionSelect(self.user_id, user_rank, feitas_hoje=feitas, vagas=vagas))
        
        # Botão Voltar (Recarrega a imagem da Guilda)
        back_btn = discord.ui.Button(label="Voltar", style=discord.ButtonStyle.danger, row=1)
        async def back_cb(i): 
            emb = discord.Embed(title="🏛️ Guilda de Porto Solare", description="Bem-vindo ao quartel general.", color=discord.Color.dark_blue())
            file, url = get_local_file("assets/locais/guilda.jpg", "guilda.jpg")
            if file: 
                emb.set_image(url=url)
                await i.response.edit_message(embed=emb, attachments=[file], view=GuildView(self.user_id, self.user_name))
            else:
                await i.response.edit_message(embed=emb, view=GuildView(self.user_id, self.user_name))
        
        back_btn.callback = back_cb
        view_mission.add_item(back_btn)

        await interaction.response.edit_message(embed=embed, view=view_mission, attachments=[]) # Limpa anexos

# --- COMANDO PRINCIPAL: /ECO GUILDA ---
@eco_group.command(name="guilda", description="Acessa o hub da Guilda (Perfil, Rank, Grupo).")
async def guilda(interaction: discord.Interaction):
    # 1. TRAVA DE ACESSO (O Portão da Cidade)
    user_id = interaction.user.id
    conn = get_bot_instance().db_conn
    cursor = conn.cursor()
    quest = cursor.execute("SELECT current_chapter FROM quest_progress WHERE user_id = ?", (user_id,)).fetchone()
    
    # Verifica se tem acesso liberado (quest da garrafa concluída ou cidade descoberta e selo entregue)
    liberado = False
    if quest and quest['current_chapter'] in ['acesso_liberado', 'city_spotted']:
        # Se 'city_spotted' for suficiente para ver o menu (mas talvez não missões), libera.
        # Ajuste conforme sua lore. Aqui assumimos que 'acesso_liberado' é o ideal.
        liberado = True
        
    # Se quiser forçar liberação para testar, comente o if acima e descomente: liberado = True
    
    if not liberado:
        # Verifica se por acaso ele tem o rank (bug fix). Mantém a semântica
        # antiga: só conta se o jogador JÁ tem conta — get_guild_rank sozinho
        # devolveria 'F' até para quem nunca jogou, escancarando o portão.
        if has_account(conn, user_id) and get_guild_rank(conn, user_id)['rank']:
            liberado = True

    if not liberado:
        return await interaction.response.send_message("🚫 **Acesso Negado.**\nOs guardas barram sua entrada.\n*\"Apenas membros credenciados. Vá falar com a Capitã Mara se tiver o Selo.\"*", ephemeral=True)

    # 2. SE ENTROU:
    # Garante a linha do jogador na v4 (users.guild_rank já nasce 'F'). Antes
    # isto era um INSERT OR IGNORE direto em `economy`: desde a etapa 3 nada
    # no runtime lê guild_rank da legada, então aquele INSERT só criava uma
    # linha legada órfã, sem contrapartida em `users`.
    ensure_user(conn, user_id, interaction.user.name)
    
    # Mostra a "Recepção"
    embed = discord.Embed(title="🏛️ Guilda de Porto Solare", description="Bem-vindo ao quartel general. Selecione uma ação no terminal.", color=discord.Color.dark_blue())
    
    file_img = None
    if os.path.exists("assets/mapas/interior_guilda.png"):
        file_img = discord.File("assets/mapas/interior_guilda.png", filename="guilda.png")
        embed.set_image(url="attachment://guilda.png")
    
    if file_img:
        await interaction.response.send_message(embed=embed, file=file_img, view=GuildView(user_id, interaction.user.name))
    else:
        await interaction.response.send_message(embed=embed, view=GuildView(user_id, interaction.user.name))

# --- VIEW DE CONVITE (Aparece para o AMIGO aceitar) ---
class PartyInviteView(discord.ui.View):
    def __init__(self, leader_id, target_id):
        super().__init__(timeout=60) # Convite dura 60 segundos
        self.leader_id = leader_id
        self.target_id = target_id

    @discord.ui.button(label="Aceitar Convite", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            return await interaction.response.send_message("Esse convite não é para você!", ephemeral=True)
        
        cursor = get_bot_instance().db_conn.cursor()
        
        # 1. Verifica se o grupo ainda existe
        party = cursor.execute("SELECT members_json FROM parties WHERE leader_id = ?", (self.leader_id,)).fetchone()
        if not party:
            return await interaction.response.edit_message(content="❌ O grupo foi desfeito antes de você aceitar.", embed=None, view=None)
        
        # 2. Verifica se o usuário já tem grupo (Líder ou Membro)
        check_leader = cursor.execute("SELECT leader_id FROM parties WHERE leader_id = ?", (self.target_id,)).fetchone()
        if check_leader:
             return await interaction.response.send_message("❌ Você é líder de outro grupo! Desfaça ele antes de entrar.", ephemeral=True)

        # Adicione isso antes de adicionar o membro
        check_member = cursor.execute("SELECT leader_id FROM parties WHERE members_json LIKE ?", (f"%{interaction.user.id}%",)).fetchone()
        if check_member:
            return await interaction.response.send_message("❌ Você já é membro de outro grupo! Saia dele primeiro.", ephemeral=True)

        # 3. Adiciona o membro
        members = json.loads(party['members_json'])
        if self.target_id not in members:
            members.append(self.target_id)
            cursor.execute("UPDATE parties SET members_json = ? WHERE leader_id = ?", (json.dumps(members), self.leader_id))
            get_bot_instance().db_conn.commit()
            
        await interaction.response.edit_message(content=f"🤝 **Squad Formado!** {interaction.user.mention} entrou para o grupo.", embed=None, view=None)

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.danger, emoji="✖️")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id: return
        await interaction.response.edit_message(content=f"🚫 Convite recusado.", embed=None, view=None)

# --- SELETOR DE USUÁRIO (O Líder escolhe quem convidar) ---
class PartyMemberSelect(discord.ui.UserSelect):
    def __init__(self, leader_id):
        super().__init__(placeholder="Selecione quem convidar...", min_values=1, max_values=1)
        self.leader_id = leader_id

    async def callback(self, interaction: discord.Interaction):
        target_user = self.values[0]
        
        if target_user.id == interaction.user.id:
            return await interaction.response.send_message("Você não pode convidar a si mesmo, solidão.", ephemeral=True)
        
        if target_user.bot:
            return await interaction.response.send_message("Robôs não sabem pescar.", ephemeral=True)

        # Manda o convite público para o amigo ver
        embed = discord.Embed(title="💌 Convite de Grupo", description=f"{interaction.user.mention} quer que você entre na Party dele para missões da Guilda!", color=discord.Color.gold())
        
        # Responde confirmando que enviou
        await interaction.response.send_message(f"📨 Convite enviado para **{target_user.name}**!", ephemeral=True)
        
        # Envia a mensagem pública com o botão para o convidado
        await interaction.channel.send(
            content=target_user.mention, 
            embed=embed, 
            view=PartyInviteView(self.leader_id, target_user.id)
        )

# --- CONFIGURAÇÃO DAS ARMADILHAS AFK ---
TRAP_TYPES = {
    "covo_basico": {
        "name": "Covo de Garrafa", 
        "cost": 100,            # Custo para comprar NOVO
        "repair_cost": 35,      # Custo para ARRUMAR
        "capacity": 5,          # Peixes por coleta
        "break_chance": 10,     # 10% de chance de quebrar ao coletar
        # Estava `00` (zero) com um comentário dizendo "5 minutos": a armadilha
        # nascia em 'working' com timer_end = agora, a re-renderização já
        # encontrava remaining <= 0 e promovia para 'ready' na mesma interação,
        # então o Covo entregava as 5 capturas instantaneamente e o único freio
        # era o reset_time. Valor correto definido em 10 minutos.
        "wait_time": 600,       # 10 minutos esperando o peixe cair
        "reset_time": 120,      # 2 minutos para limpar/desembolar
        "loot_tier_max": 1      # Só pega peixe comum/incomum
    },
    "rede_industrial": {
        "name": "Rede de Arrasto",
        "cost": 1500,
        # break_chance 80 -> 35 e repair_cost 400 -> 250. Com os valores
        # antigos a Rede custava 1.280 Sachês/hora só em conserto (4 coletas/h
        # x 80% x 400) e, mesmo com o /eco vender pagando o loot, sobravam
        # 122/h — METADE do Covo, que é grátis. Uma compra de 1.500 que rende
        # menos que o item gratuito é armadilha, e nenhuma tabela de preço de
        # venda conserta: baixar a taxa afunda a Rede, subir quebra o Covo.
        # Nos valores novos ela rende ~1.052/h e se paga em ~1,8h de uso.
        "repair_cost": 250,
        "capacity": 15,         # Pega MUITO peixe
        "break_chance": 35,     # Risco real, mas não confisco
        "wait_time": 600,       # 10 minutos (é uma rede grande)
        "reset_time": 300,      # 5 minutos para arrumar a bagunça
        "loot_tier_max": 2      # Pode pegar raros
    }
}

def process_afk_trap(trap_json):
    """Calcula recompensas da armadilha e retorna (novo_json, itens_ganhos, quebrou)."""
    if not trap_json: return {}, [], False
    
    data = json.loads(trap_json)
    trap_type = data.get('type')
    stats = TRAP_TYPES.get(trap_type)
    
    if not stats: return {}, [], False

    start_time = datetime.fromtimestamp(data['start'])
    now = datetime.now()
    diff_hours = (now - start_time).total_seconds() / 3600
    
    # Se não passou o tempo mínimo, nada acontece
    if diff_hours < stats['time_hours']:
        return trap_json, [], False
    
    # CALCULANDO RECOMPENSAS
    # Quantos ciclos completos se passaram? (Ex: 4 horas numa trap de 1h = 4 coletas)
    cycles = int(diff_hours // stats['time_hours'])
    
    # Limita os ciclos pela durabilidade restante
    cycles = min(cycles, data['durability'])
    
    loot_total = 0
    for _ in range(cycles):
        loot_total += random.randint(stats['loot_min'], stats['loot_max'])
        
    # Atualiza durabilidade
    data['durability'] -= cycles
    
    # Reseta o tempo para "agora" (para o próximo ciclo) ou remove se acabou
    broken = False
    if data['durability'] <= 0:
        broken = True
        new_json = "{}" # Trap destruída
    else:
        # Avança o relógio apenas o tempo que foi consumido
        seconds_consumed = cycles * stats['time_hours'] * 3600
        data['start'] = data['start'] + seconds_consumed 
        new_json = json.dumps(data)
        
    # Gera os itens (peixes aleatórios simples ou lixo)
    rewards = []
    # 70% chance de peixe comum, 30% lixo (afinal é automático)
    for _ in range(loot_total):
        if random.random() < 0.7:
            pool = [p[0] for p in FISH_DB if p[4] <= 1] # Tier 0 e 1
            rewards.append(random.choice(pool))
        else:
            rewards.append(random.choice(["Bota Velha", "Lata Vazia", "Alga"]))
            
    return new_json, rewards, broken

# --- FORJA DO ABISMO ---
# Varas que habilitam a forja. Derivado de ROD_STATS em vez de escrito à mão
# para que uma vara de tier 5 nova entre sozinha no requisito.
FORGE_ALLOWED_RODS = frozenset(k for k, v in ROD_STATS.items() if v["tier"] >= 5)


def forge_status(conn, user_id: int) -> dict:
    """Estado da forja para exibição: nível, custo do próximo e requisitos."""
    nivel = get_forge_level(conn, user_id)
    rod = get_current_rod(conn, user_id) or "vara_bambu"
    rank = get_guild_rank(conn, user_id)["rank"]
    custo = forge_level_cost(nivel + 1)
    tem_vara = rod in FORGE_ALLOWED_RODS
    tem_rank = rank == FORGE_REQUIRED_RANK
    return {
        "nivel": nivel,
        "proximo": nivel + 1,
        "custo_saches": custo["saches"],
        "custo_scrap": custo["scrap"],
        "bonus_atual": forge_luck_multiplier(nivel),
        "bonus_proximo": forge_luck_multiplier(nivel + 1),
        "wallet": get_wallet(conn, user_id),
        "scrap": get_scrap(conn, user_id),
        "rod": rod,
        "rank": rank,
        "tem_vara": tem_vara,
        "tem_rank": tem_rank,
        "desbloqueado": tem_vara and tem_rank,
    }


def build_forge_embed(estado: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"🌀 Forja do Abismo — Nível {estado['nivel']}",
        description=(
            "🔧 **Galdino:** 'A Fenda cospe um metal que não devia existir. "
            "Eu bato nele, você paga. Não tem fim.'"
        ),
        color=discord.Color.dark_purple(),
    )
    embed.add_field(
        name="Bônus atual",
        value=f"**+{(estado['bonus_atual'] - 1) * 100:.1f}%** de valor de captura",
        inline=True,
    )
    embed.add_field(
        name=f"Nível {estado['proximo']}",
        value=f"**+{(estado['bonus_proximo'] - 1) * 100:.1f}%**",
        inline=True,
    )
    embed.add_field(name="​", value="​", inline=True)
    embed.add_field(
        name="Custo do próximo nível",
        value=f"💰 {estado['custo_saches']:,} Sachês\n⚙️ {estado['custo_scrap']:,} Sucata".replace(",", "."),
        inline=True,
    )
    embed.add_field(
        name="Você tem",
        value=f"💰 {estado['wallet']:,}\n⚙️ {estado['scrap']:,}".replace(",", "."),
        inline=True,
    )
    embed.set_footer(text="A escada não tem teto — cada nível custa 30% mais que o anterior.")
    return embed


class ForgeView(discord.ui.View):
    """Compra de um nível por clique. Sem lote: o custo cresce 30% a cada
    nível, então um botão de "comprar N" esconderia do jogador quanto ele
    está gastando de verdade."""

    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    @discord.ui.button(label="Forjar próximo nível", style=discord.ButtonStyle.danger, emoji="🔨")
    async def forjar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Essa forja não é sua.", ephemeral=True)

        conn = get_bot_instance().db_conn
        # try_upgrade_forge relê vara, rank, saldo e nível dentro da própria
        # transação: nada do que foi capturado quando a view abriu é usado
        # para decidir (a view fica de pé por até 180s).
        resultado = try_upgrade_forge(conn, self.user_id, FORGE_ALLOWED_RODS)

        if not resultado["success"]:
            motivos = {
                "locked_rod": "🔒 Requer uma vara de **Tier 5** equipada.",
                "locked_rank": f"🔒 Requer **Rank {FORGE_REQUIRED_RANK}** da Guilda.",
                "insufficient_saches": (
                    f"💸 Faltam Sachês: precisa de **{resultado['cost_saches']:,}**, "
                    f"tem {resultado['wallet']:,}."
                ).replace(",", "."),
                "insufficient_scrap": (
                    f"⚙️ Falta sucata: precisa de **{resultado['cost_scrap']:,}**, "
                    f"tem {resultado['scrap']:,}."
                ).replace(",", "."),
            }
            return await interaction.response.send_message(
                motivos.get(resultado["reason"], "❌ Não foi possível forjar agora."),
                ephemeral=True,
            )

        estado = forge_status(conn, self.user_id)
        await interaction.response.edit_message(embed=build_forge_embed(estado), view=self)
        await interaction.followup.send(
            f"🌀 **Forja nível {resultado['level']}!** "
            f"Bônus de captura agora é **+{(forge_luck_multiplier(resultado['level']) - 1) * 100:.1f}%**.",
            ephemeral=True,
        )


# --- VIEW DA OFICINA DO GALDINO (RECICLAGEM & UPGRADES) ---
class GaldinoView(discord.ui.View):
    def __init__(self, user_id, user_name):
        super().__init__(timeout=180)
        self.user_id = user_id

    # --- BOTÃO 1: RECICLAGEM (Gera Sucata) ---
    @discord.ui.button(label="Reciclar Sucata", style=discord.ButtonStyle.success, emoji="♻️", row=0)
    async def recycle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_bot_instance().db_conn
        # Camada v4, não a tabela legada: apagar o lixo só do JSON de
        # `economy.inventory` era desfeito por sync_user_to_economy() no
        # comando seguinte — o lixo voltava e a sucata paga ficava, o que
        # tornava reciclar o mesmo lixo em loop uma fonte infinita de sucata.
        inv = get_inventory(conn, self.user_id)

        gain = 0
        for t in TRASH_ITEMS:
            qtd = inv.get(t, 0)
            if qtd > 0:
                gain += qtd * 5
                add_inventory_item(conn, self.user_id, t, -qtd)

        if gain > 0:
            # Baú da Maré (+25%) passa por aqui como por toda fonte de sucata.
            bruto = gain
            gain = grant_scrap(conn, self.user_id, gain)
            extra = gain - bruto
            msg = f"🔧 **Galdino:** 'Isso sim é material!'\n⚙️ Ganhou: {gain} Sucata."
            if extra:
                msg += f"\n🧰 *Baú da Maré rendeu +{extra}.*"
        else:
            msg = "🔧 **Galdino:** 'Sua mochila tá limpa demais. Suma daqui!'"
            
        await interaction.response.send_message(msg, ephemeral=True)

    # --- BOTÃO 2: TUNING DE VARA ---
    @discord.ui.button(label="Tunar Vara", style=discord.ButtonStyle.primary, emoji="🔫", row=0)
    async def tune_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_bot_instance().db_conn
        scrap = get_scrap(conn, self.user_id)
        upgrades = get_rod_upgrades(conn, self.user_id)

        luck_lvl = upgrades["luck"]
        cd_lvl = upgrades["cd"]
        cost_luck = (luck_lvl + 1) * 100
        cost_cd = (cd_lvl + 1) * 100

        embed = discord.Embed(title="🔧 Tuning de Equipamento", description=f"**Sucata Disponível:** ⚙️ {scrap}", color=discord.Color.orange())
        embed.add_field(name=f"🍀 Mira Laser [Lv {luck_lvl}]", value=f"Custo: ⚙️ {cost_luck}", inline=True)
        embed.add_field(name=f"⚡ Rolamentos [Lv {cd_lvl}]", value=f"Custo: ⚙️ {cost_cd}", inline=True)

        # Os botões abaixo NÃO usam scrap/custo capturados aqui em cima —
        # try_upgrade_rod relê sucata e nível na hora do clique e recalcula o
        # custo a partir do nível fresco (mesmo padrão do fix de duplicação
        # da pesca: nunca confiar em estado capturado na abertura da view, que
        # fica aberta por até 180s e pode ser clicada mais de uma vez).
        view = discord.ui.View()
        async def up_luck(inter):
            result = try_upgrade_rod(conn, self.user_id, "luck", cost_per_level=100, max_level=5)
            if result["reason"] == "insufficient_scrap": return await inter.response.send_message("❌ Sucata insuficiente!", ephemeral=True)
            if result["reason"] == "max_level": return await inter.response.send_message("⚠️ Max Level!", ephemeral=True)
            await inter.response.send_message("✅ Sorte aumentada!", ephemeral=True)

        async def up_cd(inter):
            result = try_upgrade_rod(conn, self.user_id, "cd", cost_per_level=100, max_level=5)
            if result["reason"] == "insufficient_scrap": return await inter.response.send_message("❌ Sucata insuficiente!", ephemeral=True)
            if result["reason"] == "max_level": return await inter.response.send_message("⚠️ Max Level!", ephemeral=True)
            await inter.response.send_message("✅ Cooldown reduzido!", ephemeral=True)

        b1 = discord.ui.Button(label="Upar Sorte", style=discord.ButtonStyle.success); b1.callback = up_luck
        b2 = discord.ui.Button(label="Upar CD", style=discord.ButtonStyle.primary); b2.callback = up_cd
        view.add_item(b1); view.add_item(b2)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # --- BOTÃO 3: FORJA DO ABISMO (ESCADA SEM TETO DE FIM DE JOGO) ---
    @discord.ui.button(label="Forja do Abismo", style=discord.ButtonStyle.danger, emoji="🌀", row=1)
    async def forge_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_bot_instance().db_conn
        estado = forge_status(conn, self.user_id)

        # Bloqueado: explica o requisito em vez de esconder. Mesmo padrão da
        # quest do Covo, que mostra o progresso 0/50 em vez de omitir a
        # máquina — requisito invisível vira requisito inexistente para quem
        # está jogando.
        if not estado["desbloqueado"]:
            embed = discord.Embed(
                title="🌀 Forja do Abismo",
                description=(
                    "🔒 **Galdino:** 'Isso aí não é pra qualquer um. Volta quando "
                    "tiver equipamento e patente pra encarar.'\n\n"
                    "**Requisitos:**"
                ),
                color=discord.Color.dark_grey(),
            )
            embed.add_field(
                name=f"{'✅' if estado['tem_vara'] else '❌'} Vara de Tier 5 equipada",
                value=" ou ".join(ROD_STATS[k]["name"] for k in FORGE_ALLOWED_RODS)
                + f"\n*Equipada: {ROD_STATS.get(estado['rod'], {}).get('name', estado['rod'])}*",
                inline=False,
            )
            embed.add_field(
                name=f"{'✅' if estado['tem_rank'] else '❌'} Rank {FORGE_REQUIRED_RANK} da Guilda",
                value=f"*Atual: Rank {estado['rank']}*",
                inline=False,
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.send_message(
            embed=build_forge_embed(estado),
            view=ForgeView(self.user_id),
            ephemeral=True,
        )

    # --- BOTÃO 4: EXAMINAR MÁQUINA (QUEST + GERENCIAMENTO HÍBRIDO) ---
    @discord.ui.button(label="Examinar Máquina", style=discord.ButtonStyle.secondary, emoji="🦀", row=1)
    async def trap_manager(self, interaction: discord.Interaction, button: discord.ui.Button):
        # O decorator @discord.ui.button substitui este atributo por um
        # discord.ui.Button na instância da View, então `self.trap_manager`
        # não é chamável. As transições automáticas de estado (working->ready,
        # cooldown->idle) precisam re-renderizar o painel, então a lógica vive
        # em _render_trap_manager, que é uma corrotina normal.
        await self._render_trap_manager(interaction)

    async def _render_trap_manager(self, interaction: discord.Interaction):
        conn = get_bot_instance().db_conn
        # Estado e mochila vêm da v4 (user_trap / user_inventory). A coluna
        # legada `economy.afk_trap` é reescrita a partir de user_trap por
        # sync_user_to_economy, então ler dela dava um estado que ia ser
        # descartado — e gravar nela não chegava na v4.
        trap_data = get_trap(conn, self.user_id) or None
        inv = get_inventory(conn, self.user_id)

        embed = discord.Embed(title="🦀 Oficina de Armadilhas", color=discord.Color.dark_orange())

        # ==========================================================
        # FASE 1: QUEST (Se não tem armadilha, entra na Quest do Lixo)
        # ==========================================================
        if not trap_data:
            # Conta os lixos
            total_trash = sum(inv.get(t, 0) for t in TRASH_ITEMS)
            meta = 50

            if total_trash < meta:
                # Texto da Quest Incompleta
                intro_text = get_dialogue("galdino", "afk_machine_intro") # "Aquilo? Protótipos..."
                embed.description = f"{intro_text}\n\n📊 **Progresso:** {total_trash}/{meta} Lixos na mochila."
                embed.set_footer(text="Dica: Pesque lixo (Latas, Botas, Pneus, Sacolas, Espinhas) para completar.")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                # Quest Completa -> Botão de Craftar
                embed.description = f"🔧 **Galdino:** 'Olha só! {total_trash} peças de sucata de primeira qualidade!'\n\nVocê reuniu material suficiente para montar o **Covo de Garrafa**."
                embed.color = discord.Color.green()
                
                view_craft = discord.ui.View()
                btn_craft = discord.ui.Button(label="Montar Protótipo (-50 Lixos)", style=discord.ButtonStyle.success, emoji="🛠️")
                
                async def craft_callback(inter):
                    # Relê a mochila na hora de gastar: entre montar o embed e
                    # clicar no botão o jogador pode ter gasto o lixo em outro
                    # fluxo (reciclar no Galdino, por exemplo).
                    inv_fresh = get_inventory(conn, self.user_id)
                    if sum(inv_fresh.get(t, 0) for t in TRASH_ITEMS) < meta:
                        return await inter.response.send_message("❌ Você não tem mais lixo suficiente.", ephemeral=True)

                    # Consome 50 lixos
                    removidos = 0
                    for t in TRASH_ITEMS:
                        disponivel = inv_fresh.get(t, 0)
                        se_gasta = min(disponivel, meta - removidos)
                        if se_gasta > 0:
                            add_inventory_item(conn, self.user_id, t, -se_gasta)
                            removidos += se_gasta
                        if removidos >= meta:
                            break

                    # Instala Covo Básico (Grátis na primeira vez)
                    # Status Idle para ele poder dar o start manual
                    set_trap(conn, self.user_id, {"type": "covo_basico", "status": "idle", "timer_end": 0})

                    await inter.response.send_message(f"{get_dialogue('galdino', 'afk_success')}\n(Agora clique em 'Examinar Máquina' novamente para usar!)", ephemeral=True)

                btn_craft.callback = craft_callback
                view_craft.add_item(btn_craft)
                await interaction.response.send_message(embed=embed, view=view_craft, ephemeral=True)
            return

        # ==========================================================
        # FASE 2: GERENCIAMENTO (Se já tem armadilha)
        # ==========================================================
        t_type = trap_data['type']
        t_status = trap_data.get('status', 'idle')
        t_timer = trap_data.get('timer_end', 0)
        stats = TRAP_TYPES.get(t_type)
        
        if not stats: return # Segurança

        now_ts = datetime.now().timestamp()
        embed.title = f"🦀 {stats['name']} (Status: {t_status.upper()})"
        view = discord.ui.View()

        # Lógica dos Estados (Idle -> Working -> Ready -> Broken/Cooldown)

        if t_status == "broken":
            embed.description = f"💥 **ESTÁ QUEBRADA!**\nGaldino cobra **{stats['repair_cost']} Sachês** para consertar."
            embed.color = discord.Color.red()
            
            btn_repair = discord.ui.Button(label=f"Consertar ({stats['repair_cost']} $)", style=discord.ButtonStyle.danger, emoji="🔨")
            async def repair_cb(inter):
                if not try_spend_wallet(conn, self.user_id, stats['repair_cost'], inter.user.name):
                    return await inter.response.send_message("💸 Falta dinheiro.", ephemeral=True)

                trap_data['status'] = 'idle'
                set_trap(conn, self.user_id, trap_data)
                await inter.response.send_message("🔨 **Consertado!**", ephemeral=True)
            
            btn_repair.callback = repair_cb
            view.add_item(btn_repair)

        elif t_status == "working":
            remaining = int(t_timer - now_ts)
            if remaining > 0:
                embed.description = f"🌊 **Trabalhando...**\nTempo restante: <t:{int(t_timer)}:R>"
                embed.color = discord.Color.blue()
                view.add_item(discord.ui.Button(label="Aguarde...", disabled=True))
            else:
                trap_data['status'] = 'ready'
                set_trap(conn, self.user_id, trap_data)
                return await self._render_trap_manager(interaction)

        elif t_status == "ready":
            embed.description = f"🐟 **Rede Cheia!** Capacidade: {stats['capacity']}.\n*Cuidado: Pode rasgar ao puxar.*"
            embed.color = discord.Color.green()
            
            btn_collect = discord.ui.Button(label="Puxar Rede", style=discord.ButtonStyle.success, emoji="🎣")
            async def collect_cb(inter):
                # Relê o estado na v4, não na legada: a legada é derivada e
                # podia ainda dizer 'ready' depois de uma coleta já feita,
                # liberando a mesma rede duas vezes.
                fresh_trap = get_trap(conn, self.user_id)
                if fresh_trap.get('status') != 'ready': return await inter.response.send_message("❌ Estado inválido.", ephemeral=True)

                # Fecha a rede ANTES de pagar o loot: se a entrega falhar no
                # meio, o jogador perde a coleta — o inverso deixaria a rede
                # 'ready' com o loot já creditado, que é o dupe.
                if random.randint(1, 100) <= stats['break_chance']:
                    fresh_trap['status'] = 'broken'
                    sufixo = "\n\n💥 **CRACK!** A rede rasgou!"
                else:
                    fresh_trap['status'] = 'cooldown'
                    fresh_trap['timer_end'] = now_ts + stats['reset_time']
                    sufixo = "\n\n🕸️ Limpando a rede..."
                set_trap(conn, self.user_id, fresh_trap)

                rewards = []
                pool = [p[0] for p in FISH_DB if p[4] <= stats['loot_tier_max']]
                for _ in range(stats['capacity']):
                    fish = random.choice(pool)
                    add_inventory_item(conn, self.user_id, fish, 1)
                    rewards.append(fish)

                c = Counter(rewards)
                reward_str = ", ".join([f"{k} x{v}" for k, v in c.items()])
                msg = f"💰 **Coleta:** {reward_str}{sufixo}"

                await inter.response.send_message(msg, ephemeral=True)
            
            btn_collect.callback = collect_cb
            view.add_item(btn_collect)

        elif t_status == "cooldown":
            remaining = int(t_timer - now_ts)
            if remaining > 0:
                embed.description = f"🕸️ **Desembolando...**\nPronta em: <t:{int(t_timer)}:R>"
                view.add_item(discord.ui.Button(label="Limpando...", disabled=True))
            else:
                trap_data['status'] = 'idle'
                set_trap(conn, self.user_id, trap_data)
                return await self._render_trap_manager(interaction)

        elif t_status == "idle":
            embed.description = "A armadilha está limpa e pronta.\nJogar na água?"
            wait_min = int(stats['wait_time'] / 60)
            
            btn_start = discord.ui.Button(label=f"Jogar ({wait_min}m)", style=discord.ButtonStyle.primary, emoji="🌊")
            async def start_cb(inter):
                trap_data['status'] = 'working'
                trap_data['timer_end'] = now_ts + stats['wait_time']
                set_trap(conn, self.user_id, trap_data)
                await inter.response.send_message("🌊 **Lançada!**", ephemeral=True)
            
            btn_start.callback = start_cb
            view.add_item(btn_start)
            
            # --- LOJA DE UPGRADE (Só aparece se estiver IDLE) ---
            # Permite comprar uma melhor (Rede Industrial) se tiver grana
            if t_type == "covo_basico":
                btn_buy = discord.ui.Button(label="Comprar Rede Industrial (1500$)", style=discord.ButtonStyle.secondary, row=1)
                async def buy_better_cb(inter):
                    s_ind = TRAP_TYPES["rede_industrial"]
                    if not try_spend_wallet(conn, self.user_id, s_ind['cost'], inter.user.name):
                        return await inter.response.send_message("💸 Falta dinheiro.", ephemeral=True)

                    # Substitui a trap atual
                    set_trap(conn, self.user_id, {"type": "rede_industrial", "status": "idle", "timer_end": 0})
                    await inter.response.send_message("✅ **Upgrade!** Você comprou a Rede de Arrasto.", ephemeral=True)
                
                btn_buy.callback = buy_better_cb
                view.add_item(btn_buy)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class CompraQuantidadeModal(discord.ui.Modal):
    def __init__(self, item_key, item_stats, user_id, bot_instance):
        super().__init__(title=f"Comprar: {item_stats['name']}")
        self.item_key = item_key
        self.stats = item_stats
        self.user_id = user_id
        self.bot = bot_instance

        # Campo de Texto para digitar o número
        self.qtd = discord.ui.TextInput(
            label=f"Preço Unitário: {item_stats['price']} Sachês",
            placeholder="Digite a quantidade (Ex: 10)",
            min_length=1,
            max_length=4,
            required=True
        )
        self.add_item(self.qtd)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantidade = int(self.qtd.value)
            if quantidade <= 0:
                return await interaction.response.send_message("❌ A quantidade deve ser maior que zero.", ephemeral=True)
        except ValueError:
            return await interaction.response.send_message("❌ Digite apenas números válidos.", ephemeral=True)

        custo_total = self.stats['price'] * quantidade
        conn = self.bot.db_conn

        # Atômico: relê o saldo na hora do submit (o modal pode ter ficado
        # aberto um tempo arbitrário desde que foi mostrado), em vez de usar
        # um saldo capturado quando o dropdown foi clicado.
        if not try_spend_wallet(conn, self.user_id, custo_total, interaction.user.name):
            wallet_atual = get_wallet(conn, self.user_id)
            return await interaction.response.send_message(f"💸 **Saldo Insuficiente!**\nVocê quer {quantidade}x ({custo_total} $), mas só tem {wallet_atual} $.", ephemeral=True)

        add_inventory_item(conn, self.user_id, self.item_key, quantidade)

        # Feedback
        emoji = "📦"
        if self.stats.get('type') == 'buff': emoji = "🧪"
        
        await interaction.response.send_message(
            f"✅ **Compra Confirmada!**\nAdicionado: {emoji} {quantidade}x **{self.stats['name']}**\nTotal Pago: 💰 {custo_total} Sachês.",
            ephemeral=True
        )

class ValeriusShopSelect(discord.ui.Select):
    def __init__(self, user_id):
        self.user_id = user_id
        options = []
        # Filtra apenas itens do tipo 'rod' (Varas) para vender
        for k, v in SHOP_ITEMS.items():
            if v.get('type') == 'rod':
                tier_mark = "⭐" * (v.get('tier', 0) + 1)
                # Adiciona a opção no menu
                options.append(discord.SelectOption(
                    label=v['name'], 
                    value=k, 
                    description=f"{tier_mark} | 💰 {v['price']} Sachês", 
                    emoji="🎣"
                ))
        
        super().__init__(placeholder="💰 Valerius: 'Escolha sua ferramenta de trabalho...'", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id: 
            return await interaction.response.send_message("❌ Essa negociação não é com você.", ephemeral=True)
        
        item_key = self.values[0]
        data = SHOP_ITEMS[item_key]

        conn = get_bot_instance().db_conn

        # Camada v4, não a tabela legada: debitar `economy.wallet` e gravar o
        # item em `economy.inventory` era desfeito por sync_user_to_economy()
        # no comando seguinte — o jogador pagava e a compra sumia. try_spend_wallet
        # ainda relê o saldo dentro da própria transação, então a checagem e o
        # débito não podem divergir.
        if not try_spend_wallet(conn, self.user_id, data['price'], interaction.user.name):
            return await interaction.response.send_message("💰 **Valerius:** 'Sem ouro, sem conversa.' (Saldo insuficiente)", ephemeral=True)

        add_inventory_item(conn, self.user_id, item_key, 1)

        await interaction.response.send_message(f"🤝 **Negócio Fechado!**\nVocê comprou: **{data['name']}** por {data['price']} Sachês.\n*Valerius sorri enquanto conta as moedas.*", ephemeral=True)



class EconomiaCog(commands.Cog):
    """Registra /eco (com /eco guilda como subcomando) e mantém o ciclo automático de clima."""

    def __init__(self, bot):
        self.bot = bot
        set_bot_instance(bot)

    async def cog_load(self):
        # 'guilda' é registrado automaticamente aqui: desde a migração
        # /guilda -> /eco guilda ela virou um @eco_group.command (subcomando),
        # não mais um app_commands.command standalone. Um segundo
        # add_command(guilda) explícito quebra com CommandAlreadyRegistered,
        # porque a árvore resolve o nome pelo root_parent ("eco"), que já foi
        # registrado na linha acima.
        self.bot.tree.add_command(eco_group)
        conn = self.bot.db_conn
        ensure_v4_tables(conn)
        seed_market_prices(conn, FISH_DB)
        if not self.weather_cycle.is_running():
            self.weather_cycle.start()
        if not self.market_cycle.is_running():
            self.market_cycle.start()
        if not self.catch_cleanup_loop.is_running():
            self.catch_cleanup_loop.start()

    def cog_unload(self):
        if self.weather_cycle.is_running():
            self.weather_cycle.cancel()
        if self.market_cycle.is_running():
            self.market_cycle.cancel()
        if self.catch_cleanup_loop.is_running():
            self.catch_cleanup_loop.cancel()

    @tasks.loop(hours=1)
    async def catch_cleanup_loop(self):
        async with CATCHES_LOCK:
            _cleanup_stale_catches()

    @catch_cleanup_loop.before_loop
    async def before_catch_cleanup(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=4)
    async def weather_cycle(self):
        options = ["normal", "bad", "good"]
        weights = [0.7, 0.2, 0.1]
        new_weather = random.choices(options, weights)[0]
        cursor = self.bot.db_conn.cursor()
        prev_row = cursor.execute("SELECT current_weather FROM world_state WHERE id = 1").fetchone()
        prev_weather = prev_row['current_weather'] if prev_row else None
        cursor.execute("UPDATE world_state SET current_weather = ? WHERE id = 1", (new_weather,))
        self.bot.db_conn.commit()
        status_text = f"P3LUCHE | Clima: {WEATHER_EFFECTS[new_weather]['name']}"
        await self.bot.change_presence(activity=discord.Game(name=status_text))
        print(f"[CLIMA] O tempo mudou para: {new_weather.upper()}")

        if new_weather != prev_weather:
            await self._send_weather_banner(new_weather)

    async def _send_weather_banner(self, weather_key: str):
        """Banner pontual (só na mudança de clima). Para 'normal' não há
        asset — resolve_weather_asset já retorna None e este método sai sem
        enviar nada, mantendo o comportamento de texto/status de sempre."""
        asset_path = resolve_weather_asset(weather_key)
        if not asset_path or not FISHING_CHANNEL_ID:
            return
        channel = self.bot.get_channel(FISHING_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(FISHING_CHANNEL_ID)
            except discord.HTTPException:
                return
        img_file, img_url = get_local_file(asset_path, os.path.basename(asset_path))
        if not img_file:
            return
        w_stats = WEATHER_EFFECTS[weather_key]
        embed = discord.Embed(
            title=f"🌊 O clima mudou: {w_stats['name']}",
            description=w_stats['desc'],
            color=discord.Color.red() if weather_key == "bad" else discord.Color.gold(),
        )
        embed.set_image(url=img_url)
        try:
            await channel.send(embed=embed, file=img_file)
        except discord.HTTPException:
            pass

    @weather_cycle.before_loop
    async def before_weather_cycle(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def market_cycle(self):
        cursor = self.bot.db_conn.cursor()
        cursor.execute(
            """
            UPDATE market_prices
            SET current_price = CAST(current_price * 0.95 AS INTEGER),
                last_updated = datetime('now')
            WHERE fish_name IN (
                SELECT fish_name FROM fish_sales_history
                WHERE sale_time > datetime('now', '-1 day')
                GROUP BY fish_name
                ORDER BY COUNT(*) DESC LIMIT 5
            )
            """
        )
        cursor.execute(
            """
            UPDATE market_prices
            SET current_price = CAST(current_price * 1.10 AS INTEGER),
                last_updated = datetime('now')
            WHERE fish_name NOT IN (
                SELECT DISTINCT fish_name FROM fish_sales_history
                WHERE sale_time > datetime('now', '-1 day')
            )
            """
        )
        self.bot.db_conn.commit()
        print("[MERCADO] Preços de peixes atualizados.")

    @market_cycle.before_loop
    async def before_market_cycle(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(EconomiaCog(bot))
