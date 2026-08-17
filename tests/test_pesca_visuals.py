import unittest

from cogs.pesca_visuals import (
    resolve_fishing_asset,
    resolve_qte_asset,
    resolve_weather_asset,
)


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


class ResolveQteAssetTests(unittest.TestCase):
    def test_tensao_state(self):
        self.assertEqual(resolve_qte_asset("tensao"), "assets/pesca/qte_tensao.gif")

    def test_sucesso_state(self):
        self.assertEqual(resolve_qte_asset("sucesso"), "assets/pesca/qte_sucesso.png")

    def test_falha_state_covers_both_error_and_timeout(self):
        self.assertEqual(resolve_qte_asset("falha"), "assets/pesca/qte_falha.png")

    def test_unknown_state_returns_none(self):
        self.assertIsNone(resolve_qte_asset("inexistente"))


class ResolveWeatherAssetTests(unittest.TestCase):
    def test_bad_weather(self):
        self.assertEqual(resolve_weather_asset("bad"), "assets/pesca/clima_ruim.gif")

    def test_good_weather(self):
        self.assertEqual(resolve_weather_asset("good"), "assets/pesca/clima_bom.gif")

    def test_normal_weather_has_no_asset(self):
        self.assertIsNone(resolve_weather_asset("normal"))


if __name__ == "__main__":
    unittest.main()
