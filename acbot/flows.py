"""Cross-cog action flows (restart button callback used by several commands)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from .ac.process import ProcessError

if TYPE_CHECKING:
    from .bot import ACBot


async def restart_server(bot: ACBot, interaction: discord.Interaction) -> None:
    """Restart with the currently staged config; reports outcome as followup."""
    app = bot.app
    try:
        await app.process.restart(app.backend(), app.staging)
    except ProcessError as e:
        await interaction.followup.send(f"Restart failed: {e}", ephemeral=True)
        return
    except Exception as e:  # backend/staging errors carry user-facing messages
        await interaction.followup.send(f"Restart failed: {e}", ephemeral=True)
        return
    await bot.audit(interaction, "restarted the server (staged changes applied)")
    await interaction.followup.send("🔁 Server restarted with the staged config.",
                                    ephemeral=True)


def restart_callback(bot: ACBot):
    async def _cb(interaction: discord.Interaction) -> None:
        await restart_server(bot, interaction)
    return _cb
