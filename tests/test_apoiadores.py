import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from cogs.sistema import (
    SUPPORTER_ROLE_IDS,
    build_tita_embed,
    build_veteranos_embed,
    build_vip_embed,
    categorize_supporters,
    resolve_categorized_supporters,
    resolve_supporter_display,
)

VIP = SUPPORTER_ROLE_IDS["vip"]
VETERANO = SUPPORTER_ROLE_IDS["veterano"]
TITA = SUPPORTER_ROLE_IDS["tita"]


def _member(user_id: int, *role_ids: int):
    return SimpleNamespace(
        id=user_id,
        mention=f"<@{user_id}>",
        roles=[SimpleNamespace(id=rid) for rid in role_ids],
    )


def _not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown")


class CategorizeSupportersTests(unittest.TestCase):
    """VIP e Veterano são mutuamente exclusivos (VIP tem prioridade como
    tela principal). Escudo da Tita é ADITIVO: quem tem esse cargo sempre
    aparece na lista "tita" também, além de onde mais aparecer — regressão
    do bug em que a tela do Escudo esvaziava pra quem também era VIP/Veterano.
    """

    def test_vip_only_appears_in_vip_without_badges(self):
        m = _member(1, VIP)
        result = categorize_supporters([m])
        self.assertEqual(result["vip"], [(m, [])])
        self.assertEqual(result["veterano"], [])
        self.assertEqual(result["tita"], [])

    def test_veterano_only_appears_in_veterano_without_badges(self):
        m = _member(2, VETERANO)
        result = categorize_supporters([m])
        self.assertEqual(result["veterano"], [(m, [])])
        self.assertEqual(result["vip"], [])
        self.assertEqual(result["tita"], [])

    def test_tita_only_appears_in_tita_without_badges(self):
        m = _member(3, TITA)
        result = categorize_supporters([m])
        self.assertEqual(result["tita"], [(m, [])])
        self.assertEqual(result["vip"], [])
        self.assertEqual(result["veterano"], [])

    def test_vip_and_veterano_appears_once_in_vip_with_veterano_badge(self):
        m = _member(4, VIP, VETERANO)
        result = categorize_supporters([m])
        self.assertEqual(result["vip"], [(m, ["🏛️"])])
        self.assertEqual(result["veterano"], [], "VIP e Veterano continuam mutuamente exclusivos")
        self.assertEqual(result["tita"], [])

    def test_vip_and_tita_appears_in_BOTH_vip_and_tita_screens(self):
        """Regressão direta do bug relatado: VIP+Tita não pode sumir do
        Escudo — precisa aparecer nas duas telas."""
        m = _member(5, VIP, TITA)
        result = categorize_supporters([m])
        self.assertEqual(result["vip"], [(m, ["🛡️"])], "selo 🛡️ na tela principal")
        self.assertEqual(result["tita"], [(m, [])], "e TAMBÉM aparece na tela do Escudo, sem selo")

    def test_veterano_and_tita_appears_in_BOTH_veterano_and_tita_screens(self):
        m = _member(6, VETERANO, TITA)
        result = categorize_supporters([m])
        self.assertEqual(result["veterano"], [(m, ["🛡️"])])
        self.assertEqual(result["tita"], [(m, [])])

    def test_all_three_roles_appears_in_vip_with_both_badges_AND_in_tita(self):
        m = _member(7, VIP, VETERANO, TITA)
        result = categorize_supporters([m])
        self.assertEqual(result["vip"], [(m, ["🏛️", "🛡️"])])
        self.assertEqual(result["veterano"], [])
        self.assertEqual(result["tita"], [(m, [])], "tem o cargo do Escudo, então aparece lá também")

    def test_member_with_no_supporter_roles_is_excluded_entirely(self):
        m = _member(8, 999999)  # cargo qualquer, não relacionado
        result = categorize_supporters([m])
        self.assertEqual(result["vip"], [])
        self.assertEqual(result["veterano"], [])
        self.assertEqual(result["tita"], [])

    def test_mixed_group_tita_bucket_includes_everyone_with_the_role(self):
        members = [
            _member(1, VIP),
            _member(2, VETERANO),
            _member(3, TITA),
            _member(4, VIP, VETERANO),
            _member(5, VIP, TITA),
            _member(6, VETERANO, TITA),
            _member(7, VIP, VETERANO, TITA),
            _member(8, 999999),
        ]
        result = categorize_supporters(members)

        # vip: 1, 4, 5, 7 | veterano: 2, 6 | tita: 3, 5, 6, 7
        self.assertEqual({m.id for m, _ in result["vip"]}, {1, 4, 5, 7})
        self.assertEqual({m.id for m, _ in result["veterano"]}, {2, 6})
        self.assertEqual(
            {m.id for m, _ in result["tita"]}, {3, 5, 6, 7}, "todo mundo com o cargo do Escudo, sem exceção"
        )


class ResolveSupporterDisplayTests(unittest.IsolatedAsyncioTestCase):
    """Cobre cache-first (sem custo de rede pro caso comum) e os 3 caminhos
    de rede: membro não resolve pelo cache mas ainda está lá, saiu mas a
    conta existe, e o caso extremo de conta removida de vez."""

    async def test_member_resolves_via_cache_without_any_network_call(self):
        guild = SimpleNamespace(
            get_member=lambda uid: SimpleNamespace(id=uid),
            fetch_member=AsyncMock(),
        )
        bot = SimpleNamespace(fetch_user=AsyncMock())
        member = _member(1, VIP)

        display = await resolve_supporter_display(guild, bot, member)

        self.assertEqual(display, "<@1>")
        guild.fetch_member.assert_not_called()
        bot.fetch_user.assert_not_called()

    async def test_member_not_in_cache_falls_through_to_fetch_member(self):
        guild = SimpleNamespace(
            get_member=lambda uid: None,
            fetch_member=AsyncMock(return_value=SimpleNamespace(id=1)),
        )
        bot = SimpleNamespace(fetch_user=AsyncMock())
        member = _member(1, VIP)

        display = await resolve_supporter_display(guild, bot, member)

        self.assertEqual(display, "<@1>")
        guild.fetch_member.assert_awaited_once_with(1)

    async def test_member_left_server_but_account_exists_shows_name_with_marker(self):
        guild = SimpleNamespace(get_member=lambda uid: None, fetch_member=AsyncMock(side_effect=_not_found()))
        bot = SimpleNamespace(fetch_user=AsyncMock(return_value=SimpleNamespace(display_name="ExApoiador")))
        member = _member(2, VETERANO)

        display = await resolve_supporter_display(guild, bot, member)

        self.assertIn("ExApoiador", display)
        self.assertIn("saiu do servidor", display)

    async def test_member_left_and_account_deleted_falls_back_to_generic_text(self):
        guild = SimpleNamespace(get_member=lambda uid: None, fetch_member=AsyncMock(side_effect=_not_found()))
        bot = SimpleNamespace(fetch_user=AsyncMock(side_effect=_not_found()))
        member = _member(3, TITA)

        display = await resolve_supporter_display(guild, bot, member)

        self.assertEqual(display, "Apoiador (conta removida)")
        # Nunca deixa o ID cru aparecer.
        self.assertNotIn("3", display)


class ResolveCategorizedSupportersTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_every_bucket_and_keeps_badges(self):
        vip_member = _member(1, VIP, VETERANO)
        veterano_member = _member(2, VETERANO)
        categorized = {"vip": [(vip_member, ["🏛️"])], "veterano": [(veterano_member, [])], "tita": []}

        guild = SimpleNamespace(get_member=lambda uid: SimpleNamespace(id=uid), fetch_member=AsyncMock())
        bot = SimpleNamespace(fetch_user=AsyncMock())

        resolved = await resolve_categorized_supporters(guild, bot, categorized)

        self.assertEqual(resolved["vip"], [("<@1>", ["🏛️"])])
        self.assertEqual(resolved["veterano"], [("<@2>", [])])
        self.assertEqual(resolved["tita"], [])

    async def test_resolution_runs_concurrently_not_sequentially(self):
        """Regressão de performance: N pessoas precisando de fetch_member
        devem levar ~1 chamada de tempo total, não N chamadas somadas."""
        import time

        DELAY = 0.05
        N = 6

        async def slow_fetch_member(uid):
            await asyncio.sleep(DELAY)
            raise _not_found()

        async def slow_fetch_user(uid):
            await asyncio.sleep(DELAY)
            return SimpleNamespace(display_name=f"User{uid}")

        guild = SimpleNamespace(get_member=lambda uid: None, fetch_member=AsyncMock(side_effect=slow_fetch_member))
        bot = SimpleNamespace(fetch_user=AsyncMock(side_effect=slow_fetch_user))
        categorized = {"vip": [(_member(i, VIP), []) for i in range(N)], "veterano": [], "tita": []}

        start = time.monotonic()
        await resolve_categorized_supporters(guild, bot, categorized)
        elapsed = time.monotonic() - start

        # Sequencial seria N * 2 * DELAY (fetch_member + fetch_user, um de
        # cada vez) = 0.6s. Paralelo fica perto de 2 * DELAY = 0.1s.
        self.assertLess(elapsed, 4 * DELAY, "resolução não está rodando em paralelo (asyncio.gather)")


class SupporterEmbedTests(unittest.TestCase):
    """Testa só a renderização — recebe entradas já resolvidas (texto
    pronto, não objetos Member), desacoplado de categorização/resolução,
    que já têm suas próprias suítes acima."""

    def test_vip_embed_shows_count_and_legend_only_when_badges_present(self):
        categorized = {"vip": [("<@1>", []), ("<@2>", ["🏛️"])], "veterano": [], "tita": []}

        embed = build_vip_embed(categorized)
        self.assertIn("2 apoiador", embed.description)
        self.assertIn("<@2> 🏛️", embed.description)
        legend_field = next((f for f in embed.fields if f.name == "Legenda"), None)
        self.assertIsNotNone(legend_field, "deveria ter legenda pois há selo em uso")
        self.assertIn("🏛️", legend_field.value)

    def test_vip_embed_has_no_legend_when_nobody_has_a_badge(self):
        categorized = {"vip": [("<@1>", [])], "veterano": [], "tita": []}
        embed = build_vip_embed(categorized)
        self.assertIsNone(next((f for f in embed.fields if f.name == "Legenda"), None))

    def test_veteranos_embed_uses_exact_requested_text(self):
        categorized = {"vip": [], "veterano": [("<@1>", [])], "tita": []}
        embed = build_veteranos_embed(categorized)
        self.assertTrue(embed.description.startswith("Doadores da temporada anterior do FCN."))

    def test_tita_embed_has_dedicated_screen_count_and_no_legend(self):
        categorized = {"vip": [], "veterano": [], "tita": [("<@1>", []), ("<@2>", [])]}
        embed = build_tita_embed(categorized)
        self.assertIn("**2 pessoa(s)** ajudaram quando foi preciso", embed.description)
        self.assertIn("<@1>", embed.description)
        self.assertIn("<@2>", embed.description)
        self.assertNotIn("[PLACEHOLDER]", embed.description)
        self.assertIsNone(next((f for f in embed.fields if f.name == "Legenda"), None))

    def test_empty_category_shows_fallback_text(self):
        categorized = {"vip": [], "veterano": [], "tita": []}
        embed = build_tita_embed(categorized)
        self.assertIn("Ninguém", embed.description)

    def test_left_server_and_removed_account_render_cleanly_no_raw_id(self):
        categorized = {
            "vip": [],
            "veterano": [
                ("ExApoiador *(saiu do servidor)*", []),
                ("Apoiador (conta removida)", []),
            ],
            "tita": [],
        }
        embed = build_veteranos_embed(categorized)
        self.assertIn("ExApoiador *(saiu do servidor)*", embed.description)
        self.assertIn("Apoiador (conta removida)", embed.description)


if __name__ == "__main__":
    unittest.main()
