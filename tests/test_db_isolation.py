"""Garante que a suíte nunca resolva o banco para database/bot.db de produção.

Sem isto, o isolamento de tests/__init__.py passa a valer por acidente: hoje
nenhum teste exercita db_manager ou migration_v4, então a suíte não escreveria
no banco real de qualquer forma. Estes testes falham no dia em que alguém
reintroduzir um binding que resolva para produção.
"""
import os
import sqlite3
import unittest


PROD_DB = os.path.join(os.getcwd(), "database", "bot.db")


def _mesmo_arquivo(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class DbIsolationTests(unittest.TestCase):
    def test_banco_resolvido_fica_fora_de_producao(self):
        # Dois mecanismos podem isolar: P3LUCHE_DB_PATH (definido por
        # tests/__init__.py, usado quando o top-level dir é a raiz) ou a trava
        # de _rodando_sob_test_runner() no config. Só importa o resultado.
        import config

        self.assertFalse(_mesmo_arquivo(config.DB_PATH, PROD_DB))

    def test_config_detecta_o_test_runner(self):
        import src.p3luche.config as config_real

        self.assertTrue(config_real._rodando_sob_test_runner())

    def test_os_dois_modulos_de_config_apontam_para_fora_de_producao(self):
        # config (shim da raiz) e src.p3luche.config são objetos distintos, cada
        # um com sua cópia de DB_PATH; ambos precisam estar repontados.
        import config
        import src.p3luche.config as config_real

        for mod in (config, config_real):
            self.assertFalse(
                _mesmo_arquivo(mod.DB_PATH, PROD_DB),
                f"{mod.__name__}.DB_PATH aponta para o banco de produção",
            )
        self.assertTrue(_mesmo_arquivo(config.DB_PATH, config_real.DB_PATH))

    def test_bindings_derivados_nao_apontam_para_producao(self):
        import migration_v4
        from database import db_manager

        self.assertFalse(_mesmo_arquivo(db_manager.db_path, PROD_DB))
        self.assertFalse(_mesmo_arquivo(migration_v4.DB_PATH, PROD_DB))

    def test_importar_audit_db_nao_abre_conexao(self):
        # audit_db abria sqlite3.connect(DB_PATH) no nível de módulo.
        import audit_db

        conexoes = [v for v in vars(audit_db).values() if isinstance(v, sqlite3.Connection)]
        self.assertEqual(conexoes, [], "audit_db abriu conexão no import")
        self.assertTrue(callable(audit_db.main))


if __name__ == "__main__":
    unittest.main()
