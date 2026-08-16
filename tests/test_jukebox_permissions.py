import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs import jukebox


class JukeboxChannelPermissionTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: nenhum comando de jukebox.py checava canal/cargo — qualquer
    usuário, em qualquer canal, podia fazer o bot emitir requisições via
    yt-dlp para URLs arbitrárias (SSRF) e consumir CPU/rede sem restrição.
    Todos os 10 comandos agora chamam check_channel_permission (reaproveitado
    de cogs.musica) como primeira ação, antes de qualquer lógica de voz/fila.
    """

    def _make_cog(self):
        return jukebox.MusicaV2(SimpleNamespace())

    def _make_interaction(self):
        return SimpleNamespace(
            response=AsyncMock(),
            followup=AsyncMock(),
            user=SimpleNamespace(id=1, display_name="Tester"),
            guild=None,
            channel=None,
        )

    async def _assert_blocked(self, cog, command_name, *args):
        interaction = self._make_interaction()
        with patch.object(jukebox, "check_channel_permission", new=AsyncMock(return_value=False)) as mocked_check:
            command = getattr(cog, command_name)
            await command.callback(cog, interaction, *args)

        mocked_check.assert_awaited_once_with(interaction)
        # Se a checagem bloqueou, nada do fluxo real do comando pode ter
        # rodado — nem defer, nem send_message.
        interaction.response.defer.assert_not_called()
        interaction.response.send_message.assert_not_called()

    async def test_tocar_blocked_outside_allowed_channel(self):
        await self._assert_blocked(self._make_cog(), "tocar")

    async def test_cardapio_blocked_outside_allowed_channel(self):
        await self._assert_blocked(self._make_cog(), "cardapio")

    async def test_tocar_url_blocked_outside_allowed_channel(self):
        await self._assert_blocked(self._make_cog(), "tocar_url", "https://example.com/video")

    async def test_adicionar_blocked_outside_allowed_channel(self):
        await self._assert_blocked(self._make_cog(), "adicionar", "busca qualquer")

    async def test_adicionar_url_blocked_outside_allowed_channel(self):
        await self._assert_blocked(self._make_cog(), "adicionar_url", "https://example.com/video")

    async def test_pausar_blocked_outside_allowed_channel(self):
        await self._assert_blocked(self._make_cog(), "pausar")

    async def test_retomar_blocked_outside_allowed_channel(self):
        await self._assert_blocked(self._make_cog(), "retomar")

    async def test_parar_blocked_outside_allowed_channel(self):
        await self._assert_blocked(self._make_cog(), "parar")

    async def test_pular_blocked_outside_allowed_channel(self):
        await self._assert_blocked(self._make_cog(), "pular")

    async def test_fila_blocked_outside_allowed_channel(self):
        await self._assert_blocked(self._make_cog(), "fila")


if __name__ == "__main__":
    unittest.main()
