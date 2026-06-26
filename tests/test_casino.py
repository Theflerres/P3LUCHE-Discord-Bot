import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs import casino


class CasinoTests(unittest.IsolatedAsyncioTestCase):
    async def test_blackjack_push_refunds_bet(self):
        view = casino.BlackjackView(123, 10, ["T♠", "7♥", "T♦", "7♣", "A♠", "A♥"])
        view.player = ["T♠", "7♥"]
        view.dealer = ["T♦", "7♣"]

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn="db")), patch.object(casino, "modify_wallet") as mock_modify:
            await view._finish(None)

        mock_modify.assert_called_once_with("db", 123, 10)


if __name__ == "__main__":
    unittest.main()
