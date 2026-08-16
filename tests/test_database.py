import contextlib
import io
import sqlite3
import unittest

from database import DatabaseManager


def _migrate_silently(manager: DatabaseManager) -> None:
    """DatabaseManager.migrate() faz `print()` com emoji; em consoles Windows
    com codepage cp1252 (não UTF-8) isso levanta UnicodeEncodeError — um
    problema pré-existente, fora do escopo desta correção. Redireciona stdout
    para um StringIO (que não faz encode) só para o teste não depender do
    codepage do terminal que o executa.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        manager.migrate()


class WarningsSchemaMigrationTests(unittest.TestCase):
    """Regressão: /perdoar (moderacao.py) grava em warnings.revoked_by e
    warnings.revoked_at — essas colunas precisam existir após a migração,
    senão o UPDATE falha com 'no such column' em qualquer banco criado do
    zero apenas por DatabaseManager.migrate().
    """

    def test_migrate_creates_revoked_columns_on_warnings(self):
        manager = DatabaseManager(":memory:")
        manager.connect()
        _migrate_silently(manager)

        columns = {row[1] for row in manager.conn.execute("PRAGMA table_info(warnings)").fetchall()}

        self.assertIn("revoked_by", columns)
        self.assertIn("revoked_at", columns)

    def test_perdoar_update_statement_succeeds_against_fresh_schema(self):
        manager = DatabaseManager(":memory:")
        manager.connect()
        _migrate_silently(manager)
        conn = manager.conn

        conn.execute(
            "INSERT INTO warnings (user_id, user_name, moderator_id, moderator_name, reason, proof) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "Alvo", 2, "Mod", "teste", "sem prova"),
        )
        conn.commit()
        warning_id = conn.execute("SELECT id FROM warnings").fetchone()[0]

        # Mesma instrução usada por moderacao.py:remover_warn.
        conn.execute(
            "UPDATE warnings SET status = 'revoked', revoked_by = ?, revoked_at = ? WHERE id = ?",
            ("ModTeste", "2026-08-13 12:00:00", warning_id),
        )
        conn.commit()

        row = conn.execute(
            "SELECT status, revoked_by, revoked_at FROM warnings WHERE id = ?", (warning_id,)
        ).fetchone()
        self.assertEqual(row[0], "revoked")
        self.assertEqual(row[1], "ModTeste")
        self.assertEqual(row[2], "2026-08-13 12:00:00")

    def test_migrate_is_idempotent_when_columns_already_exist(self):
        manager = DatabaseManager(":memory:")
        manager.connect()
        _migrate_silently(manager)
        # Rodar de novo não deve levantar (mesmo padrão de _add_column_safe
        # já usado para todas as outras colunas do schema).
        _migrate_silently(manager)


if __name__ == "__main__":
    unittest.main()
