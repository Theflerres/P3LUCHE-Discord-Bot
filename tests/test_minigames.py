import unittest

from cogs import minigames


class MinigamesTests(unittest.TestCase):
    def test_auction_min_bid_scales_with_rarity(self):
        common = {"price": 100, "rarity": "common"}
        rare = {"price": 100, "rarity": "rare"}
        epic = {"price": 100, "rarity": "epic"}
        legendary = {"price": 100, "rarity": "legendary"}
        mythic = {"price": 100, "rarity": "mythic"}

        self.assertEqual(minigames._get_auction_min_bid(common), 50)
        self.assertEqual(minigames._get_auction_min_bid(rare), 75)
        self.assertEqual(minigames._get_auction_min_bid(epic), 100)
        self.assertEqual(minigames._get_auction_min_bid(legendary), 150)
        self.assertEqual(minigames._get_auction_min_bid(mythic), 200)

    def test_build_auction_start_message_includes_item_name(self):
        message = minigames.build_auction_start_message("Espada de Ouro")

        self.assertIn("NOVO LEILÃO INICIADO", message)
        self.assertIn("Espada de Ouro", message)

    def test_build_auction_approval_embed_mentions_timeout(self):
        embed = minigames.build_auction_approval_embed("Espada de Ouro", "legendary", 150)

        self.assertEqual(embed.title, "📝 Solicitação de Leilão")
        self.assertIn("5 minutos", embed.description)
        self.assertIn("Espada de Ouro", embed.description)

    def test_build_auction_embed_shows_current_participant_and_last_bid(self):
        embed = minigames.build_auction_embed("Espada de Ouro", "legendary", 150, minigames.datetime.now() + minigames.timedelta(hours=1))

        self.assertEqual(embed.fields[0].name, "⏳ Tempo restante")
        self.assertEqual(embed.fields[1].name, "👤 Participante atual")
        self.assertEqual(embed.fields[1].value, "Nenhum")
        self.assertEqual(embed.fields[2].name, "💸 Último lance")
        self.assertEqual(embed.fields[2].value, "Nenhum")

    def test_format_time_remaining_formats_hours_minutes_and_seconds(self):
        self.assertEqual(minigames.format_time_remaining(3661), "1h 1m 1s")


if __name__ == "__main__":
    unittest.main()
