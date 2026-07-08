"""/upload link | pending — car-zip uploads that install only on admin approval.

A car uploaded via the HTTP upload page (see app.FileServer) is parked as the
single pending upload; this cog posts an approve/reject prompt to the configured
channel and installs it into content/cars when one admin approves.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..ac.uploads import PendingUpload, UploadError
from ..bot import admin_only, is_admin_member

if TYPE_CHECKING:
    from ..bot import ACBot

log = logging.getLogger(__name__)


class UploadApprovalView(discord.ui.View):
    """Persistent approve/reject buttons on a pending-upload prompt. Registered
    once so the buttons keep working across bot restarts."""

    def __init__(self, cog: UploadsCog):
        super().__init__(timeout=None)
        self.cog = cog

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if is_admin_member(self.cog.bot, interaction.user):
            return True
        await interaction.response.send_message(
            "You need an admin role to approve or reject installs.", ephemeral=True)
        return False

    @discord.ui.button(label="Approve install", style=discord.ButtonStyle.success,
                       emoji="✅", custom_id="acbot:upload:approve")
    async def approve(self, interaction: discord.Interaction,
                      _button: discord.ui.Button) -> None:
        if await self._guard(interaction):
            await self.cog.approve(interaction)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger,
                       emoji="🗑️", custom_id="acbot:upload:reject")
    async def reject(self, interaction: discord.Interaction,
                     _button: discord.ui.Button) -> None:
        if await self._guard(interaction):
            await self.cog.reject(interaction)


class UploadsCog(commands.GroupCog, group_name="upload",
                 group_description="Upload cars for admin-approved install"):
    def __init__(self, bot: ACBot):
        self.bot = bot
        self.app = bot.app
        # Which prompt message is currently live, so a newer upload can retire it.
        self._active: tuple[int, int] | None = None
        super().__init__()

    async def cog_load(self) -> None:
        self.bot.add_view(UploadApprovalView(self))  # persistent across restarts
        self.app.bus.subscribe("car_uploaded", self._on_uploaded)
        asyncio.create_task(self._resume_pending())

    # -- commands --------------------------------------------------------------

    @app_commands.command(name="link", description="Get the link to upload a car")
    async def link(self, interaction: discord.Interaction) -> None:
        if not self.app.public_ip:
            await interaction.response.send_message(
                "Upload server not ready (no public IP).", ephemeral=True)
            return
        url = f"http://{self.app.public_ip}:8082/upload"
        embed = discord.Embed(
            title="Upload a car",
            description=f"Open **[this upload page]({url})** and pick a car mod `.zip`.\n"
                        "It won't install until an admin approves it here.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Link", value=url, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="pending",
                          description="Show the car currently awaiting approval")
    @admin_only()
    async def pending(self, interaction: discord.Interaction) -> None:
        pending = self.app.uploads.pending()
        if pending is None:
            await interaction.response.send_message(
                "No car is currently awaiting approval.", ephemeral=True)
            return
        await self._post_prompt(pending)
        where = self._channel()
        target = where.mention if where else "the upload channel"
        await interaction.response.send_message(
            f"Posted an approval prompt for **{pending.label}** in {target}.",
            ephemeral=True)

    # -- upload event -> approval prompt ---------------------------------------

    async def _resume_pending(self) -> None:
        await self.bot.wait_until_ready()
        if self._active is None and self.app.uploads.pending() is not None:
            await self._post_prompt(self.app.uploads.pending())

    async def _on_uploaded(self, pending: PendingUpload, **_: object) -> None:
        # A newer upload overwrote the held zip — retire the previous prompt.
        await self._retire_active("⚠️ Superseded by a newer upload.")
        await self._post_prompt(pending)

    def _channel(self) -> discord.TextChannel | None:
        cid = self.app.cfg.discord.upload_channel_id
        if not cid:
            log.warning("car uploaded but discord.upload_channel_id is not set")
            return None
        channel = self.bot.get_channel(cid)
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _post_prompt(self, pending: PendingUpload) -> None:
        channel = self._channel()
        if channel is None:
            return
        embed = discord.Embed(
            title="🚗 Car upload awaiting approval",
            description="Approve to install it into `content/cars`, or reject to drop it.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Car(s)", value=", ".join(pending.cars) or "—", inline=False)
        embed.add_field(name="File", value=f"`{pending.filename}`", inline=False)
        embed.timestamp = datetime.fromtimestamp(pending.uploaded_at, tz=UTC)
        try:
            msg = await channel.send(embed=embed, view=UploadApprovalView(self))
        except discord.HTTPException:
            log.warning("could not post upload prompt to channel %s", channel.id)
            return
        self._active = (channel.id, msg.id)

    async def _retire_active(self, note: str) -> None:
        if self._active is None:
            return
        channel_id, message_id = self._active
        self._active = None
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        with contextlib.suppress(discord.HTTPException):
            msg = await channel.fetch_message(message_id)
            embed = msg.embeds[0] if msg.embeds else discord.Embed()
            embed.color = discord.Color.dark_grey()
            embed.set_footer(text=note)
            await msg.edit(embed=embed, view=None)

    # -- button actions --------------------------------------------------------

    async def approve(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            installed = await asyncio.to_thread(self.app.uploads.install)
        except UploadError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            return
        self._active = None
        with contextlib.suppress(Exception):
            await self.app.content.ensure_loaded(force=True)
        cars = ", ".join(installed)
        await self.bot.audit(interaction, f"approved car install: {cars}")
        # Clear the prompt from the channel now that it's installed.
        if interaction.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await interaction.message.delete()
        await interaction.followup.send(f"✅ Installed **{cars}**.", ephemeral=True)

    async def reject(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await asyncio.to_thread(self.app.uploads.discard)
        self._active = None
        await self.bot.audit(interaction, "rejected a pending car upload")
        await self._finish(interaction, discord.Color.dark_grey(),
                           f"🗑️ Upload rejected by {interaction.user.mention}.")

    async def _finish(self, interaction: discord.Interaction,
                      color: discord.Color, footer: str) -> None:
        msg = interaction.message
        if msg is None:
            return
        embed = msg.embeds[0] if msg.embeds else discord.Embed()
        embed.color = color
        embed.set_footer(text=footer)
        with contextlib.suppress(discord.HTTPException):
            await msg.edit(embed=embed, view=None)
