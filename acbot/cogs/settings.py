"""/settings damage | collisions | time — staged server settings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from ..ac.backends.base import NotSupportedError
from ..bot import admin_only
from ..ui import RestartNowView
from ..util import parse_hhmm

if TYPE_CHECKING:
    from ..bot import ACBot


class SettingsCog(commands.GroupCog, group_name="settings",
                  group_description="Damage, collisions, time of day"):
    def __init__(self, bot: ACBot):
        self.bot = bot
        self.app = bot.app
        super().__init__()

    async def _respond_staged(self, interaction: discord.Interaction, change: str,
                              extra: str = "") -> None:
        if self.app.process.is_running:
            from ..flows import restart_callback
            view = RestartNowView(interaction.user.id, restart_callback(self.bot))
            await interaction.response.send_message(
                f"✅ Staged: {change}.{extra} Restart to apply.", view=view,
                ephemeral=True)
        else:
            await interaction.response.send_message(
                f"✅ Staged: {change}.{extra} Applies on next start.", ephemeral=True)

    @app_commands.command(name="damage", description="Set damage rate (0 = off, 100 = full)")
    @app_commands.describe(percent="Damage multiplier 0–100%")
    @admin_only()
    async def damage(self, interaction: discord.Interaction,
                     percent: app_commands.Range[int, 0, 100]) -> None:
        change = self.app.staging.set_damage(percent)
        await self.bot.audit(interaction, change)
        await self._respond_staged(interaction, change)

    @app_commands.command(name="collisions", description="Enable/disable car collisions (AssettoServer only)")
    @admin_only()
    async def collisions(self, interaction: discord.Interaction,
                         state: Literal["on", "off"]) -> None:
        backend = self.app.backend()
        try:
            change = backend.set_collisions(state == "on")
        except NotSupportedError as e:
            await interaction.response.send_message(f"ℹ️ {e}", ephemeral=True)
            return
        await self.bot.audit(interaction, change)
        await self._respond_staged(interaction, change)

    @app_commands.command(name="time", description="Set time of day (HH:MM)")
    @app_commands.describe(value="Time of day, e.g. 09:30 (vanilla supports 08:00–18:00)")
    @admin_only()
    async def time(self, interaction: discord.Interaction, value: str) -> None:
        try:
            hour, minute = parse_hhmm(value)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        change = self.app.staging.set_time(hour, minute)

        # AssettoServer can apply live via its console, if configured.
        live_cmd = self.app.backend().live_time_command(hour, minute)
        if live_cmd and self.app.process.is_running:
            if await self.app.process.send_console(live_cmd):
                await self.bot.audit(interaction, f"{change} (live)")
                await interaction.response.send_message(
                    f"✅ {change} — applied live and staged for future starts.",
                    ephemeral=True)
                return
        await self.bot.audit(interaction, change)
        await self._respond_staged(interaction, change)
