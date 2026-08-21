"""
Camada de apresentação da pescaria — mapeia peixes/tiers/clima para os arquivos
locais em assets/pesca/. Não conhece saldo, inventário, wallet ou qualquer outra
regra de jogo: só decide qual arquivo usar.
"""

ASSET_DIR = "assets/pesca"

# Tier da captura (0-4, igual ao índice 4 das tuplas de FISH_DB) -> asset.
FISHING_VISUALS = {
    0: f"{ASSET_DIR}/tier0.png",
    1: f"{ASSET_DIR}/tier1.png",
    2: f"{ASSET_DIR}/tier2.png",
    3: f"{ASSET_DIR}/tier3.png",
    4: f"{ASSET_DIR}/tier4.gif",
}

TRASH_VISUAL = f"{ASSET_DIR}/lixo.gif"

WEATHER_VISUALS = {
    "bad": f"{ASSET_DIR}/clima_ruim.gif",
    "good": f"{ASSET_DIR}/clima_bom.gif",
    # "normal" não tem asset — mantém o comportamento de texto/status atual.
}


def resolve_fishing_asset(fish_name: str, tier: int, is_trash: bool) -> str:
    """Decide qual arquivo representa esta captura no embed de resultado."""
    if is_trash:
        return TRASH_VISUAL
    return FISHING_VISUALS.get(tier, FISHING_VISUALS[0])


def resolve_weather_asset(weather_key: str):
    """Decide qual GIF de clima usar no banner pontual de mudança de clima.
    Retorna None para 'normal' (sem asset) ou chave desconhecida."""
    return WEATHER_VISUALS.get(weather_key)
