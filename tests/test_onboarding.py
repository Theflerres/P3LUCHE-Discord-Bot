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

        bot_ref = SimpleNamespace(user=SimpleNamespace(avatar=None))
        embed = create_member_embed(bot_ref)
        field_values = "\n".join(f.value for f in embed.fields)
        self.assertIn("Adicionar App", field_values)
        self.assertIn(ADD_APP_STEPS, field_values)


if __name__ == "__main__":
    unittest.main()
