"""/join + the persistent auto-updating status message."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import admin_only
from ..util import fmt_duration

if TYPE_CHECKING:
    from ..bot import ACBot

log = logging.getLogger(__name__)


class StatusCog(commands.Cog):
    status_group = app_commands.Group(name="status",
                                      description="Persistent server status message")

    def __init__(self, bot: ACBot):
        self.bot = bot
        self.app = bot.app
        self._debounce: asyncio.Task | None = None
        self.refresh_loop.change_interval(seconds=max(10, self.app.cfg.server.status_poll_s))

    async def cog_load(self) -> None:
        for event in ("server_started", "server_stopped", "driver_joined",
                      "driver_left", "session_info"):
            self.app.bus.subscribe(event, self._on_change)
        self.app.bus.subscribe("server_exited", self._on_server_exited)
        self.refresh_loop.start()

    async def cog_unload(self) -> None:
        self.refresh_loop.cancel()

    # -- /join -----------------------------------------------------------------

    @app_commands.command(name="join", description="Get the Content Manager join link")
    async def join(self, interaction: discord.Interaction) -> None:
        url = self.app.join_url()
        if url is None:
            await interaction.response.send_message(
                "Join link unavailable: public IP unknown or no preset staged. "
                "Set `server.public_ip` in config.yaml.", ephemeral=True)
            return
        running = self.app.process.is_running
        embed = discord.Embed(
            title=self._server_name(),
            description="Click below to join via Content Manager."
                        + ("" if running else "\n⚫ **The server is currently offline.**"),
            color=discord.Color.green() if running else discord.Color.dark_grey(),
        )
        with contextlib.suppress(Exception):
            track, layout = self.app.staging.track()
            embed.add_field(name="Track", value=f"{track} {layout}".strip() or "?")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Join in Content Manager", url=url))
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="webui", description="Get the admin web UI link")
    async def webui(self, interaction: discord.Interaction) -> None:
        if self.app.web_server is None:
            await interaction.response.send_message(
                "The web UI is not running — check `web.enabled` and the bot logs.",
                ephemeral=True)
            return
        base = self.app.public_http_base()
        if base is None:
            await interaction.response.send_message(
                "Web UI link unavailable: public IP unknown. "
                "Set `server.public_ip` in config.yaml.", ephemeral=True)
            return
        url = base
        embed = discord.Embed(
            title="Admin web UI",
            description=f"Open **[the admin dashboard]({url})** to start/stop the server, "
                        "change settings, manage uploads, and view the leaderboard.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Link", value=url, inline=False)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open admin dashboard", url=url))
        await interaction.response.send_message(embed=embed, view=view)

    # -- /status pin -------------------------------------------------------------

    @status_group.command(name="pin", description="(Re)create the auto-updating status message here or in the configured channel")
    @admin_only()
    async def pin(self, interaction: discord.Interaction) -> None:
        channel_id = self.app.cfg.discord.status_channel_id or interaction.channel_id
        channel = self.bot.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Status channel not found — set `discord.status_channel_id`.",
                ephemeral=True)
            return
        # Drop the previous status message, if any.
        old = self.app.state.status_message
        if old:
            with contextlib.suppress(discord.HTTPException):
                old_channel = self.bot.get_channel(old[0])
                if isinstance(old_channel, discord.TextChannel):
                    old_msg = await old_channel.fetch_message(old[1])
                    await old_msg.delete()
        embed, view = await self._build_status()
        msg = await channel.send(embed=embed, view=view)
        self.app.state.status_message = (channel.id, msg.id)
        await self.bot.audit(interaction, f"pinned the status message in #{channel.name}")
        await interaction.response.send_message(
            f"📌 Status message created in {channel.mention}; it refreshes every "
            f"{self.app.cfg.server.status_poll_s}s.", ephemeral=True)

    # -- refresh machinery ---------------------------------------------------------

    def _server_name(self) -> str:
        try:
            return self.app.staging.server_name()
        except Exception:
            return "Assetto Corsa server"

    async def _build_status(self) -> tuple[discord.Embed, discord.ui.View | None]:
        app = self.app
        running = app.process.is_running
        embed = discord.Embed(
            title=self._server_name(),
            color=discord.Color.green() if running else discord.Color.dark_grey(),
        )
        embed.add_field(name="State", value="🟢 Online" if running else "⚫ Offline")
        preset = app.staging.preset_name()
        if preset:
            embed.add_field(name="Preset", value=preset)
        with contextlib.suppress(Exception):
            track, layout = app.staging.track()
            embed.add_field(name="Track", value=f"{track} {layout}".strip() or "?")
        view: discord.ui.View | None = None
        if running:
            embed.add_field(name="Uptime", value=fmt_duration(app.process.uptime_s))
            info = await app.server_info()
            drivers = [d for d in app.listener.roster.values() if d.connected]
            if info:
                embed.add_field(name="Players", value=f"{info.clients}/{info.maxclients}")
                session = app.listener.session
                if session:
                    left = fmt_duration(info.timeleft) if info.timeleft else "—"
                    embed.add_field(name="Session",
                                    value=f"{session.type_name} · {left} left")
            if drivers:
                names = "\n".join(
                    f"• {d.name} — {d.model}" for d in
                    sorted(drivers, key=lambda d: d.car_id)
                )
                embed.add_field(name=f"On track ({len(drivers)})",
                                value=names[:1000], inline=False)
            url = app.join_url()
            if url:
                view = discord.ui.View(timeout=None)
                view.add_item(discord.ui.Button(label="Join in Content Manager", url=url))
        embed.set_footer(text="Updated")
        embed.timestamp = discord.utils.utcnow()
        return embed, view

    async def _refresh_status_message(self) -> None:
        stored = self.app.state.status_message
        if not stored:
            return
        channel = self.bot.get_channel(stored[0])
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(stored[1])
        except discord.NotFound:
            log.info("status message deleted; forgetting it (re-run /status pin)")
            self.app.state.status_message = None
            return
        except discord.HTTPException:
            return
        embed, view = await self._build_status()
        with contextlib.suppress(discord.HTTPException):
            await msg.edit(embed=embed, view=view)

    @tasks.loop(seconds=30)
    async def refresh_loop(self) -> None:
        await self._refresh_status_message()

    @refresh_loop.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def _on_change(self, **_: object) -> None:
        """Bus event -> refresh soon (debounced so join floods coalesce)."""
        if self._debounce and not self._debounce.done():
            return
        async def _later() -> None:
            await asyncio.sleep(2)
            await self._refresh_status_message()
        self._debounce = asyncio.create_task(_later())

    async def _on_server_exited(self, code: object = None, tail: str = "",
                                **_: object) -> None:
        await self._on_change()
        channel_id = self.app.cfg.discord.status_channel_id
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            msg = (f"⚠️ The AC server exited unexpectedly (code {code}). "
                   "An admin can `/server start` it again.")
            if tail:
                # Discord code blocks cap at 4096; keep the last chunk of output.
                snippet = tail[-1500:]
                msg += f"\nLast output before it quit:\n```\n{snippet}\n```"
            with contextlib.suppress(discord.HTTPException):
                await channel.send(msg)
