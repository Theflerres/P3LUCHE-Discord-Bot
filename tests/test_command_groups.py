import unittest

import discord
from discord.ext import commands


class ModGroupRegistrationTests(unittest.IsolatedAsyncioTestCase):
    """Regressão da Fase 2: moderacao.py (advertencia/historico/perdoar) foi
    agrupado sob /mod. Isso exige que o Group seja um atributo de classe da
    própria ModeracaoCog (senão o discord.py não vincula `self` aos métodos
    corretamente — verificado empiricamente antes desta mudança).
    """

    async def test_mod_group_has_all_three_subcommands_with_correct_binding(self):
        from cogs.moderacao import ModeracaoCog

        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        cog = ModeracaoCog(bot)
        await bot.add_cog(cog)

        mod = bot.tree.get_command("mod")
        self.assertIsNotNone(mod, "/mod não foi registrado na árvore de comandos")

        names = sorted(c.name for c in mod.walk_commands())
        self.assertEqual(names, ["advertencia", "historico", "perdoar"])

        for name in names:
            sub = mod.get_command(name)
            self.assertIs(sub.binding, cog, f"/mod {name} sem binding correto de self")


class MusicaGroupRegistrationTests(unittest.IsolatedAsyncioTestCase):
    """Regressão da Fase 2: /spotify_add e /spotify_pipe (antes soltos, como
    métodos de uma Cog própria) viraram funções soltas anexadas ao
    musica_group já existente, e /biblioteca (antes solto) virou
    /musica biblioteca. musica_group é registrado via tree.add_command
    (referência viva), então comandos anexados depois (como os de spotify.py,
    que só são importados na sequência real de main.py) continuam visíveis —
    diferente do padrão de Group-como-atributo-de-Cog, que tira um snapshot
    e descartaria adições tardias silenciosamente (verificado empiricamente).
    """

    async def test_musica_group_includes_spotify_and_biblioteca_after_real_load_order(self):
        from cogs.musica import MusicaCog

        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        await bot.add_cog(MusicaCog(bot))
        # Mesma ordem de main.py: "cogs.musica" carrega antes de "cogs.spotify".
        import cogs.spotify  # noqa: F401  (import só para anexar ao musica_group)

        musica = bot.tree.get_command("musica")
        self.assertIsNotNone(musica, "/musica não foi registrado na árvore de comandos")

        names = sorted(c.name for c in musica.walk_commands())
        expected = {
            "adicionar",
            "biblioteca",
            "buscar",
            "editar",
            "ocultar",
            "restaurar",
            "spotify_add",
            "spotify_pipe",
        }
        self.assertEqual(set(names), expected)


class EcoGuildaGroupingTests(unittest.TestCase):
    """Regressão: /guilda (antes solto) virou /eco guilda — mesmo padrão de
    função solta anexada ao eco_group já usado por todos os outros comandos
    de /eco, sem necessidade de nenhum ajuste de binding.
    """

    def test_guilda_is_registered_under_eco_group(self):
        from cogs import economia

        names = {c.name for c in economia.eco_group.walk_commands()}
        self.assertIn("guilda", names)


class P3lucheHelpRenameTests(unittest.TestCase):
    """Regressão: /p3luche ajuda duplicava o propósito de /ajuda (sistema.py).
    Renomeado para /p3luche comandos, mantendo o conteúdo (só o escopo de
    lore/IA) intacto, sem remover nada.
    """

    def test_p3luche_group_uses_comandos_not_ajuda(self):
        from cogs import lore_ai

        names = {c.name for c in lore_ai.p3luche_group.walk_commands()}
        self.assertIn("comandos", names)
        self.assertNotIn("ajuda", names)


if __name__ == "__main__":
    unittest.main()
