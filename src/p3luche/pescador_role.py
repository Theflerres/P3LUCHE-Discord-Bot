"""
Cargo automático "Pescador" — concessão sob demanda, uma verificação por jogador.

Regra de custo que molda todo este módulo
-----------------------------------------
A verificação roda no fluxo de `/eco pescar`, que é o comando mais chamado do
bot. Checar cargo em todo lance seria uma chamada de API do Discord por
pescaria, para sempre, para responder uma pergunta cuja resposta praticamente
nunca muda. Por isso existe a flag `users.pescador_role_checked`: quando ela
já está marcada, este módulo retorna sem tocar em nada do Discord.

Mesmo na primeira verificação, o caminho normal não gasta chamada de API:
`guild.get_role` e `member.roles` leem o cache do gateway. A única chamada de
rede é o `add_roles`, e só para quem realmente não tem o cargo — uma vez na
vida de cada jogador.

Quando a flag NÃO é marcada
---------------------------
Só os dois desfechos definitivos marcam: o jogador já tinha o cargo, ou acabou
de receber. Falha de permissão/hierarquia e cargo inexistente NÃO marcam de
propósito — as duas são configuração errada do servidor, e queimar a flag
nesses casos negaria o cargo para sempre a todo mundo que pescou durante a
janela de configuração quebrada. Sem a marca, o conserto do servidor é o
suficiente: a próxima pescaria de cada um volta a tentar.

A migração em massa (`migration_pescador_role.py`) segue uma regra diferente,
e deliberadamente: lá a flag é marcada até para quem falhou, porque o caso
esperado de falha é membro que saiu do servidor — condição que não se resolve
com o tempo e que não vale reprocessar em toda execução do script.
"""
from __future__ import annotations

import discord

from config import PESCADOR_ROLE_ID
from economy_db import mark_pescador_role_checked, pescador_role_checked
from utils import log_to_gui

#: Motivo registrado no audit log do Discord em cada concessão.
GRANT_REASON = "Conta criada na economia do P3LUCHE (cargo automático)"

# Desfechos possíveis. Retornados como string em vez de bool para que o
# chamador (e os testes) distingam "pulei porque já verifiquei" de "verifiquei
# e o jogador já tinha" — os dois não fazem nada, por motivos diferentes.
SKIPPED = "skipped"            # flag já marcada: nenhuma consulta ao Discord
ALREADY_HAD = "already_had"    # verificado agora, já possuía o cargo
GRANTED = "granted"            # cargo concedido agora
NOT_A_MEMBER = "not_a_member"  # sem contexto de servidor (DM, por exemplo)
ROLE_MISSING = "role_missing"  # o ID do cargo não existe neste servidor
FORBIDDEN = "forbidden"        # sem permissão ou cargo acima do bot
ERROR = "error"                # qualquer outra falha do lado do Discord


def _member_has_role(member, role_id: int) -> bool:
    return any(getattr(r, "id", None) == role_id for r in getattr(member, "roles", ()))


async def ensure_pescador_role(conn, member, role_id: int = PESCADOR_ROLE_ID) -> str:
    """Garante o cargo do jogador. NUNCA levanta exceção.

    O contrato de não levantar é o ponto central: esta função é chamada de
    dentro de `/eco pescar`, e nenhuma falha de cargo pode impedir alguém de
    pescar. Todo caminho de erro sai por log + string de desfecho.

    Devolve um dos desfechos declarados no topo do módulo.
    """
    try:
        user_id = getattr(member, "id", None)
        if user_id is None:
            return NOT_A_MEMBER

        # Portão de custo: antes de qualquer coisa do Discord.
        if pescador_role_checked(conn, user_id):
            return SKIPPED

        guild = getattr(member, "guild", None)
        if guild is None:
            # Sem servidor não há cargo a conceder, e a flag NÃO é marcada:
            # o mesmo jogador pescando no servidor depois deve ser verificado.
            return NOT_A_MEMBER

        role = guild.get_role(role_id)
        if role is None:
            # Não marca a flag: se o ID mudou, consertar a constante tem que
            # voltar a funcionar para todo mundo sem migração nenhuma.
            log_to_gui(
                f"Cargo Pescador (ID {role_id}) nao existe no servidor "
                f"'{getattr(guild, 'name', '?')}' — verifique PESCADOR_ROLE_ID em config.py.",
                "ERROR",
            )
            return ROLE_MISSING

        if _member_has_role(member, role_id):
            mark_pescador_role_checked(conn, user_id)
            return ALREADY_HAD

        await member.add_roles(role, reason=GRANT_REASON)
        mark_pescador_role_checked(conn, user_id)
        return GRANTED

    except discord.Forbidden:
        log_to_gui(
            f"Sem permissao para dar o cargo Pescador a {getattr(member, 'id', '?')}: "
            "o bot precisa de 'Gerenciar Cargos' e o cargo Pescador precisa estar "
            "ABAIXO do cargo do bot na hierarquia.",
            "ERROR",
        )
        return FORBIDDEN
    except discord.HTTPException as e:
        log_to_gui(f"Falha ao dar o cargo Pescador a {getattr(member, 'id', '?')}: {e}", "ERROR")
        return ERROR
    except Exception as e:
        # Rede genérica: o fluxo de pesca não pode cair por causa de cargo,
        # nem mesmo por um erro que não previmos aqui.
        log_to_gui(f"Erro inesperado no cargo Pescador: {e!r}", "ERROR")
        return ERROR
