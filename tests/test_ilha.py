import sqlite3
import unittest
from datetime import datetime, timedelta

from economy_db import (
    ensure_v4_tables,
    finalize_island_construction,
    get_island,
    get_island_structures,
    get_island_unlocks,
    get_scrap,
    get_wallet,
    modify_scrap,
    modify_wallet,
    start_island_construction,
)
from cogs.ilha import (
    ISLAND_STRUCTURES,
    STRUCTURE_BUILD_HOURS_CAP,
    _structure_cost,
    get_island_bonuses,
    island_bonuses,
)


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE economy (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            wallet INTEGER DEFAULT 0,
            fish_count INTEGER DEFAULT 0,
            guild_rank TEXT DEFAULT 'F',
            guild_xp INTEGER DEFAULT 0,
            scrap INTEGER DEFAULT 0,
            inventory TEXT DEFAULT '{}',
            current_rod TEXT DEFAULT 'vara_bambu',
            rod_upgrades TEXT DEFAULT '{}',
            afk_trap TEXT DEFAULT '{}',
            last_fish TIMESTAMP,
            last_daily TIMESTAMP,
            last_explore TIMESTAMP,
            baits INTEGER DEFAULT 0
        )
        """
    )
    ensure_v4_tables(conn)
    return conn


def _force_ready(conn, user_id, structure_key):
    """Simula o tempo de construção já ter passado (sem esperar de verdade)."""
    past = (datetime.now() - timedelta(seconds=1)).timestamp()
    conn.execute(
        "UPDATE user_island_structures SET timer_end = ? WHERE user_id = ? AND structure_key = ?",
        (past, user_id, structure_key),
    )
    conn.commit()


class IslandCatalogTests(unittest.TestCase):
    def test_core_cost_scales_with_target_level(self):
        stats = ISLAND_STRUCTURES["nucleo"]
        c1 = _structure_cost("nucleo", 0)
        c2 = _structure_cost("nucleo", 1)
        self.assertEqual(c1["target_level"], 1)
        self.assertEqual(c1["cost_saches"], stats["cost_saches_per_level"] * 1)
        self.assertEqual(c2["target_level"], 2)
        self.assertEqual(c2["cost_saches"], stats["cost_saches_per_level"] * 2)

    def test_non_core_saches_double_per_level(self):
        stats = ISLAND_STRUCTURES["deposito"]
        for nivel_atual in range(stats["max_level"]):
            with self.subTest(nivel=nivel_atual + 1):
                c = _structure_cost("deposito", nivel_atual)
                self.assertEqual(c["cost_saches"], stats["cost_saches"] * 2 ** nivel_atual)

    def test_non_core_scrap_grows_linearly(self):
        """Sucata linear, não geométrica: as varas de tier alto têm 0-10% de
        lixo e quase não produzem sucata, então dobrá-la a cada nível faria
        dela a parede real da ilha no lugar do Sachê, que é o recurso que a
        expansão existe para drenar. Mesma assimetria da Forja do Abismo.
        """
        stats = ISLAND_STRUCTURES["farol"]
        for nivel_atual in range(stats["max_level"]):
            with self.subTest(nivel=nivel_atual + 1):
                c = _structure_cost("farol", nivel_atual)
                self.assertEqual(c["cost_scrap"], stats["cost_scrap"] * (nivel_atual + 1))

    def test_first_level_still_costs_the_original_build_price(self):
        """Regressão de compatibilidade: quem construiu antes da expansão não
        pode descobrir que o nível 1 ficou mais caro retroativamente."""
        for chave, saches, sucata in [
            ("deposito", 2400, 240),
            ("oficina", 4500, 450),
            ("farol", 7500, 750),
        ]:
            with self.subTest(estrutura=chave):
                c = _structure_cost(chave, 0)
                self.assertEqual(c["cost_saches"], saches)
                self.assertEqual(c["cost_scrap"], sucata)

    def test_every_non_core_structure_has_five_levels(self):
        for chave in ("deposito", "oficina", "farol"):
            with self.subTest(estrutura=chave):
                self.assertEqual(ISLAND_STRUCTURES[chave]["max_level"], 5)

    def test_camp_stays_the_single_progression_core(self):
        """O Acampamento não entrou na expansão: ele é a progressão (sobe o
        tier e libera as outras), não o sink. Encarecê-lo trava o acesso ao
        resto da ilha."""
        nucleo = ISLAND_STRUCTURES["nucleo"]
        self.assertTrue(nucleo["is_core"])
        self.assertEqual(nucleo["max_level"], 4)
        c1 = _structure_cost("nucleo", 0)
        c4 = _structure_cost("nucleo", 3)
        self.assertEqual(c4["cost_saches"], c1["cost_saches"] * 4)

    def test_full_expansion_totals(self):
        """O número que foi aprovado no balanceamento. Se alguém mexer numa
        base de custo sem revisar a proposta, este teste é o aviso."""
        total_saches = total_scrap = 0
        for chave in ("deposito", "oficina", "farol"):
            for nivel_atual in range(ISLAND_STRUCTURES[chave]["max_level"]):
                c = _structure_cost(chave, nivel_atual)
                total_saches += c["cost_saches"]
                total_scrap += c["cost_scrap"]
        self.assertEqual(total_saches, 446_400)
        self.assertEqual(total_scrap, 21_600)

    def test_build_hours_grow_per_level_but_are_capped(self):
        """Obra que atravessa dois dias deixa de ser progressão e vira
        castigo — o Farol nível 5 pediria 40h sem o teto."""
        stats = ISLAND_STRUCTURES["farol"]
        horas = [_structure_cost("farol", n)["build_hours"] for n in range(stats["max_level"])]
        self.assertEqual(horas[0], stats["build_hours"])
        self.assertEqual(horas, sorted(horas))
        self.assertLessEqual(max(horas), STRUCTURE_BUILD_HOURS_CAP)


class GetIslandTests(unittest.TestCase):
    def test_creates_default_tier_0_island_on_first_access(self):
        conn = _make_conn()
        island = get_island(conn, 1)
        self.assertEqual(island["tier"], 0)

        # Segunda leitura não duplica a linha nem reseta o tier.
        island2 = get_island(conn, 1)
        self.assertEqual(island2["tier"], 0)
        rows = conn.execute("SELECT COUNT(*) c FROM user_islands WHERE user_id = ?", (1,)).fetchone()
        self.assertEqual(rows["c"], 1)


class StartConstructionTests(unittest.TestCase):
    def test_insufficient_resources_refuses_without_debiting(self):
        conn = _make_conn()
        user_id = 10
        modify_wallet(conn, user_id, 100, "Tester")  # bem menos que o custo do núcleo

        cost = _structure_cost("nucleo", 0)
        result = start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "insufficient_resources")
        self.assertEqual(get_wallet(conn, user_id), 100)
        structures = get_island_structures(conn, user_id)
        self.assertNotIn("nucleo", structures)

    def test_successful_start_debits_resources_and_sets_building(self):
        conn = _make_conn()
        user_id = 11
        cost = _structure_cost("nucleo", 0)
        modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"])

        result = start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )

        self.assertTrue(result["success"])
        self.assertEqual(get_wallet(conn, user_id), 0)
        self.assertEqual(get_scrap(conn, user_id), 0)
        structures = get_island_structures(conn, user_id)
        self.assertEqual(structures["nucleo"]["status"], "building")
        self.assertEqual(structures["nucleo"]["level"], 0)  # nível só sobe ao finalizar

    def test_cannot_start_again_while_already_building(self):
        conn = _make_conn()
        user_id = 12
        cost = _structure_cost("nucleo", 0)
        modify_wallet(conn, user_id, cost["cost_saches"] * 5, "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"] * 5)

        first = start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )
        self.assertTrue(first["success"])
        wallet_after_first = get_wallet(conn, user_id)

        second = start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )
        self.assertFalse(second["success"])
        self.assertEqual(second["reason"], "already_building")
        # Não cobra de novo por uma tentativa recusada.
        self.assertEqual(get_wallet(conn, user_id), wallet_after_first)


class FinalizeConstructionTests(unittest.TestCase):
    def _start_nucleo(self, conn, user_id):
        cost = _structure_cost("nucleo", 0)
        modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"])
        start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )
        return cost

    def test_finalize_before_timer_ends_refuses(self):
        conn = _make_conn()
        user_id = 20
        self._start_nucleo(conn, user_id)

        result = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "not_ready")
        structures = get_island_structures(conn, user_id)
        self.assertEqual(structures["nucleo"]["status"], "building")

    def test_finalize_after_timer_forced_ready_raises_level_and_tier(self):
        conn = _make_conn()
        user_id = 21
        self._start_nucleo(conn, user_id)
        _force_ready(conn, user_id, "nucleo")

        result = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["level"], 1)
        self.assertEqual(result["tier"], 1)

        structures = get_island_structures(conn, user_id)
        self.assertEqual(structures["nucleo"]["status"], "idle")
        self.assertEqual(structures["nucleo"]["level"], 1)

        island = get_island(conn, user_id)
        self.assertEqual(island["tier"], 1)

        unlocks = get_island_unlocks(conn, user_id)
        self.assertIn("tier_1", unlocks)

    def test_double_finalize_only_applies_once(self):
        conn = _make_conn()
        user_id = 22
        self._start_nucleo(conn, user_id)
        _force_ready(conn, user_id, "nucleo")

        first = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertTrue(first["success"])

        second = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertFalse(second["success"])
        self.assertEqual(second["reason"], "not_building")

        island = get_island(conn, user_id)
        self.assertEqual(island["tier"], 1)  # não subiu de novo

    def test_non_core_structure_finalize_does_not_change_island_tier(self):
        conn = _make_conn()
        user_id = 23
        # Núcleo precisa chegar a tier 1 antes de "deposito" (unlock_tier=1) liberar.
        self._start_nucleo(conn, user_id)
        _force_ready(conn, user_id, "nucleo")
        finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)

        cost = _structure_cost("deposito", 0)
        modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"])
        start_result = start_island_construction(
            conn, user_id, "deposito", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"],
            required_tier=ISLAND_STRUCTURES["deposito"]["unlock_tier"],
        )
        self.assertTrue(start_result["success"])
        _force_ready(conn, user_id, "deposito")

        result = finalize_island_construction(conn, user_id, "deposito", 1, is_core=False)
        self.assertTrue(result["success"])
        self.assertIsNone(result["tier"])

        island = get_island(conn, user_id)
        self.assertEqual(island["tier"], 1)  # inalterado pelo depósito


class FullProgressionFlowTests(unittest.TestCase):
    def test_locked_structure_cannot_be_started_before_required_tier(self):
        conn = _make_conn()
        user_id = 30
        stats = ISLAND_STRUCTURES["deposito"]
        cost = _structure_cost("deposito", 0)
        modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"])

        # O gate de tier é reavaliado dentro da própria transação atômica de
        # start_island_construction (não só na UI) — mesmo se alguém tentar
        # pular a checagem da view, o helper recusa e não debita nada.
        result = start_island_construction(
            conn,
            user_id,
            "deposito",
            cost["target_level"],
            cost["cost_saches"],
            cost["cost_scrap"],
            cost["build_hours"],
            required_tier=stats["unlock_tier"],
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "locked")
        self.assertEqual(get_wallet(conn, user_id), cost["cost_saches"])  # nada debitado
        self.assertNotIn("deposito", get_island_structures(conn, user_id))

    def test_full_flow_create_build_wait_collect_tier_up(self):
        conn = _make_conn()
        user_id = 31

        island = get_island(conn, user_id)
        self.assertEqual(island["tier"], 0)

        cost = _structure_cost("nucleo", 0)
        modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"])
        start = start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )
        self.assertTrue(start["success"])

        # Ainda não passou o tempo: nada a coletar.
        premature = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertFalse(premature["success"])

        _force_ready(conn, user_id, "nucleo")
        done = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertTrue(done["success"])
        self.assertEqual(get_island(conn, user_id)["tier"], 1)


    def test_non_core_structure_can_be_upgraded_level_by_level(self):
        """O caminho novo da Fase 3: construir o Baú não é mais o fim da
        história — cada nível é uma obra própria, com custo próprio, e o bônus
        acompanha. A camada de banco já era agnóstica de nível; este teste
        fixa que o catálogo e a UI passaram a usar isso.
        """
        conn = _make_conn()
        user_id = 32
        # Ilha no tier 1 para destravar o Baú, sem depender do fluxo do núcleo.
        conn.execute("INSERT INTO user_islands (user_id, tier) VALUES (?, 1)", (user_id,))
        conn.commit()

        for nivel_alvo in (1, 2, 3):
            with self.subTest(nivel=nivel_alvo):
                cost = _structure_cost("deposito", nivel_alvo - 1)
                self.assertEqual(cost["target_level"], nivel_alvo)
                modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
                modify_scrap(conn, user_id, cost["cost_scrap"])

                start = start_island_construction(
                    conn,
                    user_id,
                    "deposito",
                    cost["target_level"],
                    cost["cost_saches"],
                    cost["cost_scrap"],
                    cost["build_hours"],
                    required_tier=ISLAND_STRUCTURES["deposito"]["unlock_tier"],
                )
                self.assertTrue(start["success"])
                # Pagou exatamente o custo do nível: nada sobrou, nada faltou.
                self.assertEqual(get_wallet(conn, user_id), 0)
                self.assertEqual(get_scrap(conn, user_id), 0)

                _force_ready(conn, user_id, "deposito")
                done = finalize_island_construction(conn, user_id, "deposito", nivel_alvo, is_core=False)
                self.assertTrue(done["success"])
                self.assertEqual(done["level"], nivel_alvo)
                # Evoluir o Baú não sobe o tier da ilha: isso é do núcleo.
                self.assertIsNone(done["tier"])
                self.assertEqual(get_island(conn, user_id)["tier"], 1)
                self.assertAlmostEqual(
                    get_island_bonuses(conn, user_id)["sucata_mult"], 1.0 + 0.25 * nivel_alvo
                )

class IslandBonusTests(unittest.TestCase):
    """Item 4: as construções deixaram de ser só custo e passaram a fazer algo.
    Fase 3: e passaram a escalar por nível, em vez de ligar e parar no 1.

    A regra de desenho que os testes fixam é que cada estrutura mexe num eixo
    diferente — quatro construções dando "+X% de renda" seriam a mesma
    construção quatro vezes, e a decisão de qual erguer primeiro sumiria.
    """

    def _estruturas(self, **niveis):
        return {
            chave: {"level": nivel, "status": "idle", "timer_end": None, "state_json": "{}"}
            for chave, nivel in niveis.items()
        }

    def test_no_structures_means_no_bonus(self):
        b = island_bonuses({})
        self.assertEqual(b["cd_reducao"], 0.0)
        self.assertEqual(b["sorte_bonus"], 0.0)
        self.assertEqual(b["sucata_mult"], 1.0)
        self.assertEqual(b["craft_mult"], 1.0)
        self.assertFalse(b["isca_diaria"])

    def test_camp_reduces_cooldown_by_two_percent_per_level(self):
        for nivel, esperado in [(1, 0.02), (2, 0.04), (3, 0.06), (4, 0.08)]:
            with self.subTest(nivel=nivel):
                b = island_bonuses(self._estruturas(nucleo=nivel))
                self.assertAlmostEqual(b["cd_reducao"], esperado)

    def test_each_structure_touches_a_different_axis(self):
        """A matriz tem que sair diagonal: uma estrutura, um eixo."""
        eixos = ["cd_reducao", "sorte_bonus", "sucata_mult", "craft_mult"]
        neutro = {"cd_reducao": 0.0, "sorte_bonus": 0.0, "sucata_mult": 1.0, "craft_mult": 1.0}
        por_estrutura = {
            "nucleo": "cd_reducao",
            "farol": "sorte_bonus",
            "deposito": "sucata_mult",
            "oficina": "craft_mult",
        }
        for chave, eixo_esperado in por_estrutura.items():
            b = island_bonuses(self._estruturas(**{chave: 1}))
            for eixo in eixos:
                with self.subTest(estrutura=chave, eixo=eixo):
                    if eixo == eixo_esperado:
                        self.assertNotEqual(b[eixo], neutro[eixo])
                    else:
                        self.assertEqual(b[eixo], neutro[eixo])

    def test_structure_under_construction_grants_nothing(self):
        """O nível só sobe em finalize_island_construction; obra em andamento
        não pode pagar bônus adiantado."""
        em_obra = {"nucleo": {"level": 0, "status": "building", "timer_end": 1, "state_json": "{}"}}
        self.assertEqual(island_bonuses(em_obra)["cd_reducao"], 0.0)

    def test_chest_adds_twenty_five_points_of_scrap_per_level(self):
        for nivel, esperado in [(1, 1.25), (2, 1.50), (3, 1.75), (4, 2.00), (5, 2.25)]:
            with self.subTest(nivel=nivel):
                b = island_bonuses(self._estruturas(deposito=nivel))
                self.assertAlmostEqual(b["sucata_mult"], esperado)

    def test_workshop_craft_discount_deepens_per_level_and_never_reaches_free(self):
        """Craft de graça apagaria a sucata como recurso — o desconto para em
        30% do custo, não em zero."""
        esperados = [(1, 0.50), (2, 0.45), (3, 0.40), (4, 0.35), (5, 0.30)]
        for nivel, esperado in esperados:
            with self.subTest(nivel=nivel):
                b = island_bonuses(self._estruturas(oficina=nivel))
                self.assertAlmostEqual(b["craft_mult"], esperado)
                self.assertGreater(b["craft_mult"], 0.0)

    def test_workshop_grants_one_daily_bait_per_level(self):
        for nivel in range(1, 6):
            with self.subTest(nivel=nivel):
                b = island_bonuses(self._estruturas(oficina=nivel))
                self.assertEqual(b["isca_diaria"], nivel)

    def test_lighthouse_adds_five_points_of_luck_per_level(self):
        for nivel, esperado in [(1, 0.10), (2, 0.15), (3, 0.20), (4, 0.25), (5, 0.30)]:
            with self.subTest(nivel=nivel):
                b = island_bonuses(self._estruturas(farol=nivel))
                self.assertAlmostEqual(b["sorte_bonus"], esperado)

    def test_level_one_pays_exactly_what_it_paid_before_the_expansion(self):
        """Regressão de compatibilidade: a expansão só acrescenta degraus
        acima. Quem já tinha as três construídas não pode acordar com menos
        bônus do que tinha ontem."""
        b = island_bonuses(self._estruturas(nucleo=4, deposito=1, oficina=1, farol=1))
        self.assertAlmostEqual(b["cd_reducao"], 0.08)
        self.assertAlmostEqual(b["sorte_bonus"], 0.10)
        self.assertEqual(b["sucata_mult"], 1.25)
        self.assertEqual(b["craft_mult"], 0.5)
        self.assertTrue(b["isca_diaria"])

    def test_every_structure_bonus_is_capped_at_its_max_level(self):
        """Nível gravado acima do teto (dado velho, correção de catálogo,
        admin) não pode pagar bônus que a estrutura não oferece."""
        for chave, eixo in [
            ("nucleo", "cd_reducao"),
            ("deposito", "sucata_mult"),
            ("oficina", "craft_mult"),
            ("farol", "sorte_bonus"),
        ]:
            teto = ISLAND_STRUCTURES[chave]["max_level"]
            no_teto = island_bonuses(self._estruturas(**{chave: teto}))
            acima = island_bonuses(self._estruturas(**{chave: teto + 3}))
            with self.subTest(estrutura=chave):
                self.assertAlmostEqual(acima[eixo], no_teto[eixo])

    def test_fully_upgraded_island_stacks_every_axis(self):
        b = island_bonuses(self._estruturas(nucleo=4, deposito=5, oficina=5, farol=5))
        self.assertAlmostEqual(b["cd_reducao"], 0.08)
        self.assertAlmostEqual(b["sorte_bonus"], 0.30)
        self.assertAlmostEqual(b["sucata_mult"], 2.25)
        self.assertAlmostEqual(b["craft_mult"], 0.30)
        self.assertEqual(b["isca_diaria"], 5)

    def test_upgrading_a_structure_raises_its_bonus_in_the_database(self):
        """O caminho completo: subir o nível gravado muda o bônus que os
        outros cogs leem, sem ninguém reimplementar a leitura."""
        conn = _make_conn()
        user_id = 5502
        conn.execute(
            "INSERT INTO user_island_structures (user_id, structure_key, level, status) "
            "VALUES (?, 'deposito', 1, 'idle')",
            (user_id,),
        )
        conn.commit()
        self.assertAlmostEqual(get_island_bonuses(conn, user_id)["sucata_mult"], 1.25)

        conn.execute(
            "UPDATE user_island_structures SET level = 4 WHERE user_id = ? AND structure_key = 'deposito'",
            (user_id,),
        )
        conn.commit()
        self.assertAlmostEqual(get_island_bonuses(conn, user_id)["sucata_mult"], 2.00)

    def test_reads_from_the_database(self):
        conn = _make_conn()
        user_id = 5501
        self.assertEqual(get_island_bonuses(conn, user_id)["sorte_bonus"], 0.0)

        conn.execute(
            "INSERT INTO user_island_structures (user_id, structure_key, level, status) "
            "VALUES (?, 'farol', 1, 'idle')",
            (user_id,),
        )
        conn.commit()
        self.assertAlmostEqual(get_island_bonuses(conn, user_id)["sorte_bonus"], 0.10)


if __name__ == "__main__":
    unittest.main()
