"""/server start | stop | restart | status"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..ac.presets import find_preset, list_presets
from ..ac.process import CooldownError, StrayProcessError
from ..bot import admin_only
from ..ui import confirm
from ..util import fmt_duration

if TYPE_CHECKING:
    from ..bot import ACBot


async def preset_autocomplete(interaction: discord.Interaction,
                              current: str) -> list[app_commands.Choice[str]]:
    bot: ACBot = interaction.client  # type: ignore[assignment]
    presets_dir = bot.app.presets_dir()
    if presets_dir is None:
        return []
    q = current.lower()
    out = []
    for p in list_presets(presets_dir):
        if q and q not in p.name.lower():
            continue
        out.append(app_commands.Choice(name=p.name[:100], value=p.name[:100]))
        if len(out) >= 25:
            break
    return out


class ServerCog(commands.GroupCog, group_name="server",
                group_description="Control the Assetto Corsa server"):
    def __init__(self, bot: ACBot):
        self.bot = bot
        self.app = bot.app
        super().__init__()

    # -- status (open) -------------------------------------------------------

    @app_commands.command(name="status", description="Show server state")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        app = self.app
        running = app.process.is_running
        embed = discord.Embed(
            title="Assetto Corsa server",
            color=discord.Color.green() if running else discord.Color.dark_grey(),
        )
        embed.add_field(name="State", value="🟢 Online" if running else "⚫ Offline")
        embed.add_field(name="Backend", value=app.cfg.server.backend)
        preset = app.staging.preset_name() or "none staged"
        embed.add_field(name="Preset", value=preset)
        if running:
            embed.add_field(name="Uptime", value=fmt_duration(app.process.uptime_s))
            info = await app.server_info()
            if info:
                embed.add_field(name="Players", value=f"{info.clients}/{info.maxclients}")
                embed.add_field(name="Track", value=info.track or "?")
            drivers = [d for d in app.listener.roster.values() if d.connected]
            if drivers:
                names = ", ".join(d.name for d in drivers)[:1000]
                embed.add_field(name="On track", value=names, inline=False)
        url = app.join_url()
        if url and running:
            embed.add_field(name="Join", value=f"[Open in Content Manager]({url})",
                            inline=False)
        await interaction.followup.send(embed=embed)

    # -- start / stop / restart (admin) ---------------------------------------

    @app_commands.command(name="start", description="Start the server (optionally applying a preset first)")
    @app_commands.describe(preset="CM preset to apply before starting")
    @app_commands.autocomplete(preset=preset_autocomplete)
    @admin_only()
    async def start(self, interaction: discord.Interaction,
                    preset: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        app = self.app
        if preset:
            presets_dir = app.presets_dir()
            if presets_dir is None:
                await interaction.followup.send(
                    "No CM presets folder found — check `paths.cm_presets_dir`.",
                    ephemeral=True)
                return
            found = find_preset(presets_dir, preset)
            if found is None:
                await interaction.followup.send(f"Preset `{preset}` not found.",
                                                ephemeral=True)
                return
            app.staging.apply_preset(found)
            app.state.active_preset = found.name
            await self.bot.audit(interaction, f"applied preset `{found.name}`")

        try:
            await app.process.start(app.backend(), app.staging)
        except StrayProcessError as e:
            if await confirm(
                interaction,
                f"⚠️ {e}\n\nKill it and start my managed server?",
                confirm_label="Take over",
            ):
                await app.process.start(app.backend(), app.staging, take_over=True,
                                        skip_cooldown=True)
                await self.bot.audit(interaction, "took over a stray AC server and started")
                await interaction.followup.send("🟢 Stray killed, server started.",
                                                ephemeral=True)
            return
        except CooldownError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        await self.bot.audit(
            interaction, f"started the server (preset `{app.staging.preset_name()}`)"
        )
        await interaction.followup.send("🟢 Server started.", ephemeral=True)

    @app_commands.command(name="stop", description="Stop the server")
    @admin_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        if not self.app.process.is_running:
            await interaction.response.send_message("The server is not running.",
                                                    ephemeral=True)
            return
        if not await confirm(interaction,
                             "Stop the server? Everyone on track will be disconnected.",
                             confirm_label="Stop server"):
            return
        await self.app.process.stop()
        await self.bot.audit(interaction, "stopped the server")
        await interaction.followup.send("⚫ Server stopped.", ephemeral=True)

    @app_commands.command(name="restart", description="Restart the server (applies staged changes)")
    @admin_only()
    async def restart(self, interaction: discord.Interaction) -> None:
        if not await confirm(interaction,
                             "Restart the server? Everyone on track will be disconnected.",
                             confirm_label="Restart"):
            return
        try:
            await self.app.process.restart(self.app.backend(), self.app.staging)
        except CooldownError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        await self.bot.audit(interaction, "restarted the server")
        await interaction.followup.send("🔁 Server restarted.", ephemeral=True)
