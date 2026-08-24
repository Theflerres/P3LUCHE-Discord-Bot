"""
Concessão em massa, única, do cargo "Pescador" a quem já tinha conta.

Contexto
--------
`pescador_role.ensure_pescador_role` concede o cargo sob demanda, na pescaria.
Isso resolve os jogadores novos, mas deixaria os antigos esperando: quem pesca
uma vez por semana levaria semanas para receber. Este script faz o passe único
sobre a base existente.

Por que ele precisa de conexão com o Discord
--------------------------------------------
Diferente de `migration_rank_fix.py`, que é SQL puro, aqui a informação
"este jogador já tem o cargo?" só existe no Discord. Então até o dry-run entra
no gateway — em modo leitura, sem conceder nada. O login usa o intent de
membros e faz `guild.chunk()` para carregar a lista completa: depois disso,
`get_member` devolvendo None significa de fato que a pessoa não está mais no
servidor, e não apenas que o cache não tinha a linha.

A regra da flag aqui é diferente da do fluxo de pesca
----------------------------------------------------
No fluxo de pesca, falha NÃO marca a flag (ver o docstring de
`pescador_role`): erro de permissão é configuração quebrada e deve voltar a
ser tentado. Aqui a flag é marcada para TODOS, inclusive quem falhou, porque
o caso esperado de falha é membro que saiu do servidor — condição que não se
resolve sozinha e que não vale reprocessar em cada execução. Se essa pessoa
voltar, ela volta sem o cargo e sem verificação pendente; é um caso conhecido
e aceito, não um descuido.

Justamente porque este script marca falha como verificada, ele se recusa a
rodar com o servidor mal configurado (ver `preflight`): sem essa trava, uma
execução sem permissão de "Gerenciar Cargos" queimaria a flag da base inteira
e ninguém receberia o cargo nunca mais, nem pela pescaria.

Idempotente de propósito
------------------------
Não usa a trava de `applied_migrations` que o recálculo de rank precisou. Aqui
reexecutar é inofensivo (conceder cargo que a pessoa já tem é no-op, flag já
marcada é no-op) e às vezes útil: rodar de novo depois de um período pega quem
criou conta no meio.

Uso
---
    python src/p3luche/migration_pescador_role.py            # dry-run
    python src/p3luche/migration_pescador_role.py --apply    # concede
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3

import discord

from config import DB_PATH, PESCADOR_ROLE_ID, TOKEN
from economy_db import ensure_v4_tables
from pescador_role import GRANT_REASON

# Desfechos da classificação (dry-run).
ALREADY_HAD = "already_had"
WILL_RECEIVE = "will_receive"
LEFT_SERVER = "left_server"


class PreflightFailed(RuntimeError):
    """O servidor não está em condições de receber a concessão em massa."""


class GuildNotFound(RuntimeError):
    """Nenhum servidor visível ao bot contém o cargo configurado."""


def load_players(db_path: str = None) -> list:
    """Todos os jogadores de `users`, com a flag atual.

    Abre em modo read-only: o plano nunca grava, e um dry-run que não pode
    escrever é melhor garantia do que um que só promete não escrever.

    A coluna `pescador_role_checked` pode ainda não existir: ela entra por
    ALTER TABLE em `ensure_v4_tables`, que só roda quando o bot sobe. Um
    dry-run rodado antes desse restart não pode simplesmente estourar com
    "no such column" — nem pode criar a coluna, porque a conexão é somente
    leitura e um dry-run que altera schema não é dry. Então a ausência é
    tratada como "ninguém verificado", que é exatamente o que ela significa.
    O `--apply` cria a coluna antes de gravar (ver apply_plan).
    """
    path = db_path or DB_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(f"Banco nao encontrado: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        colunas = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        tem_flag = "pescador_role_checked" in colunas
        coluna = "pescador_role_checked" if tem_flag else "0 AS pescador_role_checked"
        rows = conn.execute(
            f"SELECT user_id, user_name, fish_count, {coluna} FROM users"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "user_id": r["user_id"],
            "user_name": r["user_name"] or "(sem nome)",
            "fish_count": r["fish_count"] or 0,
            "already_checked": bool(r["pescador_role_checked"]),
            "coluna_ausente": not tem_flag,
        }
        for r in rows
    ]


def preflight(guild, role_id: int = PESCADOR_ROLE_ID) -> dict:
    """Confere se o servidor pode receber a concessão. Não grava nada.

    Roda ANTES de qualquer escrita porque este script marca a flag mesmo em
    caso de falha: aplicar com permissão errada gastaria a única verificação
    de cada jogador sem conceder nada a ninguém.
    """
    problemas = []
    role = guild.get_role(role_id)
    if role is None:
        problemas.append(
            f"o cargo {role_id} nao existe em '{getattr(guild, 'name', '?')}' "
            "— confira PESCADOR_ROLE_ID em config.py"
        )
        return {"ok": False, "role": None, "problemas": problemas}

    eu = getattr(guild, "me", None)
    if eu is None:
        problemas.append("nao consegui identificar o membro do proprio bot no servidor")
    else:
        if not getattr(eu.guild_permissions, "manage_roles", False):
            problemas.append("o bot nao tem a permissao 'Gerenciar Cargos'")
        topo = getattr(eu, "top_role", None)
        if topo is not None and getattr(role, "position", 0) >= getattr(topo, "position", 0):
            problemas.append(
                f"o cargo '{role.name}' esta na posicao {role.position}, "
                f"acima ou igual ao cargo do bot ('{topo.name}', posicao {topo.position}) "
                "— o Discord recusa conceder cargo nessa condicao"
            )
    return {"ok": not problemas, "role": role, "problemas": problemas}


def plan_from_guild(players: list, guild, role_id: int = PESCADOR_ROLE_ID) -> dict:
    """Classifica cada jogador sem conceder nada.

    Recebe o servidor por parâmetro (em vez de buscá-lo aqui) para que a
    classificação seja exercitável sem rede — é ela que carrega a lógica.
    """
    role = guild.get_role(role_id)
    if role is None:
        raise PreflightFailed(
            f"o cargo {role_id} nao existe em '{getattr(guild, 'name', '?')}'"
        )

    buckets = {ALREADY_HAD: [], WILL_RECEIVE: [], LEFT_SERVER: []}
    for p in players:
        membro = guild.get_member(p["user_id"])
        if membro is None:
            # Depois de guild.chunk() isto significa "saiu do servidor".
            destino = LEFT_SERVER
        elif any(getattr(r, "id", None) == role_id for r in getattr(membro, "roles", ())):
            destino = ALREADY_HAD
        else:
            destino = WILL_RECEIVE
        buckets[destino].append({**p, "resultado": destino})

    return {
        "guild_name": getattr(guild, "name", "?"),
        "role_name": getattr(role, "name", "?"),
        "role_id": role_id,
        "total": len(players),
        "ja_checados": [p for p in players if p["already_checked"]],
        "coluna_ausente": any(p.get("coluna_ausente") for p in players),
        **buckets,
    }


async def apply_plan(conn: sqlite3.Connection, guild, plano: dict, role_id: int = PESCADOR_ROLE_ID) -> dict:
    """Concede os cargos do plano e marca a flag de TODOS os jogadores.

    Recusa rodar se o preflight falhar — ver o docstring do módulo.
    """
    check = preflight(guild, role_id)
    if not check["ok"]:
        raise PreflightFailed("; ".join(check["problemas"]))
    role = check["role"]

    # Garante o schema antes de gravar: se este script rodar antes do primeiro
    # restart do bot com a feature, a coluna ainda não existe. ensure_v4_tables
    # é idempotente (ALTER TABLE tolerando "duplicate column name"), então
    # chamar aqui é seguro mesmo com o schema já em dia.
    ensure_v4_tables(conn)

    concedidos, falhas = [], []
    for p in plano[WILL_RECEIVE]:
        membro = guild.get_member(p["user_id"])
        if membro is None:
            falhas.append({**p, "erro": "saiu do servidor entre o plano e a aplicacao"})
            continue
        try:
            await membro.add_roles(role, reason=GRANT_REASON)
            concedidos.append(p)
        except discord.Forbidden as e:
            falhas.append({**p, "erro": f"permissao/hierarquia: {e}"})
        except discord.HTTPException as e:
            falhas.append({**p, "erro": f"HTTP: {e}"})

    # A flag é marcada para todos, inclusive falhas e quem saiu do servidor.
    # Numa transação só: um passe pela metade deixaria parte da base sendo
    # reverificada na pescaria sem necessidade.
    todos = [p["user_id"] for p in plano[ALREADY_HAD] + plano[WILL_RECEIVE] + plano[LEFT_SERVER]]
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(
            "UPDATE users SET pescador_role_checked = 1 WHERE user_id = ?",
            [(uid,) for uid in todos],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        **plano,
        "concedidos": concedidos,
        "falhas": falhas,
        "marcados": len(todos),
    }


def print_report(plano: dict, aplicado: dict = None, amostra: int = 8) -> None:
    modo = "APLICADO" if aplicado else "DRY-RUN"
    print(f"=== Cargo Pescador em massa ({modo}) ===")
    print(f"  servidor: {plano['guild_name']}")
    print(f"  cargo:    '{plano['role_name']}' (ID {plano['role_id']})")
    print()
    print(f"  Total de jogadores em users:   {plano['total']}")
    print(f"  Ja tinham o cargo:             {len(plano[ALREADY_HAD])}")
    print(f"  {'Receberam agora:':<30} {len(aplicado['concedidos'])}" if aplicado
          else f"  {'Vao receber agora:':<30} {len(plano[WILL_RECEIVE])}")
    print(f"  Fora do servidor:              {len(plano[LEFT_SERVER])}")
    if aplicado:
        print(f"  Falharam:                      {len(aplicado['falhas'])}")
        print(f"  Flags marcadas:                {aplicado['marcados']}")
    print(f"  (ja verificados antes deste passe: {len(plano['ja_checados'])})")
    if plano.get("coluna_ausente"):
        print("  NOTA: a coluna pescador_role_checked ainda nao existe neste banco")
        print("        (entra no proximo start do bot, ou no --apply deste script).")
    print()

    def bloco(titulo, items, extra=None):
        if not items:
            return
        print(f"  --- {titulo} ({len(items)}) ---")
        for p in items[:amostra]:
            linha = f"    {p['user_name'][:28]:<29}{p['fish_count']:>8} peixes"
            if extra:
                linha += f"  {p.get(extra, '')}"
            print(linha)
        if len(items) > amostra:
            print(f"    ... e mais {len(items) - amostra}")
        print()

    bloco("VAO RECEBER" if not aplicado else "RECEBERAM",
          aplicado["concedidos"] if aplicado else plano[WILL_RECEIVE])
    bloco("FORA DO SERVIDOR (flag marcada mesmo assim)", plano[LEFT_SERVER])
    bloco("JA TINHAM", plano[ALREADY_HAD])
    if aplicado and aplicado["falhas"]:
        bloco("FALHAS (flag marcada mesmo assim)", aplicado["falhas"], extra="erro")

    if not aplicado:
        print("  Nada foi gravado e nenhum cargo foi concedido. Use --apply para aplicar.")


async def _run(db_path: str, aplicar: bool, amostra: int, como_json: bool) -> int:
    players = load_players(db_path)

    intents = discord.Intents.default()
    intents.members = True   # sem isso a lista de membros nao carrega
    intents.guilds = True
    client = discord.Client(intents=intents)

    resultado = {"codigo": 0}

    @client.event
    async def on_ready():
        try:
            guild = next(
                (g for g in client.guilds if g.get_role(PESCADOR_ROLE_ID) is not None), None
            )
            if guild is None:
                # O parenteses no join importa: `a + b or c` avalia como
                # `(a + b) or c`, e "prefixo: " ja e verdadeiro, entao o
                # fallback nunca apareceria com a lista vazia.
                vistos = ", ".join(f"'{g.name}'" for g in client.guilds) or "(nenhum)"
                raise GuildNotFound(
                    f"nenhum servidor visivel ao bot tem o cargo {PESCADOR_ROLE_ID}. "
                    f"Servidores vistos: {vistos}"
                )
            # Carrega a lista completa de membros: sem isto, get_member devolve
            # None para quem simplesmente nao esta no cache e o relatorio
            # contaria membro presente como "fora do servidor".
            await guild.chunk()

            plano = plan_from_guild(players, guild)

            if como_json:
                print(json.dumps(
                    {k: v for k, v in plano.items() if k != "ja_checados"},
                    indent=2, ensure_ascii=False, default=str,
                ))
            elif not aplicar:
                check = preflight(guild)
                print_report(plano, amostra=amostra)
                if not check["ok"]:
                    print()
                    print("  ATENCAO — o --apply vai ser RECUSADO enquanto isto nao for corrigido:")
                    for prob in check["problemas"]:
                        print(f"    - {prob}")
                    resultado["codigo"] = 2
            else:
                conn = sqlite3.connect(db_path or DB_PATH)
                conn.row_factory = sqlite3.Row
                try:
                    aplicado = await apply_plan(conn, guild, plano)
                finally:
                    conn.close()
                print_report(plano, aplicado=aplicado, amostra=amostra)
        except (PreflightFailed, GuildNotFound) as e:
            print(f"=== Recusado ===\n  {e}")
            resultado["codigo"] = 1
        finally:
            await client.close()

    try:
        await client.start(TOKEN)
    except discord.PrivilegedIntentsRequired:
        # Erro claro em vez de stack trace: este script depende do intent de
        # membros para saber quem tem o cargo, e o intent e uma caixa no portal
        # do desenvolvedor, nao algo que o codigo possa ligar sozinho.
        print(
            "=== Recusado ===\n"
            "  O bot precisa do SERVER MEMBERS INTENT habilitado para este script.\n"
            "  Discord Developer Portal > sua aplicacao > Bot > Privileged Gateway\n"
            "  Intents > 'Server Members Intent'. Sem ele nao ha como listar quem\n"
            "  ja tem o cargo, e o relatorio contaria todo mundo como fora do servidor."
        )
        return 1
    except discord.LoginFailure as e:
        print(f"=== Recusado ===\n  Login no Discord falhou: {e}")
        return 1
    return resultado["codigo"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="concede os cargos (sem isso, so relatorio)")
    parser.add_argument("--db", default=None, help="caminho do banco (default: config.DB_PATH)")
    parser.add_argument("--amostra", type=int, default=8, help="linhas por bloco no relatorio")
    parser.add_argument("--json", action="store_true", help="despeja o plano em JSON")
    args = parser.parse_args()

    codigo = asyncio.run(_run(args.db, args.apply, args.amostra, args.json))
    if codigo:
        raise SystemExit(codigo)


if __name__ == "__main__":
    main()
