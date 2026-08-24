import os
import re
import tempfile
import unittest

from cogs.pesca_visuals import (
    FISHING_VISUALS,
    TRASH_VISUAL,
    WEATHER_VISUALS,
    resolve_fishing_asset,
    resolve_weather_asset,
)
from utils import PROJECT_ROOT, get_local_file, resolve_asset_path


class ResolveFishingAssetTests(unittest.TestCase):
    def test_each_tier_resolves_to_its_own_asset(self):
        for tier in range(5):
            with self.subTest(tier=tier):
                asset = resolve_fishing_asset("Peixe Genérico", tier, is_trash=False)
                self.assertEqual(asset, f"assets/pesca/tier{tier}.png" if tier < 4 else "assets/pesca/tier4.gif")

    def test_trash_overrides_tier_regardless_of_value(self):
        for tier in range(5):
            with self.subTest(tier=tier):
                asset = resolve_fishing_asset("Bota Velha", tier, is_trash=True)
                self.assertEqual(asset, "assets/pesca/lixo.gif")

    def test_unknown_tier_falls_back_to_tier0(self):
        asset = resolve_fishing_asset("Peixe Misterioso", 99, is_trash=False)
        self.assertEqual(asset, "assets/pesca/tier0.png")


class TierAssetsAreTheOnlyCatchVisualsTests(unittest.TestCase):
    """O QTE foi removido: peixes de tier alto não têm mais asset próprio de
    tensão/sucesso/falha — usam o asset genérico do tier, como todos os outros.
    """

    def test_no_qte_resolver_remains(self):
        from cogs import pesca_visuals

        self.assertFalse(hasattr(pesca_visuals, "resolve_qte_asset"))
        self.assertFalse(hasattr(pesca_visuals, "QTE_VISUALS"))

    def test_high_tier_catch_uses_its_plain_tier_asset(self):
        for tier in (3, 4):
            with self.subTest(tier=tier):
                asset = resolve_fishing_asset("Peixe Lendário", tier, False)
                self.assertNotIn("qte", asset)
                self.assertIn(f"tier{tier}", asset)


class AssetPathAnchoringTests(unittest.TestCase):
    """Regressão: todo caminho de asset do projeto é relativo, e antes eles
    eram resolvidos contra os.getcwd(). Subir o bot de qualquer pasta que não
    a raiz do repositório fazia TODA mídia desaparecer das mensagens em
    silêncio, porque get_local_file devolve (None, None) quando não acha o
    arquivo e todos os chamadores tratam isso como "manda sem imagem".
    """

    def test_project_root_points_at_the_repository_root(self):
        self.assertTrue((PROJECT_ROOT / "assets").is_dir(), f"assets/ não está em {PROJECT_ROOT}")
        self.assertTrue((PROJECT_ROOT / "src" / "p3luche").is_dir())

    def test_relative_asset_path_is_anchored_not_cwd_dependent(self):
        esperado = PROJECT_ROOT / "assets" / "pesca" / "tier0.png"
        self.assertEqual(resolve_asset_path("assets/pesca/tier0.png"), esperado)

    def test_absolute_path_is_left_alone(self):
        absoluto = PROJECT_ROOT / "assets" / "pesca" / "tier0.png"
        self.assertEqual(resolve_asset_path(absoluto), absoluto)

    def test_existing_asset_still_loads_from_a_foreign_working_directory(self):
        """O teste que reproduz o bug: com o CWD fora do repo, o arquivo tem
        que continuar sendo encontrado."""
        anterior = os.getcwd()
        with tempfile.TemporaryDirectory() as outra_pasta:
            try:
                os.chdir(outra_pasta)
                arquivo, url = get_local_file("assets/pesca/tier0.png", "tier0.png")
            finally:
                os.chdir(anterior)

        self.assertIsNotNone(arquivo, "asset existente não foi encontrado com o CWD fora da raiz")
        self.assertEqual(url, "attachment://tier0.png")
        arquivo.close()

    def test_missing_asset_still_degrades_quietly(self):
        """A âncora não pode transformar ausência em exceção: os chamadores
        contam com o (None, None) para mandar a mensagem sem mídia."""
        arquivo, url = get_local_file("assets/locais/nao_existe_de_jeito_nenhum.jpg", "x.jpg")
        self.assertIsNone(arquivo)
        self.assertIsNone(url)

    def test_every_asset_path_in_the_code_resolves_to_a_real_file(self):
        """Varre o código atrás de caminhos "assets/..." e confere que cada um
        aponta para arquivo existente. É esta asserção que quebra se um asset
        for renomeado no disco sem atualizar o código (ou vice-versa).
        """
        padrao = re.compile(r'["\']((?:assets)/[^"\']+\.[A-Za-z0-9]{2,4})["\']')
        encontrados = {}
        for caminho_py in (PROJECT_ROOT / "src" / "p3luche").rglob("*.py"):
            texto = caminho_py.read_text(encoding="utf-8")
            for m in padrao.finditer(texto):
                encontrados.setdefault(m.group(1), []).append(caminho_py.name)

        # Os caminhos de assets/pesca são montados por f-string em
        # pesca_visuals e não caem no regex acima; entram explicitamente.
        for valor in list(FISHING_VISUALS.values()) + [TRASH_VISUAL] + list(WEATHER_VISUALS.values()):
            encontrados.setdefault(valor, []).append("pesca_visuals.py")

        self.assertTrue(encontrados, "nenhum caminho de asset encontrado no código — regex quebrou?")
        faltando = {
            ref: onde for ref, onde in encontrados.items()
            if not resolve_asset_path(ref).is_file()
        }
        self.assertEqual(faltando, {}, f"assets referenciados que não existem no disco: {faltando}")


class ResolveWeatherAssetTests(unittest.TestCase):
    def test_bad_weather(self):
        self.assertEqual(resolve_weather_asset("bad"), "assets/pesca/clima_ruim.gif")

    def test_good_weather(self):
        self.assertEqual(resolve_weather_asset("good"), "assets/pesca/clima_bom.gif")

    def test_normal_weather_has_no_asset(self):
        self.assertIsNone(resolve_weather_asset("normal"))


if __name__ == "__main__":
    unittest.main()
