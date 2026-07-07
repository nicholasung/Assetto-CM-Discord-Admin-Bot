"""Discord client: cog loading, guild-scoped sync, role gate, audit trail."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .ac.backends.base import BackendError, NotSupportedError
from .ac.process import ProcessError
from .ac.staging import StagingError
from .app import App

log = logging.getLogger(__name__)
audit_log = logging.getLogger("acbot.audit")

USER_FACING_ERRORS = (ProcessError, StagingError, BackendError, NotSupportedError)


def is_admin_member(bot: ACBot, user: discord.abc.User) -> bool:
    if not isinstance(user, discord.Member):
        return False
    if user.guild_permissions.administrator:
        return True
    allowed = set(bot.app.cfg.discord.admin_role_ids)
    return any(role.id in allowed for role in user.roles)


def admin_only():
    """Decorator gating state-changing commands behind the configured roles."""
    def predicate(interaction: discord.Interaction) -> bool:
        bot = interaction.client
        assert isinstance(bot, ACBot)
        return is_admin_member(bot, interaction.user)
    return app_commands.check(predicate)


class ACBot(commands.Bot):
    def __init__(self, app: App):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!acbot ", intents=intents, help_command=None)
        self.app = app

    async def setup_hook(self) -> None:
        from .cogs.downloads import DownloadsCog
        from .cogs.entries import EntriesCog
        from .cogs.leaderboard import LeaderboardCog
        from .cogs.presets import PresetsCog
        from .cogs.server import ServerCog
        from .cogs.settings import SettingsCog
        from .cogs.status import StatusCog
        from .cogs.uploads import UploadsCog

        await self.app.startup()
        for cog_cls in (ServerCog, PresetsCog, EntriesCog, SettingsCog,
                        StatusCog, LeaderboardCog, DownloadsCog, UploadsCog):
            await self.add_cog(cog_cls(self))
        await self.app.autostart_if_configured()

        guild = discord.Object(id=self.app.cfg.discord.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.tree.on_error = self.on_app_command_error

    async def close(self) -> None:
        await self.app.shutdown()
        await super().close()

    async def on_ready(self) -> None:
        log.info("logged in as %s (guild %s)", self.user, self.app.cfg.discord.guild_id)

    # -- error surface ---------------------------------------------------------

    async def on_app_command_error(self, interaction: discord.Interaction,
                                   error: app_commands.AppCommandError) -> None:
        cause = getattr(error, "original", error)
        if isinstance(error, app_commands.CheckFailure) and not isinstance(
            error, app_commands.CommandOnCooldown
        ):
            msg = "You need an admin role to use this command."
        elif isinstance(cause, USER_FACING_ERRORS):
            msg = str(cause)
        else:
            log.exception("command %s failed", getattr(interaction.command, "name", "?"),
                          exc_info=cause)
            msg = "Something went wrong — check the bot logs."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    # -- audit -------------------------------------------------------------------

    async def audit(self, interaction: discord.Interaction, action: str) -> None:
        who = f"{interaction.user} ({interaction.user.id})"
        audit_log.info("%s | %s", who, action)
        channel_id = self.app.cfg.discord.audit_channel_id
        if not channel_id:
            return
        channel = self.get_channel(channel_id)
        if channel is None:
            return
        embed = discord.Embed(description=action, color=discord.Color.orange())
        embed.set_author(name=str(interaction.user),
                         icon_url=interaction.user.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.warning("could not write to audit channel %s", channel_id)
