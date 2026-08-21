"""Isolamento de banco para quem rodar a suíte com pytest.

O runner padrão do projeto é unittest, que nunca carrega conftest.py — por isso
a lógica real vive em tests/__init__.py, importado pelo discovery do unittest.
Este arquivo só garante que o pytest passe pelo mesmo bootstrap.
"""
import tests  # noqa: F401  (o import é o efeito desejado)
