import unittest

from cogs.pesca_visuals import (
    resolve_fishing_asset,
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


class ResolveWeatherAssetTests(unittest.TestCase):
    def test_bad_weather(self):
        self.assertEqual(resolve_weather_asset("bad"), "assets/pesca/clima_ruim.gif")

    def test_good_weather(self):
        self.assertEqual(resolve_weather_asset("good"), "assets/pesca/clima_bom.gif")

    def test_normal_weather_has_no_asset(self):
        self.assertIsNone(resolve_weather_asset("normal"))


if __name__ == "__main__":
    unittest.main()
