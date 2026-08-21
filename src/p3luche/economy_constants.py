"""
Dados estáticos da economia (sem dependência do discord.py).
"""

# Estrutura: (Nome, PreçoMin, PreçoMax, Emoji, Tier, Frase)
FISH_DB = [
    # ─── Tier 0: Lixo ───────────────────────────────────────────────
    ("Bota Velha", 0, 5, "👢", 0, "Alguém foi embora pulando."),
    ("Lata Vazia", 2, 6, "🥫", 0, "Recicle, por favor."),
    ("Pneu Furado", 0, 8, "🍩", 0, "Furou o rolê."),
    ("Sacola Plástica", 0, 2, "🥡", 0, "Isso mata as tartarugas!"),
    ("Espinha de Peixe", 0, 1, "🦴", 0, "Chegou tarde, o gato já comeu."),
    ("Garrafa PET", 0, 3, "🧴", 0, "500ml de decepção."),
    ("Chinelo Solitário", 0, 4, "🩴", 0, "Cadê o par dele?"),
    ("Isqueiro Molhado", 1, 6, "🔥", 0, "Não acende mais."),
    ("Meia Furada", 0, 3, "🧦", 0, "O rio também tem lavanderia perdida."),
    ("Anzol Enferrujado", 2, 7, "🪝", 0, "Cuidado ao guardar."),

    # ─── Tier 0: Peixes iniciais ────────────────────────────────────
    ("Sardinha", 10, 20, "🐟", 0, "O pão de cada dia."),
    ("Lambari", 10, 16, "🐠", 0, "Pequeno e crocante."),
    ("Tilápia", 15, 25, "🐟", 0, "Clássico do pesque-pague."),
    ("Peixe Dourado", 18, 28, "🐡", 0, "Fugiu do aquário."),
    ("Bagre", 20, 30, "🐟", 0, "Cuidado com o bigode."),
    ("Corimbatá", 8, 16, "🐟", 0, "Osso pra tudo quanto é lado."),
    ("Piau", 12, 20, "🐠", 0, "Prato de boteco de beira de rio."),
    ("Acará", 14, 22, "🐡", 0, "Colorido, mas nada raro."),
    ("Cascudo", 9, 18, "🐟", 0, "Limpa o aquário sozinho."),
    ("Barbado", 16, 26, "🐟", 0, "Tem mais bigode que o Bagre."),

    # ─── Tier 1 ──────────────────────────────────────────────────────
    ("Truta", 40, 60, "🐟", 1, "Gosta de águas geladas."),
    ("Tambaqui", 55, 85, "🐟", 1, "O gigante redondo dos rios."),
    ("Lula", 65, 95, "🦑", 1, "Anéis empanados... hmmm."),
    ("Camarão", 35, 55, "🦐", 1, "A cabeça você joga fora."),
    ("Caranguejo", 45, 75, "🦀", 1, "Andando de lado."),
    ("Polvo", 75, 105, "🐙", 1, "8 braços para te dar um tapa."),
    ("Baiacu", 40, 70, "🐡", 1, "Não coma se não souber limpar!"),
    ("Piranha", 45, 65, "🦷", 1, "Ela queria seu dedo."),
    ("Tucunaré", 55, 85, "🐠", 1, "Lutador dos rios brasileiros."),
    ("Robalo", 50, 75, "🐟", 1, "Elegante até fritado."),
    ("Pintado", 65, 95, "🐟", 1, "Peixe de aniversário de vô."),
    ("Pacu", 45, 70, "🐠", 1, "Dente de gente, cuidado."),
    ("Corvina", 55, 85, "🐟", 1, "Canta debaixo d'água, dizem."),
    ("Namorado", 70, 100, "🐟", 1, "Bom pra impressionar no jantar."),

    # ─── Tier 2 ──────────────────────────────────────────────────────
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
    ("Peixe-Balão", 130, 190, "🎈", 2, "Não aperta, ele estoura."),
    ("Peixe-Voador", 140, 210, "🕊️", 2, "Achou que era passarinho."),
    ("Cavala", 160, 240, "🐟", 2, "Ótima na brasa."),
    ("Garoupa", 280, 380, "🐟", 2, "Rende quilos de posta."),
    ("Badejo", 250, 350, "🐟", 2, "Escondido nas pedras."),

    # ─── Tier 3 ──────────────────────────────────────────────────────
    ("Tubarão Branco", 1000, 1500, "🦈", 3, "Precisamos de um barco maior."),
    ("Baleia Azul", 2000, 3000, "🐋", 3, "O maior animal da Terra."),
    ("Lula Gigante", 2500, 3500, "🦑", 3, "Pesadelo dos marinheiros antigos."),
    ("Narval", 1800, 2600, "🦄", 3, "O unicórnio dos mares."),
    ("Orca", 2200, 3200, "🐼", 3, "A baleia assassina (que é um golfinho)."),
    ("Megalodon", 4000, 6000, "🦖", 3, "Achou que estava extinto? Achou errado."),
    ("Moby Dick", 5000, 7000, "🐳", 3, "A obsessão do Capitão Ahab."),
    ("Peixe-Lua", 1500, 2500, "🌑", 3, "Parece uma panqueca gigante."),
    ("Tubarão-Baleia", 1200, 1800, "🦈", 3, "Gigante, mas manso."),
    ("Tubarão-Tigre", 1700, 2400, "🐯", 3, "Come qualquer coisa, literalmente."),
    ("Peixe-Serra", 2800, 3800, "🪚", 3, "Já vem com a ferramenta."),
    ("Lula Colossal Jovem", 3200, 4200, "🦑", 3, "Imagina o tamanho da mãe."),
    ("Behemoth das Profundezas", 4500, 5800, "🌊", 3, "Ninguém sabe o que é isso."),

    # ─── Tier 4: Mítico ────────────────────────────────────────────
    ("Kraken", 8000, 12000, "🐙🔥", 4, "LIBEREM O KRAKEN!"),
    ("Leviatã", 10000, 15000, "🐉", 4, "A serpente do fim do mundo."),
    ("Nessie", 12000, 18000, "🦕", 4, "O Monstro do Lago Ness é real?!"),
    ("Sereia", 15000, 25000, "🧜‍♀️", 4, "Cuidado com o canto dela..."),
    ("Godzilla (Aquático)", 20000, 30000, "🦖☢️", 4, "O Rei dos Monstros acordou."),
    ("CTHULHU", 50000, 66666, "🐙💀", 4, "Ph'nglui mglw'nafh R'lyeh..."),
    ("Bob Esponja", 5000, 8000, "🧽", 4, "Vive num abacaxi."),
    ("Peixe de 3 Olhos", 6000, 9000, "☢️", 4, "Direto de Springfield."),
    ("Peixe Cibernético", 15000, 20000, "🤖", 4, "Veio do ano 3077."),
    ("O PEIXE DOURADO SUPREMO", 40000, 60000, "👑", 4, "O deus de todos os aquários."),
    ("Drone de Combate Aquático", 16000, 22000, "🚀", 4, "Vazou de algum projeto secreto."),
    ("Fenrir Aquático", 22000, 28000, "🐺", 4, "O lobo que engoliu o sol, versão nadadora."),
]


# Itens de tier 0 que são LIXO, não peixe. Não dá pra derivar de `tier == 0`:
# o tier 0 também guarda os peixes iniciais (Sardinha, Lambari, Tilápia,
# Peixe Dourado, Bagre, Corimbatá, Piau, Acará, Cascudo, Barbado). Fonte
# única de verdade — antes cada ponto do código tinha sua própria lista de
# nomes hardcoded e nenhuma batia com as outras, então Pneu Furado / Sacola
# Plástica / Espinha de Peixe eram pagos como peixe e nunca chegavam na
# mochila pra reciclar. "Alga" não está no FISH_DB: vem do loot da armadilha
# AFK, mas é lixo pra todos os efeitos.
# Fatia do "roll de lixo" que entrega lixo de verdade; o resto entrega peixe
# inicial. Antes isso não era ajustável: o roll sorteava uniformemente entre
# TODAS as entradas de tier 0, então a proporção era um efeito colateral de
# quantas linhas de lixo vs. peixe existiam na tabela — mudava sozinha a cada
# item novo no FISH_DB. Agora o número é explícito e independe do tamanho da
# tabela: 0.4 = 4 lixos a cada 10 rolls de lixo.
TRASH_ROLL_RATIO = 0.4

TRASH_ITEMS = frozenset({
    "Bota Velha",
    "Lata Vazia",
    "Pneu Furado",
    "Sacola Plástica",
    "Espinha de Peixe",
    "Garrafa PET",
    "Chinelo Solitário",
    "Isqueiro Molhado",
    "Meia Furada",
    "Anzol Enferrujado",
    "Alga",
})