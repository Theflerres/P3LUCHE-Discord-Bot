"""Regressões das correções de bloqueio do event loop.

Contexto: uma investigação de latência alta mediu o loop e descobriu que ele
NÃO estava bloqueado em regime (p99 de 14ms) — a latência era o RTT real do
gateway. Mas a medição encontrou dois bloqueios genuínos, corrigidos aqui:

  B) `get_thumbnail_dominant_color` rodava no loop (requests até 5s + cálculo
     quadrático de cor) durante /musica adicionar.
  C) matplotlib/networkx/scipy eram importados no topo de cogs/lore_ai.py,
     bloqueando o loop por ~1s no startup mesmo para quem nunca usa /lore grafo.
"""
import ast
import inspect
import pathlib
import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

import utils


def _png_bytes(colors):
    """Gera um PNG onde `colors` é uma lista de (cor_rgb, quantidade)."""
    pixels = []
    for color, count in colors:
        pixels.extend([color] * count)
    side = int(len(pixels) ** 0.5)
    img = Image.new("RGB", (side, side))
    img.putdata(pixels[: side * side])
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class DominantColorTests(unittest.TestCase):
    """B — o cálculo continua correto depois de sair do O(n²)."""

    def _fetch(self, payload):
        class FakeResponse:
            content = payload

        with patch.object(utils.requests, "get", return_value=FakeResponse()):
            return utils.get_thumbnail_dominant_color("http://exemplo/t.png")

    def test_returns_the_most_frequent_color(self):
        # 3/4 vermelho, 1/4 azul -> vermelho domina.
        payload = _png_bytes([((255, 0, 0), 300), ((0, 0, 255), 100)])
        color = self._fetch(payload)
        self.assertEqual((color.r, color.g, color.b), (255, 0, 0))

    def test_solid_image_returns_that_color(self):
        payload = _png_bytes([((10, 200, 30), 400)])
        color = self._fetch(payload)
        self.assertEqual((color.r, color.g, color.b), (10, 200, 30))

    def test_network_failure_falls_back_to_blurple(self):
        with patch.object(utils.requests, "get", side_effect=OSError("sem rede")):
            color = utils.get_thumbnail_dominant_color("http://exemplo/t.png")
        import discord

        self.assertEqual(color, discord.Color.blurple())

    def test_garbage_payload_falls_back_to_blurple(self):
        color = self._fetch(b"isto nao e uma imagem")
        import discord

        self.assertEqual(color, discord.Color.blurple())

    def test_no_quadratic_pixel_count_remains(self):
        """`max(set(pixels), key=pixels.count)` varria a lista uma vez por cor
        distinta — com 2500 pixels quase todos únicos, milhões de comparações.

        Checagem via AST em vez de texto: o comentário que documenta o código
        antigo cita `pixels.count` e daria falso positivo numa busca literal.
        """
        tree = ast.parse(inspect.getsource(utils.get_thumbnail_dominant_color))

        counts = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "count"
        ]
        self.assertEqual(counts, [], "contagem por cor distinta é O(n²); use getcolors")

        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("getcolors", calls)


class ThumbnailCallSiteTests(unittest.TestCase):
    """B — a chamada precisa continuar fora do loop."""

    def test_musica_calls_it_through_to_thread(self):
        source = pathlib.Path("src/p3luche/cogs/musica.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        wrapped = False
        for node in ast.walk(tree):
            # Procura: asyncio.to_thread(get_thumbnail_dominant_color, ...)
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "to_thread"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id == "get_thumbnail_dominant_color":
                    wrapped = True
        self.assertTrue(
            wrapped,
            "get_thumbnail_dominant_color precisa ser chamada via asyncio.to_thread "
            "— direto no loop ela trava o bot por até 5s (requests + decode)",
        )


class LazyGraphImportTests(unittest.TestCase):
    """C — os imports pesados não podem voltar para o topo do módulo."""

    HEAVY = ("matplotlib", "networkx", "scipy")

    def _module_level_imports(self, path):
        tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
        names = set()
        for node in tree.body:  # só o nível do módulo, não o corpo de funções
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def test_lore_ai_does_not_import_heavy_libs_at_module_level(self):
        top = self._module_level_imports("src/p3luche/cogs/lore_ai.py")
        for lib in self.HEAVY:
            with self.subTest(lib=lib):
                self.assertNotIn(
                    lib,
                    top,
                    f"{lib} no topo de lore_ai.py bloqueia o event loop no startup "
                    f"(load_extension); importe dentro de _generate_graph_image",
                )

    def test_graph_generator_still_has_access_to_them(self):
        from cogs import lore_ai

        source = inspect.getsource(lore_ai._generate_graph_image)
        for lib in self.HEAVY:
            with self.subTest(lib=lib):
                self.assertIn(lib, source)

    def test_importing_the_cog_does_not_pull_matplotlib(self):
        """Prova o ganho real: carregar a cog não paga o custo do matplotlib."""
        import subprocess
        import sys

        code = (
            "import sys; sys.path.insert(0, 'src/p3luche');"
            "import cogs.lore_ai;"
            "print('matplotlib' in sys.modules)"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "False", result.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
