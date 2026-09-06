import datetime
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from utils.access import has_moderation_command_access
from utils.storage import (
    add_warning,
    get_active_warnings_for_user,
    get_premade_warning_reasons,
    revoke_warning,
)


PRESET_PREFIX = "__preset__:"


WARNING_TIERS = {
    "W1": {
        "color": discord.Color.yellow(),
        "title": "W1 Issued",
        "description": "A first-tier warning has been issued.",
    },
    "W2": {
        "color": discord.Color.orange(),
        "title": "W2 Issued",
        "description": "A second-tier warning has been issued.",
    },
    "W3": {
        "color": discord.Color.red(),
        "title": "W3 Issued",
        "description": "A final-tier warning has been issued.",
    },
}


class WarningsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _has_access(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        role_ids = [str(role.id) for role in getattr(member, "roles", [])]
        return has_moderation_command_access(member.id, role_ids)

    async def _deny_if_no_access(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if self._has_access(interaction):
            return False

        await interaction.response.send_message(
            "⛔ You do not have permission to use moderation warning commands.",
            ephemeral=True,
        )
        return True

    def _resolve_reason(self, submitted_reason: str) -> str | None:
        """Resolve a selected preset ID to full stored text.

        A normal manually typed reason passes through unchanged. A selected
        autocomplete value starts with PRESET_PREFIX and is looked up here.
        """
        value = (submitted_reason or "").strip()

        if not value.startswith(PRESET_PREFIX):
            return value or None

        preset_id_text = value.removeprefix(PRESET_PREFIX)

        try:
            preset_id = int(preset_id_text)
        except ValueError:
            return None

        for preset in get_premade_warning_reasons():
            if int(preset.get("id", 0)) == preset_id:
                full_reason = str(preset.get("reason") or "").strip()
                return full_reason or None

        return None

    async def reason_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        query = (current or "").strip().lower()
        choices: list[app_commands.Choice[str]] = []

        for preset in get_premade_warning_reasons():
            preset_id = preset.get("id")
            label = (preset.get("name") or "Unnamed preset").strip()
            reason_text = (preset.get("reason") or "").strip()

            if not preset_id or not reason_text:
                continue

            searchable = f"{label} {reason_text}".lower()
            if query and query not in searchable:
                continue

            preview = reason_text.replace("\n", " ").strip()
            display = f"{label} — {preview}"
            choice_name = display[:100]

            choices.append(
                app_commands.Choice(
                    name=choice_name,
                    value=f"{PRESET_PREFIX}{preset_id}",
                )
            )

            if len(choices) >= 25:
                break

        return choices

    async def warning_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        namespace = interaction.namespace
        target_user = getattr(namespace, "user", None)

        if target_user is None:
            return []

        active_warnings = get_active_warnings_for_user(target_user.id)
        query = (current or "").strip().lower()
        choices: list[app_commands.Choice[str]] = []

        for warning in active_warnings:
            case_id = str(warning.get("case_id", ""))
            tier = str(warning.get("tier", "Warning"))
            reason = str(warning.get("reason", "No reason provided."))
            issuer_name = str(warning.get("issued_by_name", "Unknown staff"))

            issued_at = warning.get("issued_at")
            try:
                issued_date = datetime.datetime.fromisoformat(
                    str(issued_at).replace("Z", "+00:00")
                ).strftime("%b %d, %Y")
            except (TypeError, ValueError):
                issued_date = "Unknown date"

            display = f"{tier} · {reason} · {issued_date} · @{issuer_name}"
            searchable = f"{display} {case_id}".lower()

            if query and query not in searchable:
                continue

            choices.append(
                app_commands.Choice(
                    name=display[:100],
                    value=case_id,
                )
            )

            if len(choices) >= 25:
                break

        return choices

    async def issue_warning(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
        tier: Literal["W1", "W2", "W3"],
    ) -> None:
        if await self._deny_if_no_access(interaction):
            return

        final_reason = self._resolve_reason(reason)
        if not final_reason:
            await interaction.response.send_message(
                "❌ A valid warning reason is required.",
                ephemeral=True,
            )
            return

        warning = add_warning(
            user_id=user.id,
            username=str(user),
            tier=tier,
            reason=final_reason,
            issued_by_id=interaction.user.id,
            issued_by_name=str(interaction.user),
        )

        config = WARNING_TIERS[tier]
        embed = discord.Embed(
            title=f"⚠️ {config['title']}",
            description=config["description"],
            color=config["color"],
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(
            name="User",
            value=f"{user.mention}\nID: `{user.id}`",
        )
        embed.add_field(
            name="Warning",
            value=f"{tier} · Case `{warning['case_id']}`",
        )
        embed.add_field(
            name="Reason",
            value=final_reason[:1024],
            inline=False,
        )
        embed.add_field(
            name="Issued by",
            value=f"{interaction.user.mention}\nID: `{interaction.user.id}`",
        )
        embed.set_footer(text="Moderation warning record")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="w1",
        description="Issue a first-tier warning to a member.",
    )
    @app_commands.describe(
        user="The member receiving the W1 warning.",
        reason="Select a premade reason or type a custom reason.",
    )
    @app_commands.autocomplete(reason=reason_autocomplete)
    async def w1(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
    ) -> None:
        await self.issue_warning(interaction, user, reason, "W1")

    @app_commands.command(
        name="w2",
        description="Issue a second-tier warning to a member.",
    )
    @app_commands.describe(
        user="The member receiving the W2 warning.",
        reason="Select a premade reason or type a custom reason.",
    )
    @app_commands.autocomplete(reason=reason_autocomplete)
    async def w2(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
    ) -> None:
        await self.issue_warning(interaction, user, reason, "W2")

    @app_commands.command(
        name="w3",
        description="Issue a final-tier warning to a member.",
    )
    @app_commands.describe(
        user="The member receiving the W3 warning.",
        reason="Select a premade reason or type a custom reason.",
    )
    @app_commands.autocomplete(reason=reason_autocomplete)
    async def w3(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
    ) -> None:
        await self.issue_warning(interaction, user, reason, "W3")

    @app_commands.command(
        name="unwarn",
        description="Revoke one specific active warning from a member.",
    )
    @app_commands.describe(
        user="The member whose warning will be revoked.",
        warning="Choose the exact active warning to revoke.",
        reason="Explain why this warning is being revoked.",
    )
    @app_commands.autocomplete(warning=warning_autocomplete)
    async def unwarn(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        warning: str,
        reason: str,
    ) -> None:
        if await self._deny_if_no_access(interaction):
            return

        revoke_reason = (reason or "").strip()
        if not revoke_reason:
            await interaction.response.send_message(
                "❌ A revocation reason is required.",
                ephemeral=True,
            )
            return

        revoked_warning = revoke_warning(
            case_id=warning,
            user_id=user.id,
            revoked_by_id=interaction.user.id,
            revoked_by_name=str(interaction.user),
            revoke_reason=revoke_reason,
        )

        if not revoked_warning:
            await interaction.response.send_message(
                "❌ That warning was not found, does not belong to that user, "
                "or has already been revoked.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="✅ Warning Revoked",
            description="One specific warning has been marked as revoked.",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(
            name="User",
            value=f"{user.mention}\nID: `{user.id}`",
        )
        embed.add_field(
            name="Revoked warning",
            value=(
                f"{revoked_warning.get('tier', 'Warning')} · "
                f"Case `{revoked_warning.get('case_id')}`"
            ),
        )
        embed.add_field(
            name="Original reason",
            value=str(
                revoked_warning.get("reason", "No reason provided.")
            )[:1024],
            inline=False,
        )
        embed.add_field(
            name="Originally issued by",
            value=revoked_warning.get("issued_by_name", "Unknown staff"),
        )
        embed.add_field(
            name="Revoked by",
            value=f"{interaction.user.mention}\nID: `{interaction.user.id}`",
        )
        embed.add_field(
            name="Revocation reason",
            value=revoke_reason[:1024],
            inline=False,
        )
        embed.set_footer(text="Moderation warning audit record")

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WarningsCog(bot))
