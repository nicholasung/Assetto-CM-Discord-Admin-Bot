"""/entry list | setcar | setskin — edit the staged entry list."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import admin_only
from ..ui import RestartNowView
from ..util import truncate

if TYPE_CHECKING:
    from ..bot import ACBot


def _app(interaction: discord.Interaction):
    return interaction.client.app  # type: ignore[attr-defined]


async def slot_autocomplete(interaction: discord.Interaction,
                            current: str) -> list[app_commands.Choice[int]]:
    try:
        entries = _app(interaction).staging.entries()
    except Exception:
        return []
    q = current.lower()
    out = []
    for e in entries:
        if q and q not in e.label.lower() and q != str(e.slot):
            continue
        out.append(app_commands.Choice(name=truncate(e.label), value=e.slot))
        if len(out) >= 25:
            break
    return out


async def car_autocomplete(interaction: discord.Interaction,
                           current: str) -> list[app_commands.Choice[str]]:
    cars = _app(interaction).content.search(current)
    return [app_commands.Choice(name=truncate(c.label), value=c.car_id) for c in cars]


async def skin_for_car_autocomplete(interaction: discord.Interaction,
                                    current: str) -> list[app_commands.Choice[str]]:
    car_id = getattr(interaction.namespace, "car", None)
    return _skin_choices(interaction, car_id, current)


async def skin_for_slot_autocomplete(interaction: discord.Interaction,
                                     current: str) -> list[app_commands.Choice[str]]:
    app = _app(interaction)
    slot = getattr(interaction.namespace, "slot", None)
    car_id = None
    if slot is not None:
        try:
            entry = app.staging.entry(int(slot))
            car_id = entry.model if entry else None
        except Exception:
            car_id = None
    return _skin_choices(interaction, car_id, current)


def _skin_choices(interaction: discord.Interaction, car_id: str | None,
                  current: str) -> list[app_commands.Choice[str]]:
    if not car_id:
        return []
    skins = _app(interaction).content.skins_for(car_id)
    q = current.lower()
    return [
        app_commands.Choice(name=truncate(s), value=s)
        for s in skins if not q or q in s.lower()
    ][:25]


class EntriesCog(commands.GroupCog, group_name="entry",
                 group_description="Entry list: cars and skins"):
    def __init__(self, bot: ACBot):
        self.bot = bot
        self.app = bot.app
        super().__init__()

    def _restart_view_or_hint(self) -> tuple[str, discord.ui.View | None]:
        if self.app.process.is_running:
            return " (takes effect on restart)", None
        return " (applies next start)", None

    async def _respond_staged(self, interaction: discord.Interaction, change: str) -> None:
        if self.app.process.is_running:
            from ..flows import restart_callback
            view = RestartNowView(interaction.user.id, restart_callback(self.bot))
            await interaction.response.send_message(
                f"✅ Staged: {change}\nThe running server still uses the old entry list.",
                view=view, ephemeral=True)
        else:
            await interaction.response.send_message(
                f"✅ Staged: {change} — applies on next `/server start`.", ephemeral=True)

    # -- commands ------------------------------------------------------------

    @app_commands.command(name="list", description="Show the staged entry list")
    async def list_cmd(self, interaction: discord.Interaction) -> None:
        entries = self.app.staging.entries()
        if not entries:
            await interaction.response.send_message("Entry list is empty.", ephemeral=True)
            return
        roster = {d.car_id: d for d in self.app.listener.roster.values() if d.connected}
        lines = []
        for e in entries[:40]:
            who = roster.get(e.slot)
            occupied = f" — 👤 {who.name}" if who else ""
            lines.append(f"`#{e.slot:>2}` **{e.model}** [{e.skin or 'default'}]{occupied}")
        embed = discord.Embed(
            title="Entry list (staged)",
            description="\n".join(lines)[:4000],
            color=discord.Color.blurple(),
        )
        if len(entries) > 40:
            embed.set_footer(text=f"{len(entries) - 40} more slots not shown")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="setcar", description="Swap the car in an entry slot")
    @app_commands.describe(slot="Entry slot", car="Car (installed on the server)",
                           skin="Skin (defaults to a valid one for the car)")
    @app_commands.autocomplete(slot=slot_autocomplete, car=car_autocomplete,
                               skin=skin_for_car_autocomplete)
    @admin_only()
    async def setcar(self, interaction: discord.Interaction, slot: int, car: str,
                     skin: str | None = None) -> None:
        entry = self.app.staging.entry(slot)
        if entry is None:
            await interaction.response.send_message(
                f"Slot {slot} does not exist — see `/entry list`.", ephemeral=True)
            return
        known = self.app.content.get(car)
        if self.app.content.all_cars() and known is None:
            await interaction.response.send_message(
                f"Car `{car}` is not installed on the server (checked content/cars).",
                ephemeral=True)
            return
        skins = known.skins if known else []
        if skin:
            if skins and skin not in skins:
                await interaction.response.send_message(
                    f"`{car}` has no skin `{skin}`. Available: "
                    f"{', '.join(skins[:15]) or 'none'}", ephemeral=True)
                return
        else:
            # Keep the current skin when the new car has it; otherwise first skin.
            skin = entry.skin if entry.skin in skins else (skins[0] if skins else "")
        change = self.app.staging.set_entry_car(slot, car, skin or "")
        await self.bot.audit(interaction, f"entry {change}")
        await self._respond_staged(interaction, change)

    @app_commands.command(name="setskin", description="Change the skin of an entry slot")
    @app_commands.describe(slot="Entry slot", skin="Skin for that slot's car")
    @app_commands.autocomplete(slot=slot_autocomplete, skin=skin_for_slot_autocomplete)
    @admin_only()
    async def setskin(self, interaction: discord.Interaction, slot: int,
                      skin: str) -> None:
        entry = self.app.staging.entry(slot)
        if entry is None:
            await interaction.response.send_message(
                f"Slot {slot} does not exist — see `/entry list`.", ephemeral=True)
            return
        skins = self.app.content.skins_for(entry.model)
        if skins and skin not in skins:
            await interaction.response.send_message(
                f"`{entry.model}` has no skin `{skin}`. Available: {', '.join(skins[:15])}",
                ephemeral=True)
            return
        change = self.app.staging.set_entry_skin(slot, skin)
        await self.bot.audit(interaction, f"entry {change}")
        await self._respond_staged(interaction, change)
