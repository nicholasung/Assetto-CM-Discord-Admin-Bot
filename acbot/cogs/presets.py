"""/preset list | apply — Content Manager server presets."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..ac.presets import find_preset, list_presets
from ..bot import admin_only
from ..ui import RestartNowView
from .server import preset_autocomplete

if TYPE_CHECKING:
    from ..bot import ACBot


class PresetsCog(commands.GroupCog, group_name="preset",
                 group_description="Content Manager server presets"):
    def __init__(self, bot: ACBot):
        self.bot = bot
        self.app = bot.app
        super().__init__()

    @app_commands.command(name="list", description="List available CM server presets")
    async def list_cmd(self, interaction: discord.Interaction) -> None:
        presets_dir = self.app.presets_dir()
        if presets_dir is None:
            await interaction.response.send_message(
                "No CM presets folder found. Set `paths.cm_presets_dir` in config.yaml "
                "(run `acbot doctor` on the VM to see the candidates).",
                ephemeral=True,
            )
            return
        presets = list_presets(presets_dir)
        if not presets:
            await interaction.response.send_message(
                f"No presets in `{presets_dir}`.", ephemeral=True)
            return
        active = self.app.staging.preset_name()
        embed = discord.Embed(title="Server presets", color=discord.Color.blurple())
        embed.set_footer(text=str(presets_dir))
        for p in presets[:20]:
            marker = " ✅ (active)" if p.name == active else ""
            cars = ", ".join(p.cars[:4]) + ("…" if len(p.cars) > 4 else "")
            embed.add_field(
                name=f"{p.name}{marker}",
                value=f"{p.track_label or '?'} · {p.max_clients} slots\n{cars or 'no cars?'}",
                inline=False,
            )
        if len(presets) > 20:
            embed.description = f"Showing 20 of {len(presets)} presets."
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="apply", description="Stage a CM preset (takes effect on restart)")
    @app_commands.autocomplete(name=preset_autocomplete)
    @admin_only()
    async def apply(self, interaction: discord.Interaction, name: str) -> None:
        presets_dir = self.app.presets_dir()
        if presets_dir is None:
            await interaction.response.send_message(
                "No CM presets folder found — check `paths.cm_presets_dir`.",
                ephemeral=True)
            return
        preset = find_preset(presets_dir, name)
        if preset is None:
            await interaction.response.send_message(f"Preset `{name}` not found.",
                                                    ephemeral=True)
            return
        self.app.staging.apply_preset(preset)
        self.app.state.active_preset = preset.name
        await self.bot.audit(interaction, f"applied preset `{preset.name}`")

        msg = (f"✅ Staged preset **{preset.name}** — {preset.track_label}, "
               f"{len(preset.cars)} car model(s), {preset.max_clients} slots.")
        if self.app.process.is_running:
            from ..flows import restart_callback
            view = RestartNowView(interaction.user.id, restart_callback(self.bot))
            await interaction.response.send_message(
                msg + "\nThe running server still uses the old config.",
                view=view, ephemeral=True)
        else:
            await interaction.response.send_message(
                msg + "\nStart it with `/server start`.", ephemeral=True)
