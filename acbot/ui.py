"""Shared interactive views (confirm dialogs, restart-now button)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord


class ConfirmView(discord.ui.View):
    """Yes/No confirmation locked to the invoking user."""

    def __init__(self, user_id: int, confirm_label: str = "Confirm",
                 danger: bool = True, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.confirmed: bool | None = None
        self.confirm_button.label = confirm_label
        self.confirm_button.style = (
            discord.ButtonStyle.danger if danger else discord.ButtonStyle.primary
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This confirmation isn't yours.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction,
                             _button: discord.ui.Button) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction,
                            _button: discord.ui.Button) -> None:
        self.confirmed = False
        self.stop()
        await interaction.response.defer()


async def confirm(interaction: discord.Interaction, prompt: str,
                  confirm_label: str = "Confirm", danger: bool = True) -> bool:
    """Send an ephemeral confirm dialog; returns True when confirmed."""
    view = ConfirmView(interaction.user.id, confirm_label=confirm_label, danger=danger)
    if interaction.response.is_done():
        msg = await interaction.followup.send(prompt, view=view, ephemeral=True, wait=True)
    else:
        await interaction.response.send_message(prompt, view=view, ephemeral=True)
        msg = await interaction.original_response()
    await view.wait()
    for child in view.children:
        child.disabled = True  # type: ignore[attr-defined]
    try:
        await msg.edit(view=view)
    except discord.HTTPException:
        pass
    return view.confirmed is True


class RestartNowView(discord.ui.View):
    """'Restart now' follow-up attached to staged-change confirmations."""

    def __init__(self, user_id: int, on_restart: Callable[[discord.Interaction], Awaitable[None]],
                 timeout: float = 300.0):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.on_restart = on_restart

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the admin who made the change can use this button.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Restart server now", style=discord.ButtonStyle.danger,
                       emoji="🔁")
    async def restart_button(self, interaction: discord.Interaction,
                             button: discord.ui.Button) -> None:
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await self.on_restart(interaction)
        self.stop()
