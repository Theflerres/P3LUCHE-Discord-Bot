"""Recálculo de rank de guilda após o fix do limiar de promoção.

O script roda uma vez contra o banco real, então a cobertura aqui é o
substituto de um ensaio em produção: banco temporário populado com vários
perfis de XP, dry-run conferido linha a linha, e só então a escrita.
"""
import os
import sqlite3
import tempfile
import unittest

from cogs.economia import GUILD_RANKS
from economy_db import ensure_v4_tables
from migration_rank_fix import (
    MigrationAlreadyApplied,
    LADDER,
    buggy_spent,
    correct_boundaries,
    rank_for,
    apply,
    plan,
)


# (nome, rank gravado, xp residual gravado, rank esperado, xp esperado)
#
# O acumulado reconstruído é `cobrado_pela_formula_antiga[rank] + xp`, e o
# rank novo é a maior fronteira correta que ele alcança:
#   cobrado antigo: F=0  E=0  D=500  C=2000  B=6000  A=16000
#   fronteira nova: F=0  E=500 D=2000 C=6000  B=16000 A=41000
PERFIS = [
    # Conta zerada: nada muda.
    ("novato", "F", 0, "F", 0),
    # Rank F com XP suficiente para o degrau novo (500): sobe de verdade.
    ("f_no_limiar", "F", 500, "E", 0),
    ("f_acima_do_limiar", "F", 900, "E", 400),
    # Rank E veio de graça no fluxo antigo (req_xp de F é 0): acumulado = xp.
    ("e_recem_promovido", "E", 0, "F", 0),
    ("e_com_pouco_xp", "E", 108, "F", 108),
    ("e_quase_la", "E", 499, "F", 499),
    ("e_no_limiar", "E", 500, "E", 0),
    # Ranks do meio: cai um degrau, resíduo preservado.
    ("d_tipico", "D", 719, "E", 719),
    ("c_tipico", "C", 3553, "D", 3553),
    ("b_tipico", "B", 386, "C", 386),
    # Resíduo alto o bastante para segurar o rank atual.
    ("c_segura_o_rank", "C", 4000, "C", 0),
    ("b_segura_o_rank", "B", 10000, "B", 0),
    # Topo da escada.
    ("a_no_topo", "A", 0, "B", 0),
    ("a_com_muito_xp", "A", 25000, "A", 0),
]


def _make_db(perfis=PERFIS):
    """Banco temporário com a tabela `users` populada."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="p3luche-rankfix-")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_v4_tables(conn)
    for i, (nome, rank, xp, _, _) in enumerate(perfis, start=1):
        conn.execute(
            "INSERT INTO users (user_id, user_name, guild_rank, guild_xp, fish_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (1000 + i, nome, rank, xp, i),
        )
    conn.commit()
    conn.close()
    return path


class TabelasDaEscadaTests(unittest.TestCase):
    def test_correct_boundaries_are_the_cumulative_sum_of_req_xp(self):
        b = correct_boundaries()
        self.assertEqual(b, {"F": 0, "E": 500, "D": 2000, "C": 6000, "B": 16000, "A": 41000})
        self.assertEqual(b["A"], sum(GUILD_RANKS[r]["req_xp"] for r in LADDER[1:]))

    def test_buggy_spent_is_the_boundary_of_the_previous_rank(self):
        """A fórmula antiga cobrava um degrau adiantado — é isso que torna a
        reconstrução do acumulado possível."""
        b, s = correct_boundaries(), buggy_spent()
        for anterior, atual in zip(LADDER, LADDER[1:]):
            self.assertEqual(s[atual], b[anterior], f"{atual} veio de {anterior}")

    def test_rank_for_picks_the_highest_reached_boundary(self):
        b = correct_boundaries()
        self.assertEqual(rank_for(0, b), "F")
        self.assertEqual(rank_for(499, b), "F")
        self.assertEqual(rank_for(500, b), "E")
        self.assertEqual(rank_for(40999, b), "B")
        self.assertEqual(rank_for(41000, b), "A")
        self.assertEqual(rank_for(10 ** 9, b), "A")


class PlanoDeRecalculoTests(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.addCleanup(lambda: os.path.exists(self.db) and os.remove(self.db))

    def test_every_profile_lands_on_the_expected_rank_and_residual(self):
        por_nome = {m["user_name"]: m for m in plan(self.db)["mudancas"]}
        for nome, _, _, rank_esperado, xp_esperado in PERFIS:
            with self.subTest(perfil=nome):
                m = por_nome[nome]
                self.assertEqual(m["rank_novo"], rank_esperado)
                self.assertEqual(m["xp_novo"], xp_esperado)

    def test_buckets_add_up_to_the_total(self):
        r = plan(self.db)
        soma = len(r["mantem"]) + len(r["sobem"]) + len(r["caem"]) + len(r["sem_escada"])
        self.assertEqual(soma, r["total"])
        self.assertEqual(r["total"], len(PERFIS))

    def test_accumulated_xp_is_never_destroyed(self):
        """O invariante que separa a reconstrução da leitura literal: o
        acumulado depois tem que ser igual ao acumulado antes."""
        s = buggy_spent()
        b = correct_boundaries()
        for m in plan(self.db)["mudancas"]:
            with self.subTest(perfil=m["user_name"]):
                antes = s[m["rank_antigo"]] + m["xp_antigo"]
                depois = b[m["rank_novo"]] + m["xp_novo"]
                self.assertEqual(antes, depois)

    def test_residual_stays_inside_the_new_rank(self):
        """XP novo nunca pode ser suficiente para outra promoção imediata."""
        for m in plan(self.db)["mudancas"]:
            with self.subTest(perfil=m["user_name"]):
                self.assertGreaterEqual(m["xp_novo"], 0)
                if m["rank_novo"] != "A":
                    proximo = LADDER[LADDER.index(m["rank_novo"]) + 1]
                    self.assertLess(m["xp_novo"], GUILD_RANKS[proximo]["req_xp"])

    def test_nobody_drops_more_than_one_step(self):
        for m in plan(self.db)["caem"]:
            with self.subTest(perfil=m["user_name"]):
                self.assertEqual(m["delta"], -1)

    def test_plan_does_not_write_anything(self):
        antes = _snapshot(self.db)
        plan(self.db)
        self.assertEqual(_snapshot(self.db), antes)

    def test_rank_outside_the_ladder_is_flagged_not_crashed(self):
        db = _make_db([("corrompido", "ZZZ", 700, "E", 200)])
        self.addCleanup(lambda: os.path.exists(db) and os.remove(db))
        r = plan(db)
        self.assertEqual(len(r["sem_escada"]), 1)
        self.assertIsNone(r["sem_escada"][0]["delta"])
        self.assertEqual(r["sem_escada"][0]["rank_novo"], "E")

    def test_missing_database_is_reported(self):
        with self.assertRaises(FileNotFoundError):
            plan(os.path.join(tempfile.gettempdir(), "p3luche-nao-existe.db"))


class AplicacaoTests(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.addCleanup(lambda: os.path.exists(self.db) and os.remove(self.db))

    def test_apply_writes_exactly_what_the_plan_promised(self):
        plano = plan(self.db)["mudancas"]
        previsto = {m["user_id"]: (m["rank_novo"], m["xp_novo"]) for m in plano}
        esperava_gravar = sum(
            1 for m in plano
            if (m["rank_novo"], m["xp_novo"]) != (m["rank_antigo"], m["xp_antigo"])
        )

        report = apply(self.db, backup=False)

        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            for row in conn.execute("SELECT user_id, guild_rank, guild_xp FROM users"):
                with self.subTest(user_id=row["user_id"]):
                    self.assertEqual(
                        (row["guild_rank"], row["guild_xp"]), previsto[row["user_id"]]
                    )
        finally:
            conn.close()
        self.assertEqual(report["gravados"], esperava_gravar)
        self.assertLess(report["gravados"], report["total"], "perfis inalterados também foram gravados")

    def test_second_run_is_refused(self):
        """A migração não é idempotente por natureza: depois de aplicada, um
        "E correto" é indistinguível de um "E bugado" olhando só a linha do
        jogador, e um segundo passe rebaixaria todo mundo mais um degrau.
        A trava tem que barrar isso antes de qualquer escrita."""
        apply(self.db, backup=False)
        depois_do_primeiro = _snapshot(self.db)

        with self.assertRaises(MigrationAlreadyApplied):
            apply(self.db, backup=False)

        self.assertEqual(_snapshot(self.db), depois_do_primeiro)

    def test_second_run_demotes_again_when_forced(self):
        """Documenta POR QUE a trava existe: com --force o segundo passe
        realmente rebaixa de novo. É o comportamento que a trava protege."""
        apply(self.db, backup=False)
        depois_do_primeiro = _snapshot(self.db)

        apply(self.db, backup=False, force=True)

        self.assertNotEqual(_snapshot(self.db), depois_do_primeiro)

    def test_refusal_does_not_leave_a_stray_backup(self):
        apply(self.db, backup=False)
        antes = set(os.listdir(os.path.dirname(self.db)))

        with self.assertRaises(MigrationAlreadyApplied):
            apply(self.db, backup=True)

        self.assertEqual(set(os.listdir(os.path.dirname(self.db))), antes)

    def test_apply_creates_a_backup_by_default(self):
        report = apply(self.db)
        self.addCleanup(lambda: os.path.exists(report["backup"]) and os.remove(report["backup"]))
        self.assertTrue(os.path.exists(report["backup"]))
        self.assertIn("pre-rankfix", report["backup"])

    def test_backup_still_holds_the_pre_migration_state(self):
        antes = _snapshot(self.db)
        report = apply(self.db)
        self.addCleanup(lambda: os.path.exists(report["backup"]) and os.remove(report["backup"]))
        self.assertNotEqual(_snapshot(self.db), antes)
        self.assertEqual(_snapshot(report["backup"]), antes)


def _snapshot(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return sorted(conn.execute("SELECT user_id, guild_rank, guild_xp FROM users").fetchall())
    finally:
        conn.close()


if __name__ == "__main__":
    unittest.main()
