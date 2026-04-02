"""
Moderação — advertências, histórico e perdão (Nota Fiscal).
"""
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config import MOD_ROLE_IDS, WARN_CHANNEL_ID, set_bot_instance


class ModeracaoCog(commands.Cog):
    """Comandos de justiça com embeds estilo Nota Fiscal."""

    def __init__(self, bot):
        self.bot = bot
        set_bot_instance(bot)

    @app_commands.command(name="advertencia", description="Registra uma advertência (Somente Moderadores).")
    @app_commands.describe(
        usuario="O usuário infrator",
        motivo="Motivo da advertência",
        prova_imagem="Print/Imagem (Opcional)",
        prova_texto="Link ou texto da prova (Opcional)",
    )
    async def slash_advertencia(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        motivo: str,
        prova_imagem: discord.Attachment = None,
        prova_texto: str = None,
    ):
        await interaction.response.defer()

        if interaction.channel_id != WARN_CHANNEL_ID:
            await interaction.followup.send(
                f"🚫 Comando permitido apenas no canal <#{WARN_CHANNEL_ID}>.",
                ephemeral=True,
            )
            return

        has_role = any(role.id in MOD_ROLE_IDS for role in interaction.user.roles)
        if not has_role:
            await interaction.followup.send("🚫 Acesso Negado. Apenas moderadores.", ephemeral=True)
            return

        proof_final = "Nenhuma prova anexada."
        if prova_imagem:
            proof_final = prova_imagem.url
        elif prova_texto:
            proof_final = prova_texto
        if prova_imagem and prova_texto:
            proof_final = f"{prova_texto}\n{prova_imagem.url}"

        cursor = self.bot.db_conn.cursor()
        cursor.execute(
            """
            INSERT INTO warnings (user_id, user_name, moderator_id, moderator_name, reason, proof)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                usuario.id,
                usuario.name,
                interaction.user.id,
                interaction.user.name,
                motivo,
                proof_final,
            ),
        )
        self.bot.db_conn.commit()

        count = cursor.execute(
            "SELECT COUNT(*) FROM warnings WHERE user_id = ? AND status = 'active'",
            (usuario.id,),
        ).fetchone()[0]
        history = cursor.execute(
            "SELECT reason FROM warnings WHERE user_id = ? AND status = 'active'",
            (usuario.id,),
        ).fetchall()

        suggestion = "Nenhuma ação automática sugerida."
        color = discord.Color.orange()

        if count >= 4:
            color = discord.Color.red()
            suggestion = "⚠️ **LIMITE DE 4 WARNS ATIVOS ATINGIDO.** Sugestão: **Ban/Kick**."

            cog = self.bot.get_cog("P3luchePersona")
            if cog and getattr(cog, "ai_client", None):
                hist_str = ", ".join([h[0] for h in history])
                try:
                    prompt = (
                        f"O usuário {usuario.name} atingiu 4 advertências ativas. "
                        f"Histórico recente: {hist_str}. Última infração: {motivo}. "
                        f"Como moderador robô, sugira uma punição curta e severa."
                    )
                    ai_resp = await cog.ai_client.aio.models.generate_content(
                        model=cog.ai_model_name,
                        contents=prompt,
                    )
                    suggestion = f"🤖 **Análise P3LUCHE:** {ai_resp.text}"
                except Exception:
                    pass

        embed = discord.Embed(
            title="🧾 REGISTRO DE ADVERTÊNCIA (Nota Fiscal)",
            color=color,
            timestamp=datetime.now(),
        )
        embed.add_field(name="Infrator", value=f"{usuario.mention}\n(ID: {usuario.id})", inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Contagem Ativa", value=f"**{count}/4**", inline=True)
        embed.add_field(name="📄 Motivo", value=motivo, inline=False)
        embed.add_field(name="🔗 Provas", value=proof_final, inline=False)
        embed.add_field(name="⚖️ Veredito/Sugestão", value=suggestion, inline=False)
        embed.set_footer(text="Sistema de Justiça P3LUCHE v2.2 (Governança Ativa)")

        await interaction.followup.send(content=usuario.mention, embed=embed)

        try:
            dm_embed = embed.copy()
            dm_embed.title = "🚨 VOCÊ RECEBEU UMA ADVERTÊNCIA"
            dm_embed.description = (
                "Seu comportamento foi registrado. Avisos acumulados podem levar a banimento."
            )
            await usuario.send(embed=dm_embed)
        except Exception:
            pass

    @app_commands.command(name="historico", description="Ver o histórico completo (Ativos e Perdoados).")
    @app_commands.describe(usuario="O usuário para consultar")
    async def historico_warns(self, interaction: discord.Interaction, usuario: discord.Member):
        has_role = any(role.id in MOD_ROLE_IDS for role in interaction.user.roles)
        if not has_role:
            await interaction.response.send_message("🚫 Acesso restrito a moderadores.", ephemeral=True)
            return

        cursor = self.bot.db_conn.cursor()
        rows = cursor.execute(
            """
            SELECT id, moderator_name, reason, created_at, proof, status, revoked_by
            FROM warnings
            WHERE user_id = ?
            ORDER BY created_at DESC
        """,
            (usuario.id,),
        ).fetchall()

        if not rows:
            await interaction.response.send_message(
                f"✅ Ficha limpa! O usuário **{usuario.name}** nunca foi advertido.",
                ephemeral=True,
            )
            return

        active_count = sum(1 for r in rows if r["status"] == "active")
        revoked_count = sum(1 for r in rows if r["status"] != "active")

        embed = discord.Embed(title=f"📂 Ficha Criminal: {usuario.name}", color=discord.Color.orange())
        embed.set_thumbnail(url=usuario.avatar.url if usuario.avatar else None)
        embed.set_footer(text=f"Ativos: {active_count} | Perdoados: {revoked_count} | ID: {usuario.id}")

        description_text = ""
        for row in rows:
            w_id = row["id"]
            mod = row["moderator_name"]
            reason = row["reason"]
            date = row["created_at"]
            status = row["status"]

            icon = "🔴" if status == "active" else "🟢"
            status_text = "**ATIVO**" if status == "active" else f"~REVOGADO por {row['revoked_by']}~"

            proof_display = "[Prova]" if "http" in row["proof"] else "Texto"

            entry = (
                f"{icon} **ID: {w_id}** | {date}\n👮 {mod} | ⚖️ {status_text}\n📝 {reason}\n🔗 {proof_display}\nFAILED_SEPARATOR"
            )
            description_text += entry

        chunks = description_text.split("FAILED_SEPARATOR")
        for chunk in chunks:
            if chunk.strip():
                embed.add_field(name="➖ Registro ➖", value=chunk, inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="perdoar", description="Revoga uma advertência (Soft Delete).")
    @app_commands.describe(id_advertencia="O ID da advertência")
    async def remover_warn(self, interaction: discord.Interaction, id_advertencia: int):
        has_role = any(role.id in MOD_ROLE_IDS for role in interaction.user.roles)
        if not has_role:
            await interaction.response.send_message("🚫 Acesso restrito a moderadores.", ephemeral=True)
            return

        cursor = self.bot.db_conn.cursor()

        check = cursor.execute(
            "SELECT user_id, user_name, status FROM warnings WHERE id = ?",
            (id_advertencia,),
        ).fetchone()
        if not check:
            await interaction.response.send_message(
                f"❌ ID **{id_advertencia}** não encontrado.",
                ephemeral=True,
            )
            return

        user_id, user_name, current_status = check["user_id"], check["user_name"], check["status"]

        if current_status != "active":
            await interaction.response.send_message(
                f"⚠️ Essa advertência já foi revogada anteriormente.",
                ephemeral=True,
            )
            return

        try:
            cursor.execute(
                """
                UPDATE warnings
                SET status = 'revoked',
                    revoked_by = ?,
                    revoked_at = ?
                WHERE id = ?
            """,
                (interaction.user.name, datetime.now(), id_advertencia),
            )
            self.bot.db_conn.commit()

            new_count = cursor.execute(
                "SELECT COUNT(*) FROM warnings WHERE user_id = ? AND status = 'active'",
                (user_id,),
            ).fetchone()[0]

            embed = discord.Embed(
                title="⚖️ INDULTO CONCEDIDO (Revogação)",
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
            embed.add_field(name="Beneficiário", value=f"{user_name}", inline=True)
            embed.add_field(name="Autor do Perdão", value=interaction.user.mention, inline=True)
            embed.add_field(name="ID Revogado", value=str(id_advertencia), inline=True)
            embed.add_field(name="Nova Contagem Ativa", value=f"**{new_count}/4**", inline=False)
            embed.set_footer(text="O registro foi mantido no histórico, mas não conta mais para punição.")

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            await interaction.response.send_message(f"Erro ao revogar: {e}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ModeracaoCog(bot))
