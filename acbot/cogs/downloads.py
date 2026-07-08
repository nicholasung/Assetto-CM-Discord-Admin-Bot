"""Download commands: /download cars | tracks | car <name> | track <name>"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from ..bot import ACBot


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


class DownloadsCog(commands.GroupCog, group_name="download",
                   group_description="Download AC content"):
    def __init__(self, bot: ACBot):
        self.bot = bot
        self.app = bot.app
        super().__init__()

    # -- autocomplete (must stay instant: reads the warmed cache only) --------

    async def _car_autocomplete(self, interaction: discord.Interaction,
                                current: str) -> list[app_commands.Choice]:
        cars = self.app.content.search(current, limit=20)
        return [app_commands.Choice(name=car.label[:100], value=car.car_id) for car in cars]

    async def _track_autocomplete(self, interaction: discord.Interaction,
                                  current: str) -> list[app_commands.Choice]:
        query = current.lower()
        matches = [t for t in self.app.content.all_tracks() if query in t.lower()]
        return [app_commands.Choice(name=t[:100], value=t) for t in matches[:20]]

    # -- lists ----------------------------------------------------------------

    @app_commands.command(name="cars", description="List all available cars")
    async def list_cars(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        await self.app.content.ensure_loaded()
        cars = self.app.content.all_cars()
        if not cars:
            await interaction.edit_original_response(content="No cars found.")
            return
        lines = [f"• `{car.car_id}` — {car.display_name}" for car in cars]
        await self._post_list(interaction, "Cars", "Car List", lines,
                              "Use `/download car <name>` to get a download link.")

    @app_commands.command(name="tracks", description="List all available tracks")
    async def list_tracks(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        await self.app.content.ensure_loaded()
        tracks = self.app.content.all_tracks()
        if not tracks:
            await interaction.edit_original_response(content="No tracks found.")
            return
        lines = [f"• `{track}`" for track in tracks]
        await self._post_list(interaction, "Tracks", "Track List", lines,
                              "Use `/download track <name>` to get a download link.")

    async def _post_list(self, interaction: discord.Interaction, label: str,
                         thread_name: str, lines: list[str], footer: str) -> None:
        """Keep the channel tidy: post a one-line header, then drop the full
        list into a thread (batched to stay under Discord's message limit)."""
        header = await interaction.edit_original_response(
            content=f"**Available {label} ({len(lines)})** — full list in the thread below 👇")
        try:
            thread = await header.create_thread(name=thread_name)
        except discord.HTTPException:
            # No thread permission / not a threadable channel — fall back inline.
            await header.edit(content=f"**Available {label} ({len(lines)})**")
            for batch in _batched(lines, 20):
                await interaction.followup.send("\n".join(batch))
            return
        for batch in _batched(lines, 20):
            await thread.send("\n".join(batch))
        await thread.send(footer)

    # -- download links -------------------------------------------------------

    @app_commands.command(name="car", description="Get download link for a car")
    @app_commands.autocomplete(name=_car_autocomplete)
    async def download_car(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(thinking=True)
        await self.app.content.ensure_loaded()
        car = self.app.content.get(name)
        if not car:
            await interaction.edit_original_response(
                content=f"Car `{name}` not found. Use `/download cars` to see available cars.")
            return
        if not self.app.public_ip:
            await interaction.edit_original_response(
                content="Download server not ready (no public IP).")
            return

        url = (f"http://{self.app.public_ip}:8082/get/cars/{quote(car.car_id)}"
               f"?name={quote(car.label)}")
        embed = discord.Embed(title=car.label, color=discord.Color.green())
        embed.add_field(name="Download", value=f"[Click here]({url})", inline=False)
        embed.set_footer(text="Extract to your Assetto Corsa content/cars folder")
        await interaction.edit_original_response(embed=embed)

    @app_commands.command(name="track", description="Get download link for a track")
    @app_commands.autocomplete(name=_track_autocomplete)
    async def download_track(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(thinking=True)
        await self.app.content.ensure_loaded()
        if name not in self.app.content.all_tracks():
            await interaction.edit_original_response(
                content=f"Track `{name}` not found. Use `/download tracks` to see available tracks.")
            return
        if not self.app.public_ip:
            await interaction.edit_original_response(
                content="Download server not ready (no public IP).")
            return

        url = (f"http://{self.app.public_ip}:8082/get/tracks/{quote(name)}"
               f"?name={quote(name)}")
        embed = discord.Embed(title=name, color=discord.Color.green())
        embed.add_field(name="Download", value=f"[Click here]({url})", inline=False)
        embed.set_footer(text="Extract to your Assetto Corsa content/tracks folder")
        await interaction.edit_original_response(embed=embed)
