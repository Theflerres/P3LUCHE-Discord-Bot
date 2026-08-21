"""Testes do painel de status do terminal.

Cobrem o que é lógica: coleta de dados (com psutil/banco mockados), invariantes
da arte ASCII e a máquina de estado da animação. Não testam a renderização
visual em si — só um smoke test garantindo que a composição não explode.
"""
import os
import random
import sqlite3
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

import telemetry
from cogs import dashboard_animation as anim
from cogs import dashboard_art as art
from cogs.dashboard_panel import (
    DashboardData,
    LatencyTracker,
    build_dashboard,
    format_gateway_latency,
    collect_connection,
    collect_economy,
    collect_process_stats,
    format_number,
    format_uptime,
    render_tv_lines,
    should_redraw,
)


# ──────────────────────────────────────────────
#  ARTE — invariantes que o render assume
# ──────────────────────────────────────────────

class ArtInvariantTests(unittest.TestCase):
    """Se alguém redesenhar a TV, estes testes avisam antes de desalinhar."""

    def test_every_frame_line_has_the_declared_width(self):
        for index, line in enumerate(art.TV_FRAME):
            self.assertEqual(len(line), art.FRAME_WIDTH, f"linha {index}: {line!r}")

    def test_delta_art_matches_the_screen_rectangle(self):
        self.assertEqual(len(art.DELTA_ART), art.SCREEN_HEIGHT)
        for index, line in enumerate(art.DELTA_ART):
            self.assertEqual(len(line), art.SCREEN_WIDTH, f"linha {index}: {line!r}")

    def test_screen_rectangle_fits_inside_the_frame(self):
        self.assertLessEqual(art.SCREEN_LEFT + art.SCREEN_WIDTH, art.FRAME_WIDTH)
        self.assertLessEqual(art.SCREEN_TOP + art.SCREEN_HEIGHT, len(art.TV_FRAME))

    def test_render_preserves_frame_dimensions(self):
        frame = anim.Frame(anim.NORMAL_STATE, None, 1.0)
        lines = render_tv_lines(frame, random.Random(0))
        self.assertEqual(len(lines), len(art.TV_FRAME))
        for line in lines:
            self.assertEqual(len(line), art.FRAME_WIDTH)


# ──────────────────────────────────────────────
#  ANIMAÇÃO — máquina de estado
# ──────────────────────────────────────────────

class AnimationStateTests(unittest.TestCase):
    def _animation(self, **kwargs):
        kwargs.setdefault("rng", random.Random(1234))
        return anim.TVAnimation(**kwargs)

    def test_starts_in_normal_state(self):
        tv = self._animation()
        self.assertEqual(tv.state(now=100.0), anim.NORMAL_STATE)

    def test_error_notification_switches_to_error_state(self):
        tv = self._animation(error_hold_seconds=60)
        tv.notify_error(when=100.0)
        self.assertEqual(tv.state(now=100.5), anim.ERROR_STATE)

    def test_returns_to_normal_after_the_hold_window(self):
        tv = self._animation(error_hold_seconds=60)
        tv.notify_error(when=100.0)
        self.assertEqual(tv.state(now=159.0), anim.ERROR_STATE)
        self.assertEqual(tv.state(now=161.0), anim.NORMAL_STATE)

    def test_new_error_extends_the_hold_window(self):
        """Erro isolado passa; erro novo renova o vermelho."""
        tv = self._animation(error_hold_seconds=60)
        tv.notify_error(when=100.0)
        tv.notify_error(when=150.0)
        self.assertEqual(tv.state(now=180.0), anim.ERROR_STATE)
        self.assertEqual(tv.state(now=215.0), anim.NORMAL_STATE)

    def test_older_error_never_rewinds_the_state(self):
        tv = self._animation(error_hold_seconds=60)
        tv.notify_error(when=200.0)
        tv.notify_error(when=100.0)  # chegou fora de ordem
        self.assertEqual(tv.state(now=250.0), anim.ERROR_STATE)


class AnimationGlitchTests(unittest.TestCase):
    def test_no_glitch_when_the_roll_fails(self):
        tv = anim.TVAnimation(rng=random.Random(), normal_glitch_per_second=0.0)
        frame = tv.tick(now=1.0)
        self.assertFalse(frame.has_glitch)
        self.assertEqual(frame.state, anim.NORMAL_STATE)

    def test_glitch_always_fires_when_probability_is_one(self):
        tv = anim.TVAnimation(rng=random.Random(7), normal_glitch_per_second=1.0)
        frame = tv.tick(now=1.0)
        self.assertTrue(frame.has_glitch)
        self.assertIn(frame.glitch_type, anim.GLITCH_TYPES)

    def test_glitch_lasts_one_or_two_frames_then_clears(self):
        tv = anim.TVAnimation(rng=random.Random(3), normal_glitch_per_second=1.0)
        tv._p_normal = 1.0
        first = tv.tick(now=1.0)
        self.assertTrue(first.has_glitch)
        # Força a próxima rolagem a falhar para observar o fim do glitch atual.
        tv._p_normal = 0.0
        seen = [first.has_glitch]
        for _ in range(3):
            seen.append(tv.tick(now=1.0).has_glitch)
        self.assertTrue(seen[0])
        self.assertFalse(seen[-1], "glitch deveria ter terminado depois de 1-2 frames")

    def test_glitch_type_varies_across_occurrences(self):
        """Não pode repetir sempre o mesmo tipo — a graça é variar."""
        tv = anim.TVAnimation(rng=random.Random(42), normal_glitch_per_second=1.0)
        types = {tv.tick(now=float(i)).glitch_type for i in range(60)}
        self.assertGreater(len(types), 1)

    def test_error_state_glitches_more_than_normal(self):
        normal = anim.TVAnimation(
            rng=random.Random(99), normal_glitch_per_second=0.05, error_glitch_per_second=0.55
        )
        errored = anim.TVAnimation(
            rng=random.Random(99), normal_glitch_per_second=0.05, error_glitch_per_second=0.55
        )
        errored.notify_error(when=0.0)

        normal_hits = sum(1 for i in range(400) if normal.tick(now=float(i) / 8).has_glitch)
        error_hits = sum(1 for i in range(400) if errored.tick(now=float(i) / 8).has_glitch)
        self.assertGreater(error_hits, normal_hits * 3)

    def test_error_frames_carry_higher_intensity(self):
        tv = anim.TVAnimation(rng=random.Random(5))
        self.assertEqual(tv.tick(now=1.0).intensity, 1.0)
        tv.notify_error(when=1.0)
        self.assertEqual(tv.tick(now=1.5).intensity, 2.0)

    def test_per_frame_probability_matches_the_per_second_target(self):
        p = anim.per_frame_probability(0.05, fps=8)
        # 8 frames independentes devem reconstituir ~5% por segundo.
        self.assertAlmostEqual(1 - (1 - p) ** 8, 0.05, places=6)

    def test_per_frame_probability_is_safe_at_the_edges(self):
        self.assertEqual(anim.per_frame_probability(0.0, fps=8), 0.0)
        self.assertEqual(anim.per_frame_probability(1.0, fps=8), 1.0)
        self.assertEqual(anim.per_frame_probability(0.5, fps=0), 0.0)


class ApplyGlitchTests(unittest.TestCase):
    def setUp(self):
        self.rows = list(art.DELTA_ART)
        self.rng = random.Random(11)

    def test_every_glitch_type_preserves_dimensions(self):
        for glitch in anim.GLITCH_TYPES:
            with self.subTest(glitch=glitch):
                out = anim.apply_glitch(self.rows, glitch, random.Random(2), intensity=2.0)
                self.assertEqual(len(out), len(self.rows))
                for line in out:
                    self.assertEqual(len(line), art.SCREEN_WIDTH)

    def test_none_glitch_returns_unchanged_copy(self):
        out = anim.apply_glitch(self.rows, None, self.rng)
        self.assertEqual(out, self.rows)
        self.assertIsNot(out, self.rows)

    def test_glitch_actually_changes_something(self):
        for glitch in anim.GLITCH_TYPES:
            with self.subTest(glitch=glitch):
                out = anim.apply_glitch(self.rows, glitch, random.Random(4), intensity=2.0)
                self.assertNotEqual(out, self.rows)

    def test_empty_input_is_handled(self):
        self.assertEqual(anim.apply_glitch([], anim.GLITCH_STATIC, self.rng), [])


# ──────────────────────────────────────────────
#  TELEMETRIA
# ──────────────────────────────────────────────

class TelemetryTests(unittest.TestCase):
    def setUp(self):
        telemetry.reset()

    def tearDown(self):
        telemetry.reset()

    def test_records_and_returns_recent_interaction(self):
        now = datetime(2026, 8, 20, 12, 0, 0)
        telemetry.record_interaction("theflerres", "eco pescar", when=now)
        found = telemetry.recent_interactions(window_seconds=600, now=now)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["user_name"], "theflerres")
        self.assertEqual(found[0]["command_name"], "eco pescar")

    def test_interactions_outside_the_window_are_dropped(self):
        now = datetime(2026, 8, 20, 12, 0, 0)
        telemetry.record_interaction("antigo", "ajuda", when=now - timedelta(minutes=30))
        telemetry.record_interaction("recente", "ajuda", when=now - timedelta(minutes=2))
        found = telemetry.recent_interactions(window_seconds=600, now=now)
        self.assertEqual([e["user_name"] for e in found], ["recente"])

    def test_repeated_user_command_is_deduplicated_keeping_latest(self):
        now = datetime(2026, 8, 20, 12, 0, 0)
        for minutes in (5, 3, 1):
            telemetry.record_interaction("spam", "eco pescar", when=now - timedelta(minutes=minutes))
        found = telemetry.recent_interactions(window_seconds=600, now=now)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["when"], now - timedelta(minutes=1))

    def test_most_recent_interaction_comes_first(self):
        now = datetime(2026, 8, 20, 12, 0, 0)
        telemetry.record_interaction("a", "um", when=now - timedelta(minutes=5))
        telemetry.record_interaction("b", "dois", when=now - timedelta(minutes=1))
        found = telemetry.recent_interactions(window_seconds=600, now=now)
        self.assertEqual([e["user_name"] for e in found], ["b", "a"])

    def test_ordering_is_by_time_not_insertion_order(self):
        """A ordem do painel não pode depender da ordem em que os registros
        chegaram — só do horário deles."""
        now = datetime(2026, 8, 20, 12, 0, 0)
        telemetry.record_interaction("recente", "um", when=now - timedelta(minutes=1))
        telemetry.record_interaction("antigo", "dois", when=now - timedelta(minutes=8))
        found = telemetry.recent_interactions(window_seconds=600, now=now)
        self.assertEqual([e["user_name"] for e in found], ["recente", "antigo"])

    def test_last_error_at_uses_the_latest_timestamp_not_the_latest_insert(self):
        now = datetime(2026, 8, 20, 12, 0, 0)
        telemetry.record_error("mais novo", "ERROR", when=now)
        telemetry.record_error("mais velho", "ERROR", when=now - timedelta(minutes=5))
        self.assertEqual(telemetry.last_error_at(), now)

    def test_active_user_count_counts_distinct_users(self):
        now = datetime(2026, 8, 20, 12, 0, 0)
        telemetry.record_interaction("a", "um", when=now)
        telemetry.record_interaction("a", "dois", when=now)
        telemetry.record_interaction("b", "um", when=now)
        self.assertEqual(telemetry.active_user_count(600, now), 2)

    def test_error_count_respects_the_window(self):
        now = datetime(2026, 8, 20, 12, 0, 0)
        telemetry.record_error("velho", "ERROR", when=now - timedelta(hours=3))
        telemetry.record_error("novo", "ERROR", when=now - timedelta(minutes=10))
        self.assertEqual(telemetry.error_count(3600, now), 1)

    def test_last_error_at_ignores_warnings(self):
        """WARNING aparece na lista, mas não deixa a TV vermelha."""
        now = datetime(2026, 8, 20, 12, 0, 0)
        telemetry.record_error("apenas aviso", "WARNING", when=now)
        self.assertIsNone(telemetry.last_error_at())
        telemetry.record_error("pau de verdade", "ERROR", when=now)
        self.assertEqual(telemetry.last_error_at(), now)

    def test_error_message_is_flattened_to_one_line(self):
        telemetry.record_error("linha 1\nlinha 2", "ERROR")
        self.assertNotIn("\n", telemetry.recent_errors(1)[0]["message"])

    def test_buffers_are_bounded(self):
        for i in range(telemetry.MAX_ERRORS + 25):
            telemetry.record_error(f"erro {i}", "ERROR")
        self.assertEqual(len(telemetry.recent_errors(limit=None)), telemetry.MAX_ERRORS)

    def test_log_handler_feeds_telemetry_from_the_existing_logger(self):
        """A captura pendura no logger que o erros.py já usa."""
        import logging

        handler = telemetry.attach_error_capture("TESTE_ERROS")
        try:
            logging.getLogger("TESTE_ERROS").error("explodiu tudo\ncom stack trace")
        finally:
            telemetry.detach_error_capture(handler, "TESTE_ERROS")

        errors = telemetry.recent_errors(1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["level"], "ERROR")
        self.assertEqual(errors[0]["message"], "explodiu tudo")

    def test_log_to_gui_errors_reach_the_panel(self):
        """Erros operacionais (backup do Drive, FFmpeg, carga de extensão) só
        passam pelo log_to_gui — sem isto o painel não os veria."""
        from utils import log_to_gui

        log_to_gui("Falha no backup automático: invalid_grant", "ERROR")
        errors = telemetry.recent_errors(1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["level"], "ERROR")
        self.assertIn("invalid_grant", errors[0]["message"])

    def test_log_to_gui_error_turns_the_tv_red(self):
        from utils import log_to_gui

        log_to_gui("Falha no backup automático: invalid_grant", "ERROR")
        self.assertIsNotNone(telemetry.last_error_at())

    def test_log_to_gui_warning_is_listed_but_does_not_turn_the_tv_red(self):
        from utils import log_to_gui

        log_to_gui("CANAL_APROVACAO_LORE_ID não configurado.", "WARNING")
        self.assertEqual(len(telemetry.recent_errors(1)), 1)
        self.assertIsNone(telemetry.last_error_at())

    def test_log_to_gui_success_and_info_are_not_recorded(self):
        from utils import log_to_gui

        log_to_gui("Extensão carregada: cogs.economia", "SUCCESS")
        log_to_gui("Sincronizado 23 comandos.", "INFO")
        self.assertEqual(telemetry.recent_errors(limit=None), [])

    def test_log_handler_ignores_levels_below_warning(self):
        import logging

        handler = telemetry.attach_error_capture("TESTE_ERROS_INFO")
        try:
            logging.getLogger("TESTE_ERROS_INFO").info("rotina normal")
        finally:
            telemetry.detach_error_capture(handler, "TESTE_ERROS_INFO")
        self.assertEqual(telemetry.recent_errors(limit=None), [])


# ──────────────────────────────────────────────
#  COLETORES DO PAINEL
# ──────────────────────────────────────────────

class FormattingTests(unittest.TestCase):
    def test_uptime_without_days(self):
        self.assertEqual(format_uptime(timedelta(hours=3, minutes=14, seconds=22)), "03:14:22")

    def test_uptime_with_days(self):
        self.assertEqual(format_uptime(timedelta(days=2, hours=3, minutes=4, seconds=5)), "2d 03:04:05")

    def test_negative_uptime_is_clamped(self):
        self.assertEqual(format_uptime(timedelta(seconds=-50)), "00:00:00")

    def test_number_uses_pt_br_thousands(self):
        self.assertEqual(format_number(1234567), "1.234.567")

    def test_number_handles_missing_value(self):
        self.assertEqual(format_number(None), "n/d")
        self.assertEqual(format_number("nada"), "n/d")


class CollectConnectionTests(unittest.TestCase):
    def _bot(self, latency=0.042, ready=True, closed=False, start_time=None):
        return SimpleNamespace(
            latency=latency,
            is_ready=lambda: ready,
            is_closed=lambda: closed,
            start_time=start_time,
        )

    def test_uptime_uses_bot_start_time_when_available(self):
        now = datetime(2026, 8, 20, 12, 0, 0)
        bot = self._bot(start_time=now - timedelta(hours=1))
        self.assertEqual(collect_connection(bot, now)["uptime"], timedelta(hours=1))

    def test_uptime_falls_back_to_process_start_before_ready(self):
        now = datetime(2026, 8, 20, 12, 0, 0)
        bot = self._bot(start_time=None)
        result = collect_connection(bot, now, process_start=now - timedelta(minutes=5))
        self.assertEqual(result["uptime"], timedelta(minutes=5))

    def test_latency_is_converted_to_milliseconds(self):
        self.assertEqual(collect_connection(self._bot(latency=0.042))["latency_ms"], 42)

    def test_nan_latency_before_first_heartbeat_is_reported_as_unknown(self):
        self.assertIsNone(collect_connection(self._bot(latency=float("nan")))["latency_ms"])

    def test_infinite_latency_is_reported_as_unknown(self):
        self.assertIsNone(collect_connection(self._bot(latency=float("inf")))["latency_ms"])

    def test_closed_connection_is_not_reported_as_connected(self):
        self.assertFalse(collect_connection(self._bot(ready=True, closed=True))["connected"])

    def test_not_ready_is_not_reported_as_connected(self):
        self.assertFalse(collect_connection(self._bot(ready=False))["connected"])

    def test_broken_bot_object_does_not_raise(self):
        class Broken:
            start_time = None

            @property
            def latency(self):
                raise RuntimeError("gateway sumiu")

            def is_ready(self):
                raise RuntimeError("idem")

            def is_closed(self):
                return False

        result = collect_connection(Broken(), datetime(2026, 8, 20, 12, 0, 0))
        self.assertIsNone(result["latency_ms"])
        self.assertFalse(result["connected"])


class CollectProcessStatsTests(unittest.TestCase):
    def test_reads_cpu_and_memory_from_the_process(self):
        process = SimpleNamespace(
            cpu_percent=lambda _=None: 12.5,
            memory_info=lambda: SimpleNamespace(rss=256 * 1024 * 1024),
            memory_percent=lambda: 3.25,
        )
        stats = collect_process_stats(process)
        self.assertEqual(stats["cpu_percent"], 12.5)
        self.assertAlmostEqual(stats["ram_mb"], 256.0)
        self.assertAlmostEqual(stats["ram_percent"], 3.25)

    def test_partial_failure_degrades_to_none_without_raising(self):
        def boom():
            raise RuntimeError("psutil falhou")

        process = SimpleNamespace(
            cpu_percent=lambda _=None: 5.0,
            memory_info=boom,
            memory_percent=boom,
        )
        stats = collect_process_stats(process)
        self.assertEqual(stats["cpu_percent"], 5.0)
        self.assertIsNone(stats["ram_mb"])
        self.assertIsNone(stats["ram_percent"])


class CollectEconomyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, wallet INTEGER DEFAULT 0);
            CREATE TABLE user_cooldowns (
                user_id INTEGER PRIMARY KEY,
                last_fish TIMESTAMP,
                last_daily TIMESTAMP,
                last_explore TIMESTAMP
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_totals_players_and_sachets(self):
        self.conn.executemany("INSERT INTO users VALUES (?,?)", [(1, 100), (2, 250), (3, 0)])
        result = collect_economy(self.conn, datetime(2026, 8, 20, 12, 0, 0))
        self.assertEqual(result["total_players"], 3)
        self.assertEqual(result["total_sachets"], 350)

    def test_counts_only_players_active_today(self):
        now = datetime(2026, 8, 20, 12, 0, 0)
        self.conn.executemany("INSERT INTO users VALUES (?,?)", [(1, 0), (2, 0), (3, 0)])
        self.conn.executemany(
            "INSERT INTO user_cooldowns VALUES (?,?,?,?)",
            [
                (1, "2026-08-20 09:15:00.000000", None, None),          # pescou hoje
                (2, None, None, "2026-08-19 23:59:59.000000"),          # ontem
                (3, None, "2026-08-20 11:00:00.000000", None),          # diário hoje
            ],
        )
        self.assertEqual(collect_economy(self.conn, now)["active_today"], 2)

    def test_empty_database_reports_zeros_not_none(self):
        result = collect_economy(self.conn, datetime(2026, 8, 20, 12, 0, 0))
        self.assertEqual(result["total_players"], 0)
        self.assertEqual(result["total_sachets"], 0)
        self.assertEqual(result["active_today"], 0)

    def test_missing_connection_degrades_gracefully(self):
        result = collect_economy(None, datetime(2026, 8, 20, 12, 0, 0))
        self.assertIsNone(result["total_players"])

    def test_missing_tables_degrade_gracefully(self):
        empty = sqlite3.connect(":memory:")
        try:
            result = collect_economy(empty, datetime(2026, 8, 20, 12, 0, 0))
            self.assertIsNone(result["total_players"])
        finally:
            empty.close()


class DashboardDataCacheTests(unittest.TestCase):
    """O refresh roda a 8 fps: as partes caras não podem ser recalculadas sempre."""

    class _CountingConn:
        def __init__(self):
            self.calls = 0

        def execute(self, *args, **kwargs):
            self.calls += 1
            raise sqlite3.OperationalError("sem tabelas")  # o valor não importa aqui

    def test_economy_is_queried_once_within_the_ttl(self):
        conn = self._CountingConn()
        data = DashboardData()
        base = datetime(2026, 8, 20, 12, 0, 0)
        for offset in range(0, 40, 2):
            data.economy(conn, base + timedelta(seconds=offset))
        self.assertEqual(conn.calls, 1)

    def test_economy_is_requeried_after_the_ttl(self):
        conn = self._CountingConn()
        data = DashboardData()
        base = datetime(2026, 8, 20, 12, 0, 0)
        data.economy(conn, base)
        data.economy(conn, base + timedelta(seconds=46))
        self.assertEqual(conn.calls, 2)

    def test_process_stats_are_cached_between_frames(self):
        calls = []

        def cpu_percent(_=None):
            calls.append(1)
            return 1.0

        process = SimpleNamespace(
            cpu_percent=cpu_percent,
            memory_info=lambda: SimpleNamespace(rss=1024),
            memory_percent=lambda: 0.1,
        )
        data = DashboardData(process=process)
        base = datetime(2026, 8, 20, 12, 0, 0)
        # Oito frames dentro do mesmo segundo -> uma leitura só.
        for frame_index in range(8):
            data.process_stats(base + timedelta(milliseconds=125 * frame_index))
        self.assertEqual(len(calls), 1)

    def test_absent_process_reports_unknown_without_raising(self):
        stats = DashboardData(process=None).process_stats(datetime(2026, 8, 20, 12, 0, 0))
        self.assertIsNone(stats["cpu_percent"])
        self.assertIsNone(stats["ram_mb"])


class ShouldRedrawTests(unittest.TestCase):
    """O loop roda a 8 fps, mas quase todo frame em repouso é redundante."""

    def setUp(self):
        self.second = datetime(2026, 8, 20, 12, 0, 0)

    def test_skips_redundant_frames_within_the_same_second(self):
        self.assertFalse(
            should_redraw(
                has_glitch=False, was_glitching=False,
                current_second=self.second, last_second=self.second,
            )
        )

    def test_redraws_when_the_second_changes(self):
        self.assertTrue(
            should_redraw(
                has_glitch=False, was_glitching=False,
                current_second=self.second + timedelta(seconds=1), last_second=self.second,
            )
        )

    def test_redraws_every_glitch_frame(self):
        self.assertTrue(
            should_redraw(
                has_glitch=True, was_glitching=False,
                current_second=self.second, last_second=self.second,
            )
        )

    def test_redraws_once_more_to_restore_the_clean_image(self):
        """Sem isto, a tela ficaria congelada no último frame do glitch até o
        segundo virar."""
        self.assertTrue(
            should_redraw(
                has_glitch=False, was_glitching=True,
                current_second=self.second, last_second=self.second,
            )
        )

    def test_first_frame_always_draws(self):
        self.assertTrue(
            should_redraw(
                has_glitch=False, was_glitching=False,
                current_second=self.second, last_second=None,
            )
        )


class ExtensionReloadTests(unittest.TestCase):
    """Regressão do painel que nunca desenhava.

    `bot.load_extension` não reaproveita o módulo já importado — ele faz
    `module_from_spec` + `exec_module`, criando um objeto NOVO e re-executando o
    arquivo. Estado guardado nos globais da extensão passa a existir em duas
    cópias: o `main.py` escrevia numa e o cog lia da outra (sempre None).
    """

    def _reexecute_like_load_extension(self, module_name):
        import importlib.util

        spec = importlib.util.find_spec(module_name)
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        return fresh

    def test_live_session_survives_extension_reexecution(self):
        from cogs import dashboard_runtime as runtime

        sentinel = object()
        original = runtime._live
        runtime._live = sentinel
        try:
            fresh = self._reexecute_like_load_extension("cogs.dashboard")
            self.assertIs(
                fresh.get_live(),
                sentinel,
                "o cog re-executado perdeu a sessão iniciada pelo main.py",
            )
        finally:
            runtime._live = original

    def test_extension_module_does_not_own_the_live_state(self):
        """Se `_live` voltar a morar na extensão, o bug volta junto."""
        from cogs import dashboard

        self.assertFalse(
            hasattr(dashboard, "_live"),
            "estado do Live não pode viver no módulo de extensão — use dashboard_runtime",
        )

    def test_process_start_is_stable_across_reexecution(self):
        from cogs import dashboard_runtime as runtime

        fresh = self._reexecute_like_load_extension("cogs.dashboard")
        self.assertIs(fresh.PROCESS_START, runtime.PROCESS_START)


class IsEnabledTests(unittest.TestCase):
    def setUp(self):
        from cogs import dashboard_runtime as runtime

        self.runtime = runtime
        self._original = os.environ.get(runtime.ENV_FLAG)

    def tearDown(self):
        if self._original is None:
            os.environ.pop(self.runtime.ENV_FLAG, None)
        else:
            os.environ[self.runtime.ENV_FLAG] = self._original

    def test_env_flag_name_is_the_documented_one(self):
        self.assertEqual(self.runtime.ENV_FLAG, "P3LUCHE_DASHBOARD")

    def test_truthy_values_enable_it(self):
        for value in ("1", "true", "TRUE", "on", "yes", "sim"):
            with self.subTest(value=value):
                os.environ[self.runtime.ENV_FLAG] = value
                self.assertTrue(self.runtime.is_enabled())

    def test_falsy_values_disable_it_even_on_a_tty(self):
        for value in ("0", "false", "off", "no", ""):
            with self.subTest(value=value):
                os.environ[self.runtime.ENV_FLAG] = value
                self.assertFalse(self.runtime.is_enabled())

    def test_powershell_style_value_with_whitespace_is_accepted(self):
        os.environ[self.runtime.ENV_FLAG] = " 1 "
        self.assertTrue(self.runtime.is_enabled())


class LatencyTrackerTests(unittest.TestCase):
    """O gateway só manda heartbeat a cada ~41s, então bot.latency fica
    congelado entre batidas. O rastreador existe para o painel poder mostrar
    HÁ QUANTO TEMPO aquela leitura está parada — foi a ausência disso que fez
    um valor normal parecer um problema de performance.
    """

    def setUp(self):
        self.tracker = LatencyTracker()
        self.t0 = datetime(2026, 8, 21, 12, 0, 0)

    def test_starts_without_a_reading(self):
        self.assertIsNone(self.tracker.value)
        self.assertIsNone(self.tracker.age_seconds(self.t0))

    def test_records_first_reading(self):
        self.tracker.update(137, self.t0)
        self.assertEqual(self.tracker.value, 137)
        self.assertEqual(self.tracker.age_seconds(self.t0), 0)

    def test_age_grows_while_the_value_stays_frozen(self):
        self.tracker.update(137, self.t0)
        # Painel redesenha a cada segundo com o MESMO valor: não é leitura nova.
        for offset in range(1, 40):
            self.tracker.update(137, self.t0 + timedelta(seconds=offset))
        self.assertEqual(self.tracker.age_seconds(self.t0 + timedelta(seconds=39)), 39)

    def test_new_heartbeat_resets_the_age(self):
        self.tracker.update(137, self.t0)
        self.tracker.update(141, self.t0 + timedelta(seconds=41))
        self.assertEqual(self.tracker.value, 141)
        self.assertEqual(self.tracker.age_seconds(self.t0 + timedelta(seconds=41)), 0)

    def test_tracks_the_observed_minimum(self):
        for value, offset in ((178, 0), (141, 41), (137, 82)):
            self.tracker.update(value, self.t0 + timedelta(seconds=offset))
        self.assertEqual(self.tracker.observed_min, 137)

    def test_history_is_bounded(self):
        for i in range(LatencyTracker.HISTORY + 30):
            self.tracker.update(100 + i, self.t0 + timedelta(seconds=i))
        self.assertLessEqual(len(self.tracker._history), LatencyTracker.HISTORY)

    def test_none_latency_is_ignored(self):
        self.tracker.update(137, self.t0)
        self.tracker.update(None, self.t0 + timedelta(seconds=5))
        self.assertEqual(self.tracker.value, 137)

    def test_age_never_goes_negative(self):
        self.tracker.update(137, self.t0)
        self.assertEqual(self.tracker.age_seconds(self.t0 - timedelta(seconds=10)), 0)


class FormatGatewayLatencyTests(unittest.TestCase):
    def setUp(self):
        self.tracker = LatencyTracker()
        self.t0 = datetime(2026, 8, 21, 12, 0, 0)

    def test_shows_waiting_before_the_first_heartbeat(self):
        rendered = format_gateway_latency(self.tracker, self.t0).plain
        self.assertIn("aguardando", rendered)

    def test_shows_value_and_age(self):
        self.tracker.update(137, self.t0)
        rendered = format_gateway_latency(self.tracker, self.t0 + timedelta(seconds=12)).plain
        self.assertIn("137 ms", rendered)
        self.assertIn("há 12s", rendered)

    def test_shows_observed_minimum_when_current_reading_is_above_it(self):
        """O caso que importa: a batida atual veio alta (ex.: a primeira, medida
        durante o startup) e o mínimo observado mostra o piso real."""
        self.tracker.update(137, self.t0)
        self.tracker.update(178, self.t0 + timedelta(seconds=41))
        rendered = format_gateway_latency(self.tracker, self.t0 + timedelta(seconds=45)).plain
        self.assertIn("178 ms", rendered)
        self.assertIn("mín 137", rendered)

    def test_omits_minimum_when_it_equals_the_current_value(self):
        self.tracker.update(137, self.t0)
        rendered = format_gateway_latency(self.tracker, self.t0).plain
        self.assertNotIn("mín", rendered)


class BuildDashboardSmokeTests(unittest.TestCase):
    """Não valida aparência — só garante que a composição não levanta exceção."""

    def _build(self, frame):
        return build_dashboard(
            frame=frame,
            rng=random.Random(0),
            connection={"uptime": timedelta(hours=2), "latency_ms": 55, "connected": True},
            stats={"cpu_percent": 4.2, "ram_mb": 180.0, "ram_percent": 2.1},
            economy={"total_players": 11, "active_today": 3, "total_sachets": 98765},
            interactions=[
                {"when": datetime(2026, 8, 20, 12, 0, 0), "user_name": "theflerres", "command_name": "eco pescar"}
            ],
            errors=[{"when": datetime(2026, 8, 20, 11, 59, 0), "level": "ERROR", "message": "algo quebrou"}],
            error_count_hour=1,
        )

    def test_builds_in_normal_state(self):
        self.assertIsNotNone(self._build(anim.Frame(anim.NORMAL_STATE, None, 1.0)))

    def test_builds_in_error_state_with_glitch(self):
        frame = anim.Frame(anim.ERROR_STATE, anim.GLITCH_STATIC, 2.0)
        self.assertIsNotNone(self._build(frame))

    def test_builds_with_empty_activity_and_errors(self):
        renderable = build_dashboard(
            frame=anim.Frame(anim.NORMAL_STATE, None, 1.0),
            rng=random.Random(0),
            connection={"uptime": timedelta(0), "latency_ms": None, "connected": False},
            stats={"cpu_percent": None, "ram_mb": None, "ram_percent": None},
            economy={"total_players": None, "active_today": None, "total_sachets": None},
            interactions=[],
            errors=[],
            error_count_hour=0,
        )
        self.assertIsNotNone(renderable)


if __name__ == "__main__":
    unittest.main()
