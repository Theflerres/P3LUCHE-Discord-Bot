
"""
Economia Scrap Seas — pescaria, loja, guilda, exploração, AFK traps e clima.
"""
import asyncio
import json
import os
import random
import re
from collections import Counter
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import get_bot_instance, set_bot_instance
from utils import get_local_file, log_to_gui

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
        "cd": 1.0, "trash": 60, "luck": 1.0,
        "desc": "A clássica. Conffiável e humilde."
    },
    "vara_treino": {
        "name": "Vara de Treino", 
        "price": 250, 
        "tier": 0, 
        "cd": 1.2, "trash": 40, "luck": 1.0,
        "desc": "Um pouco lenta, mas ensina a não pegar lixo."
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
        "price": 150000, 
        "tier": 5, # UPGRADE DE TIER!
        "cd": 0.5, "trash": 5, "luck": 4.0,
        "desc": "Pesca em realidades paralelas simultaneamente."
    },
    "vara_void": { # NOVA VARA
        "name": "Devoradora do Vazio", 
        "price": 500000, 
        "tier": 5, 
        "cd": 0.8, "trash": 0, "luck": 6.6,
        "desc": "Olhe para ela e ela olhará de volta..."
    }
}

# 2. CONFIGURAÇÃO DA LOJA E ITENS
SHOP_ITEMS = {
    # --- CONSUMÍVEIS (Buffs para Pesca) ---
    "isca": {"name": "Isca Minhoca", "price": 50, "type": "consumable", "rarity": "common", "desc": "Reduz lixo pela metade (+Valor)."},
    "energetico": {"name": "Energético", "price": 150, "type": "buff", "rarity": "common", "desc": "Reseta cooldown imediatamente."},
    "rede": {"name": "Rede de Mão", "price": 400, "type": "consumable", "rarity": "uncommon", "desc": "Pega 3 itens de uma vez (Consumível)."},
    "caixa_misteriosa": {"name": "Caixa Misteriosa", "price": 500, "type": "box", "rarity": "rare", "desc": "Pode ter dinheiro, itens ou nada."},
    
    # Novos Consumíveis (Para funcionar com o novo comando /pescar)
    "ima_saches": {"name": "Ímã de Sachês", "price": 300, "type": "buff", "rarity": "uncommon", "desc": "Duplica o valor da próxima pesca."},
    "firewall": {"name": "Firewall Portátil", "price": 100, "type": "buff", "rarity": "common", "desc": "Bloqueia 100% de Lixo na próxima pesca."},
    "chip_sorte": {"name": "Chip da Sorte", "price": 6000, "type": "buff", "desc": "Consumível. Se vier peixe Comum, tenta de novo.", "rarity": "legendary"},

    # --- VARAS (Sincronizado com ROD_STATS) ---
    # TIER 0 & 1
    "vara_treino":   {"name": "Vara de Treino", "price": 250, "type": "rod", "key": "vara_treino", "tier": 0, "rarity": "common", "desc": "Para iniciantes aprenderem."},
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
    "vara_quantum":  {"name": "Vara Quântica",  "price": 150000, "type": "rod", "key": "vara_quantum", "tier": 5, "rarity": "mythic", "desc": "Multidimensional."},
    "vara_void":     {"name": "Devoradora do Vazio", "price": 500000, "type": "rod", "key": "vara_void", "tier": 5, "rarity": "mythic", "desc": "O fim da pescaria."},

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

# 3. LISTA DE PEIXES (SUPER EXPANDIDA)
# Estrutura: (Nome, PreçoMin, PreçoMax, Emoji, Tier, Frase)
FISH_DB = [
    # --- TIER 0: LIXO & COMUM (O Início Humilde) ---
    ("Bota Velha", 0, 5, "👢", 0, "Alguém foi embora pulando."), 
    ("Lata Vazia", 2, 5, "🥫", 0, "Recicle, por favor."),
    ("Pneu Furado", 0, 8, "🍩", 0, "Furou o rolê."),
    ("Sacola Plástica", 0, 2, "🥡", 0, "Isso mata as tartarugas!"),
    ("Espinha de Peixe", 0, 1, "🦴", 0, "Chegou tarde, o gato já comeu."),
    
    ("Sardinha", 10, 20, "🐟", 0, "O pão de cada dia."), 
    ("Lambari", 10, 15, "🐠", 0, "Pequeno e crocante."),
    ("Tilápia", 15, 25, "🐟", 0, "Clássico do pesque-pague."),
    ("Peixe Dourado", 18, 28, "🐡", 0, "Fugiu do aquário."),
    ("Bagre", 20, 30, "🐟", 0, "Cuidado com o bigode."),

    # --- TIER 1: INCOMUM (Comida Boa) ---
    ("Truta", 40, 60, "🐟", 1, "Gosta de águas geladas."),
    ("Tambaqui", 50, 80, "🐟", 1, "O gigante redondo dos rios."),
    ("Lula", 60, 90, "🦑", 1, "Anéis empanados... hmmm."),
    ("Camarão", 35, 55, "🦐", 1, "A cabeça você joga fora."),
    ("Caranguejo", 45, 75, "🦀", 1, "Andando de lado."),
    ("Polvo", 70, 100, "🐙", 1, "8 braços para te dar um tapa."),
    ("Baiacu", 40, 70, "🐡", 1, "Não coma se não souber limpar!"),
    ("Piranha", 45, 65, "🦷", 1, "Ela queria seu dedo."),
    ("Tucunaré", 55, 85, "🐠", 1, "Lutador dos rios brasileiros."),

    # --- TIER 2: RARO (Exóticos & Bonitos) ---
    ("Peixe-Palhaço", 120, 180, "🤡", 2, "Procurando o filho dele..."),
    ("Dourado do Mar", 150, 250, "🐬", 2, "Brilha como ouro puro."),
    ("Arraia", 180, 280, "🛸", 2, "A nave espacial do mar."),
    ("Cavalo-Marinho", 200, 300, "🎠", 2, "O pai é quem engravida."),
    ("Enguia Elétrica", 220, 320, "⚡", 2, "BZZZ! Cuidado com o choque!"),
    ("Tubarão Martelo", 250, 400, "🔨", 2, "Pregos não inclusos."),
    ("Peixe-Espada", 300, 450, "⚔️", 2, "En Garde, marinheiro!"),
    ("Moreia", 160, 220, "🐍", 2, "Parece cobra, mas morde mais."),
    ("Axolote", 190, 290, "🦎", 2, "Ele se regenera!"),
    ("Peixe-Lanterna", 210, 310, "💡", 2, "Luz natural do abismo."),

    # --- TIER 3: LENDÁRIO (Pesos Pesados) ---
    ("Tubarão Branco", 1000, 1500, "🦈", 3, "Precisamos de um barco maior."),
    ("Baleia Azul", 2000, 3000, "🐋", 3, "O maior animal da Terra."),
    ("Lula Gigante", 2500, 3500, "🦑", 3, "Pesadelo dos marinheiros antigos."),
    ("Narval", 1800, 2600, "🦄", 3, "O unicórnio dos mares."),
    ("Orca", 2200, 3200, "🐼", 3, "A baleia assassina (que é um golfinho)."),
    ("Megalodon", 4000, 6000, "🦖", 3, "Achou que estava extinto? Achou errado."),
    ("Moby Dick", 5000, 7000, "🐳", 3, "A obsessão do Capitão Ahab."),
    ("Peixe-Lua", 1500, 2500, "🌑", 3, "Parece uma panqueca gigante."),

    # --- TIER 4: MÍTICO / CÓSMICO (Eventos & Memes) ---
    ("Kraken", 8000, 12000, "🐙🔥", 4, "LIBEREM O KRAKEN!"),
    ("Leviatã", 10000, 15000, "🐉", 4, "A serpente do fim do mundo."),
    ("Nessie", 12000, 18000, "🦕", 4, "O Monstro do Lago Ness é real?!"),
    ("Sereia", 15000, 25000, "🧜‍♀️", 4, "Cuidado com o canto dela..."),
    ("Godzilla (Aquático)", 20000, 30000, "🦖☢️", 4, "O Rei dos Monstros acordou."),
    ("CTHULHU", 50000, 66666, "🐙💀", 4, "Ph'nglui mglw'nafh R'lyeh..."),
    
    # Easter Eggs (Raros dentro do Raro)
    ("Bob Esponja", 5000, 8000, "🧽", 4, "Vive num abacaxi."),
    ("Peixe de 3 Olhos", 6000, 9000, "☢️", 4, "Direto de Springfield."),
    ("Peixe Cibernético", 15000, 20000, "🤖", 4, "Veio do ano 3077."),
    ("O PEIXE DOURADO SUPREMO", 40000, 60000, "👑", 4, "O deus de todos os aquários.")
]

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
        "vara_void", "admin_item" # Outros itens proibidos
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

        cursor = get_bot_instance().db_conn.cursor()
        row = cursor.execute("SELECT wallet, inventory FROM economy WHERE user_id = ?", (inter.user.id,)).fetchone()
        
        if not row: return await inter.response.send_message("❌ Crie conta com /eco pescar.", ephemeral=True)
            
        wallet = row['wallet']
        inv = json.loads(row['inventory']) if row['inventory'] else {}
        tipo = item_stats.get('type')

        # ROTA A: VARAS (Compra 1x Direto e Salva no Inventário)
        if tipo == 'rod':
            custo = item_stats['price']
            if wallet < custo: return await inter.response.send_message(f"💸 Falta grana ({custo}).", ephemeral=True)
            
            # Adiciona na mochila (inv) E equipa (current_rod)
            inv[item_key] = 1 
            cursor.execute("UPDATE economy SET wallet = wallet - ?, inventory = ?, current_rod = ? WHERE user_id = ?", (custo, json.dumps(inv), item_key, inter.user.id))
            get_bot_instance().db_conn.commit()
            
            await inter.response.send_message(f"🎣 **Compra Efetuada!**\n**{item_stats['name']}** foi adicionada à mochila e equipada.", ephemeral=True)

        # ROTA B: CONSUMÍVEIS (Abre Modal de Quantidade)
        else:
            modal = CompraQuantidadeModal(item_key, item_stats, wallet, inv, inter.user.id, get_bot_instance())
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
    cursor = get_bot_instance().db_conn.cursor()
    row = cursor.execute("SELECT wallet, inventory FROM economy WHERE user_id = ?", (user_id,)).fetchone()
    
    if not row: return await interaction.response.send_message("❌ Use /eco pescar primeiro.", ephemeral=True)
    if row['wallet'] < price: return await interaction.response.send_message(f"💸 Sem saldo ({row['wallet']} < {price}).", ephemeral=True)

    # --- COMPRA E ARMAZENAMENTO ---
    new_wallet = row['wallet'] - price
    
    # Carrega mochila atual
    try:
        inv = json.loads(row['inventory']) if row['inventory'] else {}
    except (json.JSONDecodeError, TypeError) as e:
        log_to_gui(f"Erro ao carregar inventário na compra: {e}", "WARNING")
        inv = {}

    # Adiciona o item na mochila (SOMA +1)
    inv[item] = inv.get(item, 0) + 1

    # Se for isca, também atualiza a coluna legacy 'baits' para compatibilidade com outras rotinas
    baits_add = 1 if item == 'isca' else 0

    # Salva no banco (atualiza wallet, inventory e incrementa baits se necessário)
    if baits_add:
        cursor.execute("UPDATE economy SET wallet = ?, inventory = ?, baits = COALESCE(baits, 0) + ? WHERE user_id = ?", (new_wallet, json.dumps(inv), baits_add, user_id))
    else:
        cursor.execute("UPDATE economy SET wallet = ?, inventory = ? WHERE user_id = ?", (new_wallet, json.dumps(inv), user_id))
    get_bot_instance().db_conn.commit()

    # Mensagem de confirmação
    emoji_tipo = "🎒"
    if data['type'] == 'rod': emoji_tipo = "🎣"
    if data['type'] == 'buff': emoji_tipo = "⚡"
    
    await interaction.response.send_message(f"✅ **Compra realizada!**\n{emoji_tipo} **{data['name']}** foi guardado na sua mochila.\nUse `/eco saldo` para ver ou usar.", ephemeral=True)

@eco_group.command(name="pescar", description="Pesca usando itens da sua mochila.")
async def pescar(interaction: discord.Interaction):
    # Defer para evitar erro de tempo limite
    try:
        await interaction.response.defer()
    except (discord.NotFound, discord.HTTPException) as e:
        log_to_gui(f"interaction.defer() falhou: {e}", "WARNING")

    user_id = interaction.user.id
    cursor = get_bot_instance().db_conn.cursor()
    
    # 1. BUSCA DADOS COMPLETOS
    row = cursor.execute("""
        SELECT e.last_fish, e.wallet, e.fish_count, e.rod_tier, e.current_rod, e.inventory, e.baits, e.guild_rank, e.guild_xp,
               e.rod_upgrades, e.scrap,
               q.current_chapter
        FROM economy e
        LEFT JOIN quest_progress q ON e.user_id = q.user_id
        WHERE e.user_id = ?
    """, (user_id,)).fetchone()
    
    if not row:
        cursor.execute("INSERT INTO economy (user_id, user_name) VALUES (?, ?)", (user_id, interaction.user.name))
        get_bot_instance().db_conn.commit()
        return await interaction.followup.send("🆕 Conta criada! Tente pescar novamente.", ephemeral=True)

    # 2. CARREGA INVENTÁRIO E VARA
    try:
        inv = json.loads(row['inventory']) if row['inventory'] else {}
    except (json.JSONDecodeError, TypeError):
        inv = {}

    current_rod_key = row['current_rod'] if row['current_rod'] else 'vara_bambu'
    if current_rod_key not in ROD_STATS: current_rod_key = 'vara_bambu'
    rod_data = ROD_STATS[current_rod_key]
    
    # 3. CARREGA UPGRADES
    try:
        upgrades = json.loads(row['rod_upgrades']) if row['rod_upgrades'] else {"luck": 0, "cd": 0}
    except (json.JSONDecodeError, TypeError):
        upgrades = {"luck": 0, "cd": 0}

    luck_bonus = 1 + (upgrades.get("luck", 0) * 0.10) # +10% por nível
    cd_reduction = 1 - (upgrades.get("cd", 0) * 0.05) # -5% por nível
    
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

    # 5. CONSUMO DE ITENS
    used_bait = False; used_magnet = False; used_firewall = False; used_chip = False
    legacy_baits = row['baits'] if row['baits'] else 0
    
    # Consome isca
    if inv.get("isca", 0) > 0: 
        inv["isca"] -= 1
        if legacy_baits > 0: legacy_baits -= 1
        used_bait = True
    elif legacy_baits > 0: 
        legacy_baits -= 1
        used_bait = True
    
    if inv.get("isca", 0) <= 0: inv.pop("isca", None)

    # Consome outros itens
    if inv.get("firewall", 0) > 0: inv["firewall"] -= 1; used_firewall = True
    if inv.get("firewall", 0) <= 0: inv.pop("firewall", None)
    
    if inv.get("chip_sorte", 0) > 0: inv["chip_sorte"] -= 1; used_chip = True
    if inv.get("chip_sorte", 0) <= 0: inv.pop("chip_sorte", None)

    # ==========================================================
    # 6. PESCA (RNG + CLIMA ATUALIZADO)
    # ==========================================================
    
    # Pega o clima atual do banco
    w_key, w_stats = get_current_weather()
    
    # Modifica chance de lixo com base no clima
    trash_chance = rod_data['trash'] * w_stats['trash_mod']
    
    if used_bait: trash_chance /= 2
    if used_firewall: trash_chance = 0
    
    roll = random.randint(1, 100)
    
    pool = []
    if used_chip: 
        pool = [p for p in FISH_DB if p[4] >= 2]
        if not pool: pool = [p for p in FISH_DB if p[4] > 0]
    elif roll <= trash_chance: 
        pool = [p for p in FISH_DB if p[4] == 0] # Lixo
        if not pool: pool = [("Bota Velha", 0, 5, "👢", 0, "Que nojo!")]
    else: 
        # Modifica o Tier Máximo com base no bônus do clima
        max_tier_possible = rod_data['tier'] + w_stats['tier_bonus']
        pool = [p for p in FISH_DB if p[4] <= max_tier_possible and p[4] > 0]
        if not pool: pool = [p for p in FISH_DB if p[4] == 0]

    catch_data = random.choice(pool)
    nome, v_min, v_max, emoji, tier_p, frase = catch_data
    
    # Cálculo de Valor (Aplicando Clima)
    TRASH_ITEMS = ["Bota Velha", "Lata Vazia", "Alga", "Pilha Velha"]
    is_trash = nome in TRASH_ITEMS

    # Fórmula: ValorBase * SorteVara * Upgrade * Clima
    base_val = int(random.randint(v_min, v_max) * rod_data['luck'] * luck_bonus * w_stats['luck_mod'])
    if used_bait: base_val = int(base_val * 1.5)

    valor = 0
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
                    reward_money = m_data['reward']
                    reward_xp = m_data['xp']
                    
                    members_ids = json.loads(my_party['members_json'])
                    members_ids.append(my_party['leader_id'])
                    
                    for member_id in set(members_ids):
                        cursor.execute("UPDATE economy SET wallet = wallet + ?, guild_xp = guild_xp + ? WHERE user_id = ?", (reward_money, reward_xp, member_id))
                    
                    cursor.execute("UPDATE parties SET active_mission_id = NULL, mission_progress = 0 WHERE leader_id = ?", (my_party['leader_id'],))
                    mission_msg = f"\n🎉 **MISSÃO CUMPRIDA!**\nGrupo completou: **{m_data['title']}**\nPrêmio: 💰 {reward_money} | ⭐ {reward_xp} XP!"

    # 8. XP DE GUILDA E RANK UP
    xp_ganho = 0
    if row['current_chapter'] in ['acesso_liberado', 'city_spotted', 'garrafa_encontrada']:
        xp_table = {0: 2, 1: 10, 2: 25, 3: 100, 4: 500}
        xp_ganho = xp_table.get(tier_p, 2)

    novo_saldo = row['wallet'] + valor
    new_xp_total = (row['guild_xp'] or 0) + xp_ganho
    
    current_rank = row['guild_rank'] if row['guild_rank'] else 'F'
    rank_info = GUILD_RANKS.get(current_rank, GUILD_RANKS['F'])
    
    if rank_info['next'] and new_xp_total >= rank_info['req_xp']:
        if rank_info['next'] == 'S':
             pass
        else:
            current_rank = rank_info['next']
            new_xp_total -= rank_info['req_xp']
            mission_msg += f"\n🌟 **RANK UP!** Agora você é Rank {current_rank}!"

    # 9. QUEST DA GARRAFA
    quest_trigger = False
    prev_fish_count = row['fish_count'] if row['fish_count'] else 0
    new_fish_count = prev_fish_count + 1
    already_has = inv.get('garrafa_incrustada', 0) > 0

    if not already_has:
        with CATCHES_LOCK:
            CATCHES_SINCE_RESTART[user_id] = CATCHES_SINCE_RESTART.get(user_id, 0) + 1
            session_count = CATCHES_SINCE_RESTART[user_id]

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
            if not row['current_chapter'] or row['current_chapter'] == 'inicio':
                cursor.execute("UPDATE quest_progress SET current_chapter = 'garrafa_encontrada' WHERE user_id = ?", (user_id,))
            else:
                cursor.execute("INSERT OR IGNORE INTO quest_progress (user_id, current_chapter) VALUES (?, 'garrafa_encontrada')", (user_id,))
                
    # 10. SALVA NO BANCO
    if used_bait:
        if inv.get("isca", 0) <= 0: inv.pop("isca", None)
    
    cursor.execute("""
        UPDATE economy SET 
        wallet = ?, last_fish = ?, fish_count = fish_count + 1, 
        inventory = ?, baits = ?, guild_xp = ?, guild_rank = ?, user_name = ?
        WHERE user_id = ?
    """, (novo_saldo, agora_str, json.dumps(inv), legacy_baits, new_xp_total, current_rank, interaction.user.name, user_id))
    get_bot_instance().db_conn.commit()

    # 11. GERA EMBED FINAL (Visual Intacto)
    
    embed_color = discord.Color.from_rgb(46, 204, 113) # Verde estilo Matrix
    if tier_p == 0: embed_color = discord.Color.light_grey()
    if tier_p == 2: embed_color = discord.Color.blue()
    if tier_p >= 3: embed_color = discord.Color.purple()

    embed = discord.Embed(title=f"{emoji} P3LUCHE Fishing OS", color=embed_color)
    
    # Campo 1: Capturado
    embed.add_field(name="Capturado:", value=f"**{emoji} {nome}**", inline=False)
    
    # Campo 2: Frase
    embed.add_field(name="P3LUCHE diz:", value=f"*{frase}*", inline=False)
    
    # Campo 3: Detalhes
    cd_minutos = int(actual_cd / 60) if 'actual_cd' in locals() else int(rod_data['cd'] * 5)
    stats_info = f"**{rod_data['name']}**\n(⏱️ {cd_minutos}m | 🎲 x{rod_data['luck']})"
    embed.add_field(name="Detalhes", value=stats_info, inline=True)

    # Campo 4: Lucro Visual
    lucro_visual = f"```diff\n+ {valor} Sachês\n```"
    embed.add_field(name="Lucro", value=lucro_visual, inline=True)
    
    # Rodapé com Clima e Saldo
    iscas_restantes = inv.get("isca", 0)
    weather_icon = "☀️" if w_key == "normal" else ("⛈️" if w_key == "bad" else "✨")
    embed.set_footer(text=f"Saldo: {novo_saldo} | Iscas: {iscas_restantes} | Clima: {weather_icon} {w_stats['name']}")

    if mission_msg:
        embed.description = f"{mission_msg}" 
        if mission_completed: embed.color = discord.Color.gold()

    if quest_trigger:
        bottle_msg = "\n🧴 **Você encontrou uma Garrafa Incrustada!** Use /ler_garrafa para ver o conteúdo."
        if embed.description: embed.description = f"{embed.description}{bottle_msg}"
        else: embed.description = bottle_msg

    await interaction.followup.send(embed=embed)

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
    cursor = get_bot_instance().db_conn.cursor()
    
    # 1. VERIFICAÇÕES BÁSICAS
    row = cursor.execute("SELECT wallet, last_explore FROM economy WHERE user_id = ?", (user_id,)).fetchone()
    if not row: return await interaction.response.send_message("❌ Crie uma conta pescando primeiro.", ephemeral=True)

    quest = cursor.execute("SELECT current_chapter, inventory FROM quest_progress WHERE user_id = ?", (user_id,)).fetchone()
    city_spotted = quest and quest['current_chapter'] not in ['inicio', 'locked', None]
    
    # 2. DECISÃO (VIEW DE ESCOLHA)
    modo_exploracao = "farm"
    
    # Se já viu a cidade, pergunta pra onde quer ir
    if city_spotted:
        view = ExplorationView(user_id)
        await interaction.response.send_message("📡 **Painel de Controle do Drone:** Escolha o destino.", view=view, ephemeral=True)
        await view.wait()
        if view.choice is None: return # Se o tempo acabar ou não escolher
        modo_exploracao = view.choice
    else:
        # Se não viu a cidade, vai direto farmar na ilha
        await interaction.response.defer()

    # 3. CUSTO E COOLDOWN
    custo = 80
    agora = datetime.now()
    agora_str = agora.strftime("%Y-%m-%d %H:%M:%S.%f")
    
    # Lógica de Cooldown (10 min)
    if row['last_explore']:
        try:
            last_exp = datetime.strptime(row['last_explore'], "%Y-%m-%d %H:%M:%S.%f")
            if (agora - last_exp).total_seconds() < 600:
                ts = int((last_exp + timedelta(minutes=10)).timestamp())
                msg = f"⏳ **Drone Recarregando!** <t:{ts}:R>."
                if city_spotted: return await interaction.followup.send(msg, ephemeral=True)
                else: return await interaction.followup.send(msg, ephemeral=True)
        except: pass

    # Verifica Saldo
    if row['wallet'] < custo:
        msg = f"🔋 Precisa de {custo} Sachês para operar o drone."
        if city_spotted: return await interaction.followup.send(msg, ephemeral=True)
        else: return await interaction.followup.send(msg, ephemeral=True)

    # 4. COBRA O CUSTO
    cursor.execute("UPDATE economy SET wallet = wallet - ? WHERE user_id = ?", (custo, user_id))

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
                rw, rx = m_data['reward'], m_data['xp']
                mems = json.loads(my_party['members_json']) + [my_party['leader_id']]
                for mid in set(mems):
                    cursor.execute("UPDATE economy SET wallet=wallet+?, guild_xp=guild_xp+? WHERE user_id=?", (rw, rx, mid))
                cursor.execute("UPDATE parties SET active_mission_id=NULL, mission_progress=0 WHERE leader_id=?", (my_party['leader_id'],))
                mission_msg = f"\n🎉 **Missão Completa!** Ganharam {rw} Sachês!"

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
            cursor.execute("UPDATE economy SET last_explore = NULL WHERE user_id = ?", (user_id,)) # Reembolsa
            get_bot_instance().db_conn.commit()
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
    
    if valor_ganho > 0:
        cursor.execute("UPDATE economy SET wallet = wallet + ?, last_explore = ? WHERE user_id = ?", (valor_ganho, agora_str, user_id))
        msg += f"\n\n💰 **Resultado:** +{valor_ganho} Sachês (Lucro: {valor_ganho - custo})"
        cor = discord.Color.green()
    
    elif item_ganho:
        inv_atual = {}
        cursor.execute("SELECT inventory FROM economy WHERE user_id = ?", (user_id,))
        curr_inv_json = cursor.fetchone()[0]
        if curr_inv_json:
             try: inv_atual = json.loads(curr_inv_json)
             except: pass
        
        if item_ganho == "caixa_misteriosa":
             inv_atual["caixa_misteriosa"] = inv_atual.get("caixa_misteriosa", 0) + 1
             msg += f"\n\n📦 **Loot Raro!** Você achou uma Caixa Misteriosa."
             cor = discord.Color.gold()
        elif item_ganho == "energetico":
             cursor.execute("UPDATE economy SET last_fish = NULL WHERE user_id = ?", (user_id,))
             msg += f"\n\n⚡ **Energia Pura!** Seu cooldown de PESCA foi resetado."
             cor = discord.Color.blue()
        
        cursor.execute("UPDATE economy SET inventory = ?, last_explore = ? WHERE user_id = ?", (json.dumps(inv_atual), agora_str, user_id))

    else:
        cursor.execute("UPDATE economy SET last_explore = ? WHERE user_id = ?", (agora_str, user_id))
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

@eco_group.command(name="presentear", description="Dê um item a um amigo.")
async def presentear(interaction: discord.Interaction, amigo: discord.Member, item: str):
    if amigo.id == interaction.user.id: return await interaction.response.send_message("🎁 Use /eco comprar.", ephemeral=True)
    if item not in SHOP_ITEMS: return await interaction.response.send_message("❌ Item inválido.", ephemeral=True)
    
    data = SHOP_ITEMS[item]
    price = data['price']
    cursor = get_bot_instance().db_conn.cursor()
    sender = cursor.execute("SELECT wallet FROM economy WHERE user_id = ?", (interaction.user.id,)).fetchone()
    if not sender or sender['wallet'] < price: return await interaction.response.send_message(f"💸 Falta grana ({price}).", ephemeral=True)

    receiver = cursor.execute("SELECT rod_tier, inventory FROM economy WHERE user_id = ?", (amigo.id,)).fetchone()
    if not receiver:
        cursor.execute("INSERT INTO economy (user_id, user_name) VALUES (?, ?)", (amigo.id, amigo.name))
        receiver = {'rod_tier': 0, 'inventory': '{}'}

    msg = ""
    if data['type'] == 'rod':
        if receiver['rod_tier'] >= data['tier']: return await interaction.response.send_message(f"⚠️ {amigo.name} já tem vara melhor.", ephemeral=True)
        cursor.execute("UPDATE economy SET rod_tier = ?, current_rod = ? WHERE user_id = ?", (data['tier'], data['key'], amigo.id))
        msg = f"🎣 **Presente:** {data['name']} entregue!"
    elif data['type'] == 'flex':
        try: inv = json.loads(receiver['inventory']) if receiver['inventory'] else {}
        except: inv = {}
        inv[data['name']] = inv.get(data['name'], 0) + 1
        cursor.execute("UPDATE economy SET inventory = ? WHERE user_id = ?", (json.dumps(inv), amigo.id))
        msg = f"💎 **Luxo:** {data['name']} entregue!"
    else: return await interaction.response.send_message("❌ Só pode dar Varas ou Flex.", ephemeral=True)

    cursor.execute("UPDATE economy SET wallet = wallet - ? WHERE user_id = ?", (price, interaction.user.id))
    get_bot_instance().db_conn.commit()
    await interaction.response.send_message(f"🎁 **Enviado!**\n{msg}")

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

        # Atualiza no Banco de Dados
        cursor = get_bot_instance().db_conn.cursor()

        # Garante que a vara equipada esteja presente no inventário (não deve 'sumir')
        try:
            row = cursor.execute("SELECT inventory FROM economy WHERE user_id = ?", (self.user_id,)).fetchone()
            inv = json.loads(row['inventory']) if row and row['inventory'] else {}
        except:
            inv = {}

        # Se equipando uma vara comprada, assegura que o item exista no inventário
        if new_rod != 'vara_bambu':
            if inv.get(new_rod, 0) <= 0:
                inv[new_rod] = 1
                cursor.execute("UPDATE economy SET inventory = ? WHERE user_id = ?", (json.dumps(inv), self.user_id))

        cursor.execute("UPDATE economy SET current_rod = ? WHERE user_id = ?", (new_rod, self.user_id))
        get_bot_instance().db_conn.commit()

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
        cursor = get_bot_instance().db_conn.cursor()
        
        # Recarrega inventário para garantir que não houve dupe
        row = cursor.execute("SELECT inventory, wallet, last_fish_time FROM economy WHERE user_id = ?", (user_id,)).fetchone()
        if not row: return
        
        inv = json.loads(row['inventory'])
        wallet = row['wallet']
        
        if inv.get(item_key, 0) <= 0:
            return await interaction.response.send_message("❌ Você não tem mais este item.", ephemeral=True)

        msg = "" 
        item_data = SHOP_ITEMS.get(item_key, {})
        
        # --- LÓGICA DE USO DOS ITENS ---

        # 1. ENERGÉTICO (Reseta Cooldown)
        if item_key == "energetico":
            inv[item_key] -= 1
            # Reseta o tempo de pesca definindo para o passado distante
            cursor.execute("UPDATE economy SET last_fish_time = ? WHERE user_id = ?", (datetime.min.isoformat(), user_id))
            msg = "⚡ **Energético bebido!** Você está pilhado! O tempo de espera da pesca foi zerado."

        # 2. CAIXA MISTERIOSA (Sorteio)
        elif item_key == "caixa_misteriosa":
            inv[item_key] -= 1
            premio = random.randint(100, 1000)
            wallet += premio
            cursor.execute("UPDATE economy SET wallet = ? WHERE user_id = ?", (wallet, user_id))
            msg = f"🎁 **Caixa Aberta!** Você encontrou 💰 **{premio} Sachês** dentro dela."

        # 3. REDE DE MÃO (Pesca 3 itens aleatórios instantâneos)
        elif item_key == "rede":
            inv[item_key] -= 1
            # Sorteia 3 recompensas simples (dinheiro) para simular pesca
            lucro_rede = 0
            for _ in range(3):
                val = random.randint(10, 50)
                lucro_rede += val
            
            wallet += lucro_rede
            cursor.execute("UPDATE economy SET wallet = ? WHERE user_id = ?", (wallet, user_id))
            msg = f"🕸️ **Rede lançada!** Você puxou um monte de tralha e peixes pequenos, lucrando 💰 **{lucro_rede} Sachês**."

        # 4. BUFFS NOVOS (Ímã, Firewall, Chip)
        elif item_key in ["ima_saches", "firewall", "chip_sorte"]:
            msg = f"ℹ️ **{item_data['name']}**: Este item é passivo! Mantenha ele no inventário e ele será usado automaticamente na próxima pescaria."

        # 5. ISCAS (Avisar que é automático)
        elif item_key == "isca":
             msg = "🪱 **Isca:** Mantenha na mochila. Ela é usada automaticamente quando você pesca para reduzir o lixo."

        # 6. ELSE (Segurança)
        else:
            msg = f"❓ O item **{item_data.get('name', item_key)}** não pode ser usado através deste menu."

        # Salva alterações (se gastou algo)
        if inv.get(item_key, 0) <= 0 and item_key in inv:
            del inv[item_key] 
            
        cursor.execute("UPDATE economy SET inventory = ? WHERE user_id = ?", (json.dumps(inv), user_id))
        get_bot_instance().db_conn.commit()

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
    cursor = get_bot_instance().db_conn.cursor()
    row = cursor.execute("SELECT wallet, fish_count, current_rod, baits, inventory FROM economy WHERE user_id = ?", (target.id,)).fetchone()
    
    if not row:
        return await interaction.response.send_message("❌ Usuário sem conta bancária. Use /eco pescar primeiro!", ephemeral=True)

    # --- 1. PROCESSA INVENTÁRIO (VISUAL) ---
    inv_text = "Mochila vazia."
    try:
        inv = json.loads(row['inventory']) if row['inventory'] else {}
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
    rod_key = row['current_rod'] if row['current_rod'] else "vara_bambu"
    if rod_key not in ROD_STATS: rod_key = "vara_bambu"
    rod_data = ROD_STATS[rod_key]

    # --- 3. MONTA O EMBED ---
    embed = discord.Embed(color=discord.Color.from_rgb(47, 49, 54))
    embed.set_author(name=f"Inventário de {target.name}", icon_url="https://cdn-icons-png.flaticon.com/512/3081/3081840.png")
    
    if target.avatar: embed.set_thumbnail(url=target.avatar.url)
    
    embed.add_field(name="💳 Finanças", value=f"💰 **{row['wallet']}** Sachês\n🐟 **{row['fish_count']}** Peixes", inline=False)
    
    stats_str = f"⏱️ CD: {int(rod_data['cd']*5)}m | 🎲 Sorte: x{rod_data['luck']}"
    embed.add_field(name="🎣 Equipado", value=f"**{rod_data['name']}**\n*{stats_str}*\n🪱 **{row['baits']}** Iscas", inline=False)
    
    embed.add_field(name="🎒 Mochila", value=inv_text, inline=False)
    
    # --- 4. LÓGICA DO MENU DE EQUIPAR ---
    view = None
    # O menu só aparece se você estiver olhando seu próprio saldo
    if target.id == interaction.user.id:
        # Recupera inventário para ver quais varas o jogador tem
        try: inv_data = json.loads(row['inventory']) if row['inventory'] else {}
        except: inv_data = {}
        
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

@eco_group.command(name="diario", description="Resgate diário.")
async def diario(interaction: discord.Interaction):
    user_id = interaction.user.id
    cursor = get_bot_instance().db_conn.cursor()
    row = cursor.execute("SELECT last_daily FROM economy WHERE user_id = ?", (user_id,)).fetchone()
    agora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    
    if row and row['last_daily']:
        try:
            last = datetime.strptime(row['last_daily'], "%Y-%m-%d %H:%M:%S.%f")
            if (datetime.now() - last).total_seconds() < 86400:
                ts = int((last + timedelta(days=1)).timestamp())
                return await interaction.response.send_message(f"📅 Volte <t:{ts}:R>.", ephemeral=True)
        except: pass

    premio = random.randint(200, 500)
    cursor.execute("""
        INSERT INTO economy (user_id, user_name, wallet, last_daily) VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET wallet = wallet + ?, last_daily = ?, user_name = ?
    """, (user_id, interaction.user.name, premio, agora_str, premio, agora_str, interaction.user.name))
    get_bot_instance().db_conn.commit()
    await interaction.response.send_message(f"💰 **Diário!** +{premio} Sachês.")

@eco_group.command(name="rank", description="Hall da Fama.")
async def rank(interaction: discord.Interaction):
    cursor = get_bot_instance().db_conn.cursor()
    rows_m = cursor.execute("SELECT user_name, wallet FROM economy ORDER BY wallet DESC LIMIT 10").fetchall()
    rows_f = cursor.execute("SELECT user_name, fish_count FROM economy ORDER BY fish_count DESC LIMIT 10").fetchall()
    
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
    def __init__(self, user_id, user_rank):
        self.user_id = user_id
        
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

        super().__init__(
            placeholder="📜 Escolha a Missão Ativa de hoje...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
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
        cursor = get_bot_instance().db_conn.cursor()
        placeholders = ','.join('?' for _ in member_ids)
        # Traz apenas quem NÃO é o líder (não pode se auto-expulsar)
        query = f"SELECT user_id, user_name FROM economy WHERE user_id IN ({placeholders}) AND user_id != ?"
        rows = cursor.execute(query, (*member_ids, leader_id)).fetchall()
        
        options = []
        for r in rows:
            options.append(discord.SelectOption(
                label=r['user_name'], 
                value=str(r['user_id']), 
                description=f"ID: {r['user_id']}",
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
        row = cursor.execute("SELECT guild_rank, guild_xp FROM economy WHERE user_id = ?", (self.user_id,)).fetchone()
        rank = row['guild_rank'] if row and row['guild_rank'] else 'F'
        xp = row['guild_xp'] if row and row['guild_xp'] else 0

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
                erow = cursor.execute("SELECT inventory FROM economy WHERE user_id = ?", (self.user_id,)).fetchone()
                if erow and erow['inventory']:
                    try:
                        e_inv = json.loads(erow['inventory'])
                        if e_inv.get('selo_capitao'):
                            has_seal = True
                    except:
                        has_seal = False

            if has_seal and (not qrow or qrow.get('current_chapter') != 'acesso_liberado'):
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
                cursor = get_bot_instance().db_conn.cursor()

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
                    next_key = rdata['next']
                    if next_key:
                        req = rdata['req_xp']
                        embed = discord.Embed(title="📜 Informação de Rank", color=discord.Color.blue())
                        embed.add_field(name="Rank Atual", value=f"**{curr_rank}** - {rdata['title']}", inline=False)
                        embed.add_field(name="XP", value=f"{xp_val}/{req}", inline=False)
                        await interaction.response.edit_message(embed=embed, view=self.view)
                    else:
                        embed = discord.Embed(title="📜 Informação de Rank", description="Você já atingiu o rank máximo.", color=discord.Color.blue())
                        await interaction.response.edit_message(embed=embed, view=self.view)
                    return

                if choice == 'ask_promo':
                    # Recarrega os dados
                    row = cursor.execute("SELECT guild_rank, guild_xp FROM economy WHERE user_id = ?", (self.user_id,)).fetchone()
                    curr_rank = row['guild_rank'] if row and row['guild_rank'] else 'F'
                    xp_val = row['guild_xp'] if row and row['guild_xp'] else 0
                    rdata = GUILD_RANKS.get(curr_rank, GUILD_RANKS['F'])
                    next_key = rdata['next']
                    if not next_key:
                        await interaction.response.edit_message(embed=discord.Embed(description="⚠️ Você já está no Rank máximo."), view=self.view)
                        return

                    if xp_val >= rdata['req_xp']:
                        if next_key == 'S':
                            text = get_dialogue('jenna', 'rank_s_lock')
                            await interaction.response.edit_message(embed=discord.Embed(title="🛡️ Capitã Jenna", description=text, color=discord.Color.red()), view=self.view)
                            return
                        new_rank = next_key
                        new_xp = xp_val - rdata['req_xp']
                        cursor.execute("UPDATE economy SET guild_rank = ?, guild_xp = ? WHERE user_id = ?", (new_rank, new_xp, self.user_id))
                        get_bot_instance().db_conn.commit()
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

        cursor = get_bot_instance().db_conn.cursor()
        row = cursor.execute("SELECT guild_rank, guild_xp, fish_count FROM economy WHERE user_id = ?", (user_id,)).fetchone()
        
        if not row: 
            # Erro continua invisível pra não poluir o chat
            return await interaction.response.send_message("❌ Erro de registro. Use /eco pescar para criar conta.", ephemeral=True)

        current_rank = row['guild_rank'] if row['guild_rank'] else 'F'
        xp_atual = row['guild_xp'] if row['guild_xp'] else 0
        
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
        embed.add_field(name="🎣 Histórico", value=f"{row['fish_count']} Peixes", inline=True)
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
            placeholders = ','.join('?' for _ in members_ids)
            member_names = []
            if members_ids:
                rows = cursor.execute(f"SELECT user_name FROM economy WHERE user_id IN ({placeholders})", tuple(members_ids)).fetchall()
                member_names = [f"👤 {r['user_name']}" for r in rows]
            
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
        row = cursor.execute("SELECT guild_rank FROM economy WHERE user_id = ?", (self.user_id,)).fetchone()
        user_rank = row['guild_rank'] if row else "F"

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

        view_mission = discord.ui.View()
        view_mission.add_item(MissionSelect(self.user_id, user_rank))
        
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

# --- COMANDO PRINCIPAL: /GUILDA ---
@app_commands.command(name="guilda", description="Acessa o hub da Guilda (Perfil, Rank, Grupo).")
async def guilda(interaction: discord.Interaction):
    # 1. TRAVA DE ACESSO (O Portão da Cidade)
    user_id = interaction.user.id
    cursor = get_bot_instance().db_conn.cursor()
    quest = cursor.execute("SELECT current_chapter FROM quest_progress WHERE user_id = ?", (user_id,)).fetchone()
    
    # Verifica se tem acesso liberado (quest da garrafa concluída ou cidade descoberta e selo entregue)
    liberado = False
    if quest and quest['current_chapter'] in ['acesso_liberado', 'city_spotted']:
        # Se 'city_spotted' for suficiente para ver o menu (mas talvez não missões), libera.
        # Ajuste conforme sua lore. Aqui assumimos que 'acesso_liberado' é o ideal.
        liberado = True
        
    # Se quiser forçar liberação para testar, comente o if acima e descomente: liberado = True
    
    if not liberado:
        # Verifica se por acaso ele tem o rank (bug fix)
        eco = cursor.execute("SELECT guild_rank FROM economy WHERE user_id = ?", (user_id,)).fetchone()
        if eco and eco['guild_rank']: liberado = True

    if not liberado:
        return await interaction.response.send_message("🚫 **Acesso Negado.**\nOs guardas barram sua entrada.\n*\"Apenas membros credenciados. Vá falar com a Capitã Mara se tiver o Selo.\"*", ephemeral=True)

    # 2. SE ENTROU:
    # Garante que ele tem um Rank inicial no banco
    cursor.execute("INSERT OR IGNORE INTO economy (user_id, user_name, guild_rank) VALUES (?, ?, 'F')", (user_id, interaction.user.name))
    get_bot_instance().db_conn.commit()
    
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
        "wait_time": 00,       # 5 minutos esperando o peixe cair
        "reset_time": 120,      # 2 minutos para limpar/desembolar
        "loot_tier_max": 1      # Só pega peixe comum/incomum
    },
    "rede_industrial": {
        "name": "Rede de Arrasto", 
        "cost": 1500, 
        "repair_cost": 400,     # Caro, mas vale a pena pelos 15 peixes
        "capacity": 15,         # Pega MUITO peixe
        "break_chance": 80,     # 80% de chance de quebrar (Alto Risco)
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

# --- VIEW DA OFICINA DO GALDINO (RECICLAGEM & UPGRADES) ---
class GaldinoView(discord.ui.View):
    def __init__(self, user_id, user_name):
        super().__init__(timeout=180)
        self.user_id = user_id

    # --- BOTÃO 1: RECICLAGEM (Gera Sucata) ---
    @discord.ui.button(label="Reciclar Sucata", style=discord.ButtonStyle.success, emoji="♻️", row=0)
    async def recycle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cursor = get_bot_instance().db_conn.cursor()
        row = cursor.execute("SELECT inventory, scrap FROM economy WHERE user_id = ?", (self.user_id,)).fetchone()
        inv = json.loads(row['inventory']) if row['inventory'] else {}
        
        trash_list = ["Bota Velha", "Lata Vazia", "Alga", "Pilha Velha"]
        gain = 0
        for t in trash_list:
            if t in inv:
                gain += inv[t] * 5
                del inv[t]
        
        if gain > 0:
            cursor.execute("UPDATE economy SET inventory = ?, scrap = scrap + ? WHERE user_id = ?", (json.dumps(inv), gain, self.user_id))
            get_bot_instance().db_conn.commit()
            msg = f"🔧 **Galdino:** 'Isso sim é material!'\n⚙️ Ganhou: {gain} Sucata."
        else:
            msg = "🔧 **Galdino:** 'Sua mochila tá limpa demais. Suma daqui!'"
            
        await interaction.response.send_message(msg, ephemeral=True)

    # --- BOTÃO 2: TUNING DE VARA ---
    @discord.ui.button(label="Tunar Vara", style=discord.ButtonStyle.primary, emoji="🔫", row=0)
    async def tune_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # (O código do Tuning continua idêntico ao anterior, pode manter)
        # Vou resumir aqui para não ficar gigante, use o mesmo lógica do tuning que enviei antes.
        cursor = get_bot_instance().db_conn.cursor()
        row = cursor.execute("SELECT scrap, rod_upgrades, current_rod FROM economy WHERE user_id = ?", (self.user_id,)).fetchone()
        scrap = row['scrap']
        upgrades = json.loads(row['rod_upgrades']) if row['rod_upgrades'] else {"luck": 0, "cd": 0}
        
        luck_lvl = upgrades.get("luck", 0)
        cd_lvl = upgrades.get("cd", 0)
        cost_luck = (luck_lvl + 1) * 100
        cost_cd = (cd_lvl + 1) * 100
        
        embed = discord.Embed(title="🔧 Tuning de Equipamento", description=f"**Sucata Disponível:** ⚙️ {scrap}", color=discord.Color.orange())
        embed.add_field(name=f"🍀 Mira Laser [Lv {luck_lvl}]", value=f"Custo: ⚙️ {cost_luck}", inline=True)
        embed.add_field(name=f"⚡ Rolamentos [Lv {cd_lvl}]", value=f"Custo: ⚙️ {cost_cd}", inline=True)

        view = discord.ui.View()
        async def up_luck(inter):
            if scrap < cost_luck: return await inter.response.send_message("❌ Sucata insuficiente!", ephemeral=True)
            if luck_lvl >= 5: return await inter.response.send_message("⚠️ Max Level!", ephemeral=True)
            upgrades["luck"] += 1
            cursor.execute("UPDATE economy SET scrap = scrap - ?, rod_upgrades = ? WHERE user_id = ?", (cost_luck, json.dumps(upgrades), self.user_id))
            get_bot_instance().db_conn.commit()
            await inter.response.send_message("✅ Sorte aumentada!", ephemeral=True)
            
        async def up_cd(inter):
            if scrap < cost_cd: return await inter.response.send_message("❌ Sucata insuficiente!", ephemeral=True)
            if cd_lvl >= 5: return await inter.response.send_message("⚠️ Max Level!", ephemeral=True)
            upgrades["cd"] += 1
            cursor.execute("UPDATE economy SET scrap = scrap - ?, rod_upgrades = ? WHERE user_id = ?", (cost_cd, json.dumps(upgrades), self.user_id))
            get_bot_instance().db_conn.commit()
            await inter.response.send_message("✅ Cooldown reduzido!", ephemeral=True)

        b1 = discord.ui.Button(label="Upar Sorte", style=discord.ButtonStyle.success); b1.callback = up_luck
        b2 = discord.ui.Button(label="Upar CD", style=discord.ButtonStyle.primary); b2.callback = up_cd
        view.add_item(b1); view.add_item(b2)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # --- BOTÃO 3: EXAMINAR MÁQUINA (QUEST + GERENCIAMENTO HÍBRIDO) ---
    @discord.ui.button(label="Examinar Máquina", style=discord.ButtonStyle.secondary, emoji="🦀", row=1)
    async def trap_manager(self, interaction: discord.Interaction, button: discord.ui.Button):
        cursor = get_bot_instance().db_conn.cursor()
        row = cursor.execute("SELECT wallet, afk_trap, inventory FROM economy WHERE user_id = ?", (self.user_id,)).fetchone()
        
        trap_data = json.loads(row['afk_trap']) if row['afk_trap'] and row['afk_trap'] != "{}" else None
        wallet = row['wallet']
        inv = json.loads(row['inventory']) if row['inventory'] else {}

        embed = discord.Embed(title="🦀 Oficina de Armadilhas", color=discord.Color.dark_orange())

        # ==========================================================
        # FASE 1: QUEST (Se não tem armadilha, entra na Quest do Lixo)
        # ==========================================================
        if not trap_data:
            # Conta os lixos
            lixo_types = ["Lata Vazia", "Bota Velha", "Alga", "Pilha Velha"]
            total_trash = sum(inv.get(t, 0) for t in lixo_types)
            meta = 50

            if total_trash < meta:
                # Texto da Quest Incompleta
                intro_text = get_dialogue("galdino", "afk_machine_intro") # "Aquilo? Protótipos..."
                embed.description = f"{intro_text}\n\n📊 **Progresso:** {total_trash}/{meta} Lixos na mochila."
                embed.set_footer(text="Dica: Pesque lixo (Latas, Botas, Algas) para completar.")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                # Quest Completa -> Botão de Craftar
                embed.description = f"🔧 **Galdino:** 'Olha só! {total_trash} peças de sucata de primeira qualidade!'\n\nVocê reuniu material suficiente para montar o **Covo de Garrafa**."
                embed.color = discord.Color.green()
                
                view_craft = discord.ui.View()
                btn_craft = discord.ui.Button(label="Montar Protótipo (-50 Lixos)", style=discord.ButtonStyle.success, emoji="🛠️")
                
                async def craft_callback(inter):
                    # Consome 50 lixos
                    removidos = 0
                    for t in lixo_types:
                        while inv.get(t, 0) > 0 and removidos < meta:
                            inv[t] -= 1
                            removidos += 1
                    
                    # Instala Covo Básico (Grátis na primeira vez)
                    # Status Idle para ele poder dar o start manual
                    new_trap = {"type": "covo_basico", "status": "idle", "timer_end": 0}
                    
                    cursor.execute("UPDATE economy SET inventory = ?, afk_trap = ? WHERE user_id = ?", (json.dumps(inv), json.dumps(new_trap), self.user_id))
                    get_bot_instance().db_conn.commit()
                    
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
                curr_wallet = cursor.execute("SELECT wallet FROM economy WHERE user_id=?", (self.user_id,)).fetchone()[0]
                if curr_wallet < stats['repair_cost']: return await inter.response.send_message("💸 Falta dinheiro.", ephemeral=True)
                
                trap_data['status'] = 'idle'
                cursor.execute("UPDATE economy SET wallet = wallet - ?, afk_trap = ? WHERE user_id = ?", (stats['repair_cost'], json.dumps(trap_data), self.user_id))
                get_bot_instance().db_conn.commit()
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
                cursor.execute("UPDATE economy SET afk_trap = ? WHERE user_id = ?", (json.dumps(trap_data), self.user_id))
                get_bot_instance().db_conn.commit()
                return await self.trap_manager(interaction, button)

        elif t_status == "ready":
            embed.description = f"🐟 **Rede Cheia!** Capacidade: {stats['capacity']}.\n*Cuidado: Pode rasgar ao puxar.*"
            embed.color = discord.Color.green()
            
            btn_collect = discord.ui.Button(label="Puxar Rede", style=discord.ButtonStyle.success, emoji="🎣")
            async def collect_cb(inter):
                # Re-check DB
                fresh_row = cursor.execute("SELECT afk_trap FROM economy WHERE user_id = ?", (self.user_id,)).fetchone()
                fresh_trap = json.loads(fresh_row['afk_trap'])
                if fresh_trap.get('status') != 'ready': return await inter.response.send_message("❌ Estado inválido.", ephemeral=True)

                rewards = []
                pool = [p[0] for p in FISH_DB if p[4] <= stats['loot_tier_max']]
                for _ in range(stats['capacity']):
                    fish = random.choice(pool)
                    inv[fish] = inv.get(fish, 0) + 1
                    rewards.append(fish)

                from collections import Counter
                c = Counter(rewards)
                reward_str = ", ".join([f"{k} x{v}" for k,v in c.items()])
                
                if random.randint(1, 100) <= stats['break_chance']:
                    trap_data['status'] = 'broken'
                    msg = f"💰 **Coleta:** {reward_str}\n\n💥 **CRACK!** A rede rasgou!"
                else:
                    trap_data['status'] = 'cooldown'
                    trap_data['timer_end'] = now_ts + stats['reset_time']
                    msg = f"💰 **Coleta:** {reward_str}\n\n🕸️ Limpando a rede..."

                cursor.execute("UPDATE economy SET inventory = ?, afk_trap = ? WHERE user_id = ?", (json.dumps(inv), json.dumps(trap_data), self.user_id))
                get_bot_instance().db_conn.commit()
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
                cursor.execute("UPDATE economy SET afk_trap = ? WHERE user_id = ?", (json.dumps(trap_data), self.user_id))
                get_bot_instance().db_conn.commit()
                return await self.trap_manager(interaction, button)

        elif t_status == "idle":
            embed.description = "A armadilha está limpa e pronta.\nJogar na água?"
            wait_min = int(stats['wait_time'] / 60)
            
            btn_start = discord.ui.Button(label=f"Jogar ({wait_min}m)", style=discord.ButtonStyle.primary, emoji="🌊")
            async def start_cb(inter):
                trap_data['status'] = 'working'
                trap_data['timer_end'] = now_ts + stats['wait_time']
                cursor.execute("UPDATE economy SET afk_trap = ? WHERE user_id = ?", (json.dumps(trap_data), self.user_id))
                get_bot_instance().db_conn.commit()
                await inter.response.send_message("🌊 **Lançada!**", ephemeral=True)
            
            btn_start.callback = start_cb
            view.add_item(btn_start)
            
            # --- LOJA DE UPGRADE (Só aparece se estiver IDLE) ---
            # Permite comprar uma melhor (Rede Industrial) se tiver grana
            if t_type == "covo_basico":
                btn_buy = discord.ui.Button(label="Comprar Rede Industrial (1500$)", style=discord.ButtonStyle.secondary, row=1)
                async def buy_better_cb(inter):
                    s_ind = TRAP_TYPES["rede_industrial"]
                    curr_wallet = cursor.execute("SELECT wallet FROM economy WHERE user_id=?", (self.user_id,)).fetchone()[0]
                    if curr_wallet < s_ind['cost']: return await inter.response.send_message("💸 Falta dinheiro.", ephemeral=True)
                    
                    # Substitui a trap atual
                    new_trap = {"type": "rede_industrial", "status": "idle", "timer_end": 0}
                    cursor.execute("UPDATE economy SET wallet = wallet - ?, afk_trap = ? WHERE user_id = ?", (s_ind['cost'], json.dumps(new_trap), self.user_id))
                    get_bot_instance().db_conn.commit()
                    await inter.response.send_message("✅ **Upgrade!** Você comprou a Rede de Arrasto.", ephemeral=True)
                
                btn_buy.callback = buy_better_cb
                view.add_item(btn_buy)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class CompraQuantidadeModal(discord.ui.Modal):
    def __init__(self, item_key, item_stats, current_wallet, current_inv, user_id, bot_instance):
        super().__init__(title=f"Comprar: {item_stats['name']}")
        self.item_key = item_key
        self.stats = item_stats
        self.wallet = current_wallet
        self.inv = current_inv
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

        if self.wallet < custo_total:
            return await interaction.response.send_message(f"💸 **Saldo Insuficiente!**\nVocê quer {quantidade}x ({custo_total} $), mas só tem {self.wallet} $.", ephemeral=True)

        # Processa a compra
        cursor = self.bot.db_conn.cursor()
        
        # Adiciona ao inventário
        self.inv[self.item_key] = self.inv.get(self.item_key, 0) + quantidade
        
        # Atualiza Banco
        cursor.execute("UPDATE economy SET wallet = wallet - ?, inventory = ? WHERE user_id = ?", (custo_total, json.dumps(self.inv), self.user_id))
        self.bot.db_conn.commit()

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
        
        cursor = get_bot_instance().db_conn.cursor()
        
        # Verifica Saldo
        row = cursor.execute("SELECT wallet, inventory FROM economy WHERE user_id = ?", (self.user_id,)).fetchone()
        if row['wallet'] < data['price']:
            return await interaction.response.send_message("💰 **Valerius:** 'Sem ouro, sem conversa.' (Saldo insuficiente)", ephemeral=True)
        
        # Processa a Compra
        inv = json.loads(row['inventory']) if row['inventory'] else {}
        
        # Se for vara, equipa ou guarda
        inv[item_key] = inv.get(item_key, 0) + 1
        
        # Atualiza o banco (Desconta dinheiro + Adiciona item)
        cursor.execute("UPDATE economy SET wallet = wallet - ?, inventory = ? WHERE user_id = ?", 
                      (data['price'], json.dumps(inv), self.user_id))
        get_bot_instance().db_conn.commit()
        
        await interaction.response.send_message(f"🤝 **Negócio Fechado!**\nVocê comprou: **{data['name']}** por {data['price']} Sachês.\n*Valerius sorri enquanto conta as moedas.*", ephemeral=True)



class EconomiaCog(commands.Cog):
    """Registra /eco e /guilda no Command Tree e mantém o ciclo automático de clima."""

    def __init__(self, bot):
        self.bot = bot
        set_bot_instance(bot)

    async def cog_load(self):
        self.bot.tree.add_command(eco_group)
        self.bot.tree.add_command(guilda)
        if not self.weather_cycle.is_running():
            self.weather_cycle.start()

    def cog_unload(self):
        self.weather_cycle.cancel()

    @tasks.loop(hours=4)
    async def weather_cycle(self):
        options = ["normal", "bad", "good"]
        weights = [0.7, 0.2, 0.1]
        new_weather = random.choices(options, weights)[0]
        cursor = self.bot.db_conn.cursor()
        cursor.execute("UPDATE world_state SET current_weather = ? WHERE id = 1", (new_weather,))
        self.bot.db_conn.commit()
        status_text = f"P3LUCHE | Clima: {WEATHER_EFFECTS[new_weather]['name']}"
        await self.bot.change_presence(activity=discord.Game(name=status_text))
        print(f"[CLIMA] O tempo mudou para: {new_weather.upper()}")

    @weather_cycle.before_loop
    async def before_weather_cycle(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(EconomiaCog(bot))
