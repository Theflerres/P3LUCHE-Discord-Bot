import unittest
from types import SimpleNamespace

from cogs.onboarding import ADD_APP_STEPS, build_welcome_embed


def _make_member(user_id=123, name="Novato"):
    return SimpleNamespace(
        id=user_id,
        mention=f"<@{user_id}>",
        display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
    )


class BuildWelcomeEmbedTests(unittest.TestCase):
    def test_mentions_the_new_member(self):
        embed = build_welcome_embed(_make_member(42))
        self.assertIn("<@42>", embed.description)

    def test_points_to_eco_pescar_as_first_action(self):
        embed = build_welcome_embed(_make_member())
        self.assertIn("/eco pescar", embed.description)

    def test_includes_add_app_instructions(self):
        embed = build_welcome_embed(_make_member())
        field_values = "\n".join(f.value for f in embed.fields)
        self.assertIn("Adicionar App", field_values)
        self.assertIn("Autorizar", field_values)


class AjudaAddAppTipTests(unittest.TestCase):
    def test_member_embed_includes_add_app_steps(self):
        from cogs.sistema import create_member_embed

        bot_ref = SimpleNamespace(user=SimpleNamespace(avatar=None), get_cog=lambda name: None)
        embed = create_member_embed(bot_ref)
        field_values = "\n".join(f.value for f in embed.fields)
        self.assertIn("Adicionar App", field_values)
        self.assertIn(ADD_APP_STEPS, field_values)


class AjudaCapabilitiesOverviewTests(unittest.TestCase):
    """/ajuda ganhou uma visão geral do que o bot é capaz de fazer, na
    description do embed de membro (antes das listas de comando por
    categoria). Cobre as áreas pedidas e reflete o status real do cassino
    (lido via bot.get_cog, não hardcoded)."""

    def _make_bot(self, casino_loaded: bool):
        fake_casino_cog = object() if casino_loaded else None
        return SimpleNamespace(
            user=SimpleNamespace(avatar=None),
            get_cog=lambda name: fake_casino_cog if name == "CasinoCog" else None,
        )

    def test_overview_covers_all_required_areas(self):
        from cogs.sistema import create_member_embed

        embed = create_member_embed(self._make_bot(casino_loaded=False))
        desc = embed.description
        for keyword in ("Economia", "Pesca", "Ilha", "Cassino", "Música", "Moderação", "Lore"):
            self.assertIn(keyword, desc, f"visão geral não menciona '{keyword}'")

    def test_reuses_weather_names_from_economia_instead_of_hardcoding(self):
        from cogs.economia import WEATHER_EFFECTS
        from cogs.sistema import create_member_embed

        embed = create_member_embed(self._make_bot(casino_loaded=False))
        for weather in WEATHER_EFFECTS.values():
            self.assertIn(weather["name"], embed.description)

    def test_casino_shown_as_disabled_when_cog_not_loaded(self):
        from cogs.sistema import create_member_embed

        embed = create_member_embed(self._make_bot(casino_loaded=False))
        self.assertIn("desativado", embed.description.lower())

    def test_casino_not_shown_as_disabled_when_cog_is_loaded(self):
        from cogs.sistema import create_member_embed

        embed = create_member_embed(self._make_bot(casino_loaded=True))
        self.assertNotIn("desativado", embed.description.lower())

    def test_does_not_remove_the_add_app_tip(self):
        from cogs.sistema import create_member_embed

        embed = create_member_embed(self._make_bot(casino_loaded=False))
        field_values = "\n".join(f.value for f in embed.fields)
        self.assertIn("Adicionar App", field_values)


if __name__ == "__main__":
    unittest.main()
