"""
Recálculo único de rank de guilda após o fix do limiar de promoção.

Contexto
--------
`/eco pescar` e o diálogo da Capitã comparavam o XP com o `req_xp` do rank
ATUAL em vez do rank de DESTINO, o que deslocou a escada inteira um degrau
para baixo (rank F tem `req_xp` 0, então a promoção F->E saía no primeiro
lance). Corrigida a fórmula, os ranks já gravados continuam refletindo a
escada antiga e precisam de um recálculo — este script, rodado UMA vez.
Não é algo para chamar de ensure_user: promoção é evento, não invariante.

O ponto delicado: `users.guild_xp` NÃO é acumulado
--------------------------------------------------
Toda promoção subtrai o custo do XP (`new_xp_total -= custo`), então o valor
gravado é o RESÍDUO dentro do rank atual, não o total que o jogador ganhou na
vida. Recalcular o rank lendo `guild_xp` como se fosse acumulado apagaria toda
a progressão anterior à última promoção — um jogador de rank B com 386 de
resíduo viraria F.

Este script reconstrói o acumulado antes de reclassificar:

    acumulado = subtraido_no_caminho_bugado(rank_atual) + guild_xp

`subtraido_no_caminho_bugado` é a soma dos custos que a fórmula ERRADA cobrou
para levar o jogador até o rank em que ele está — determinística, porque a
fórmula antiga sempre cobrava o `req_xp` do rank de origem. Com o acumulado em
mãos, o rank novo sai das fronteiras corretas e o `guild_xp` é reescrito como o
resíduo dentro dele, preservando o invariante do resto do código.

Limite conhecido: a reconstrução assume que o jogador chegou ao rank atual pela
escada normal. Rank definido à mão (comando de admin, importação legada) não
tem caminho para reconstruir e é reclassificado a partir do que estiver
gravado — os casos assim aparecem marcados no relatório.

Uso
---
    python src/p3luche/migration_rank_fix.py            # dry-run, não grava
    python src/p3luche/migration_rank_fix.py --apply    # grava
"""
import argparse
import json
import os
import shutil
import sqlite3
from datetime import datetime

from config import DB_PATH
from cogs.economia import GUILD_RANKS

# Ordem da escada. 'S' fica de fora de propósito: é conteúdo bloqueado
# (`req_xp` 999999) e ninguém deveria alcançá-lo pela progressão normal.
LADDER = ["F", "E", "D", "C", "B", "A"]

MIGRATION_NAME = "rank_fix_limiar_promocao"

# Esta migração NÃO é idempotente por natureza, e a trava abaixo existe por
# causa disso. A reconstrução do acumulado parte do princípio de que o rank
# gravado veio da escada bugada; depois de aplicada, os ranks são os corretos
# e um segundo passe leria um "E correto" como se fosse um "E bugado",
# rebaixando de novo (E/0 -> acumulado 0 -> F). Não dá para distinguir os dois
# estados olhando só a linha do jogador, então a defesa é registrar que a
# migração rodou e recusar a segunda execução.
MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS applied_migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class MigrationAlreadyApplied(RuntimeError):
    """Levantada quando o recálculo já rodou neste banco."""


def already_applied(conn: sqlite3.Connection) -> bool:
    conn.executescript(MIGRATIONS_TABLE_SQL)
    row = conn.execute(
        "SELECT applied_at FROM applied_migrations WHERE name = ?", (MIGRATION_NAME,)
    ).fetchone()
    return row is not None


def correct_boundaries() -> dict:
    """XP ACUMULADO necessário para estar em cada rank, pela regra correta.

    Cada degrau custa o `req_xp` do rank de destino, então a fronteira de um
    rank é a soma dos degraus até ele: E=500, D=2000, C=6000, B=16000,
    A=41000.
    """
    total = 0
    bounds = {"F": 0}
    for anterior, atual in zip(LADDER, LADDER[1:]):
        total += GUILD_RANKS[atual]["req_xp"]
        bounds[atual] = total
    return bounds


def buggy_spent() -> dict:
    """XP que a fórmula ERRADA cobrou para chegar em cada rank.

    Ela subtraía o `req_xp` do rank de ORIGEM, então o caminho até D custou
    `req_xp[F] + req_xp[E]` = 0 + 500, e assim por diante.
    """
    total = 0
    spent = {"F": 0}
    for anterior, atual in zip(LADDER, LADDER[1:]):
        total += GUILD_RANKS[anterior]["req_xp"]
        spent[atual] = total
    return spent


def rank_for(acumulado: int, bounds: dict) -> str:
    """Rank mais alto cuja fronteira o acumulado alcança."""
    atual = "F"
    for rank in LADDER:
        if acumulado >= bounds[rank]:
            atual = rank
    return atual


def plan(db_path: str = None) -> dict:
    """Calcula o recálculo de todo mundo SEM gravar nada."""
    path = db_path or DB_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(f"Banco nao encontrado: {path}")

    bounds = correct_boundaries()
    spent = buggy_spent()

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT user_id, user_name, guild_rank, guild_xp, fish_count FROM users"
        ).fetchall()
    finally:
        conn.close()

    mudancas = []
    for row in rows:
        rank_atual = row["guild_rank"] or "F"
        xp_residual = int(row["guild_xp"] or 0)
        fora_da_escada = rank_atual not in LADDER

        # Rank fora da escada não tem caminho reconstruível; trata o que está
        # gravado como acumulado e deixa marcado no relatório.
        base = spent.get(rank_atual, 0)
        acumulado = base + xp_residual
        rank_novo = rank_for(acumulado, bounds)
        xp_novo = acumulado - bounds[rank_novo]

        if rank_atual in LADDER:
            delta = LADDER.index(rank_novo) - LADDER.index(rank_atual)
        else:
            delta = None

        mudancas.append(
            {
                "user_id": row["user_id"],
                "user_name": row["user_name"] or "(sem nome)",
                "fish_count": row["fish_count"] or 0,
                "rank_antigo": rank_atual,
                "xp_antigo": xp_residual,
                "acumulado": acumulado,
                "rank_novo": rank_novo,
                "xp_novo": xp_novo,
                "delta": delta,
                "fora_da_escada": fora_da_escada,
            }
        )

    mudancas.sort(key=lambda m: -m["acumulado"])
    return {
        "db_path": path,
        "total": len(mudancas),
        "mantem": [m for m in mudancas if m["delta"] == 0],
        "sobem": [m for m in mudancas if m["delta"] is not None and m["delta"] > 0],
        "caem": [m for m in mudancas if m["delta"] is not None and m["delta"] < 0],
        "sem_escada": [m for m in mudancas if m["delta"] is None],
        "mudancas": mudancas,
        "fronteiras": bounds,
        "cobrado_pela_formula_antiga": spent,
    }


def print_report(report: dict, amostra: int = 5) -> None:
    b = report["fronteiras"]
    print("=== Recalculo de rank de guilda (DRY-RUN) ===")
    print(f"  banco: {report['db_path']}")
    print()
    print("  Fronteiras corretas (XP acumulado para estar no rank):")
    print("   ", "  ".join(f"{r}={b[r]}" for r in LADDER))
    print("  Cobrado pela formula antiga ate cada rank:")
    s = report["cobrado_pela_formula_antiga"]
    print("   ", "  ".join(f"{r}={s[r]}" for r in LADDER))
    print()
    print(f"  Total de jogadores:      {report['total']}")
    print(f"  Mantem o rank:           {len(report['mantem'])}")
    print(f"  Sobem de rank:           {len(report['sobem'])}")
    print(f"  Caem de rank:            {len(report['caem'])}")
    if report["sem_escada"]:
        print(f"  Rank fora da escada:     {len(report['sem_escada'])}  <-- revisar a mao")
    print()

    def bloco(titulo, items):
        if not items:
            return
        print(f"  --- {titulo} ({len(items)}) ---")
        print(f"    {'jogador':<24}{'peixes':>8}{'de':>6}{'para':>6}{'xp antes':>10}{'acum.':>9}{'xp depois':>11}")
        for m in items[:amostra]:
            print(
                f"    {m['user_name'][:23]:<24}{m['fish_count']:>8}"
                f"{m['rank_antigo']:>6}{m['rank_novo']:>6}"
                f"{m['xp_antigo']:>10}{m['acumulado']:>9}{m['xp_novo']:>11}"
            )
        if len(items) > amostra:
            print(f"    ... e mais {len(items) - amostra}")
        print()

    bloco("CAEM", report["caem"])
    bloco("SOBEM", report["sobem"])
    bloco("MANTEM", report["mantem"])
    bloco("FORA DA ESCADA", report["sem_escada"])
    print("  Nada foi gravado. Use --apply para aplicar.")


def apply(db_path: str = None, backup: bool = True, force: bool = False) -> dict:
    """Aplica o recálculo. Faz backup do arquivo do banco antes.

    Recusa a segunda execução (ver MIGRATIONS_TABLE_SQL): reaplicar rebaixaria
    todo mundo mais um degrau. `force=True` existe só para retomar uma execução
    que morreu no meio, e depois de conferir o estado do banco na mão.
    """
    report = plan(db_path)
    path = report["db_path"]

    # sync_user_to_economy mantém a cópia legada `economy` coerente com users;
    # sem ela o primeiro comando do jogador reescreveria economy a partir de
    # users e a divergência apareceria em qualquer leitura da legada.
    from economy_db import sync_user_to_economy

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        if already_applied(conn) and not force:
            raise MigrationAlreadyApplied(
                f"'{MIGRATION_NAME}' ja foi aplicada neste banco ({path}). "
                "Reaplicar rebaixaria os jogadores outra vez. Use force=True "
                "apenas para retomar uma execucao interrompida."
            )
    except Exception:
        conn.close()
        raise

    backup_path = None
    if backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = f"{path}.pre-rankfix-{stamp}.bak"
        shutil.copy2(path, backup_path)

    gravados = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for m in report["mudancas"]:
            if m["rank_novo"] == m["rank_antigo"] and m["xp_novo"] == m["xp_antigo"]:
                continue
            conn.execute(
                "UPDATE users SET guild_rank = ?, guild_xp = ? WHERE user_id = ?",
                (m["rank_novo"], m["xp_novo"], m["user_id"]),
            )
            gravados += 1
        conn.execute(
            "INSERT OR REPLACE INTO applied_migrations (name, applied_at) VALUES (?, ?)",
            (MIGRATION_NAME, datetime.now().isoformat(" ")),
        )
        conn.commit()
        for m in report["mudancas"]:
            sync_user_to_economy(conn, m["user_id"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    report["gravados"] = gravados
    report["backup"] = backup_path
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="grava (sem isso, so relatorio)")
    parser.add_argument("--db", default=None, help="caminho do banco (default: config.DB_PATH)")
    parser.add_argument("--amostra", type=int, default=5, help="linhas por bloco no relatorio")
    parser.add_argument("--json", action="store_true", help="despeja o plano em JSON")
    parser.add_argument(
        "--force",
        action="store_true",
        help="reaplica mesmo ja tendo rodado (so para retomar execucao interrompida)",
    )
    args = parser.parse_args()

    if args.json:
        print(json.dumps(plan(args.db)["mudancas"], indent=2, ensure_ascii=False))
        return

    if not args.apply:
        print_report(plan(args.db), amostra=args.amostra)
        return

    try:
        report = apply(args.db, force=args.force)
    except MigrationAlreadyApplied as e:
        print(f"=== Recusado ===\n  {e}")
        raise SystemExit(1)
    print("=== Recalculo de rank APLICADO ===")
    print(f"  banco:    {report['db_path']}")
    print(f"  backup:   {report['backup']}")
    print(f"  gravados: {report['gravados']} de {report['total']}")


if __name__ == "__main__":
    main()
