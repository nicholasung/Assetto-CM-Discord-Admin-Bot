"""Download commands: /download cars | tracks | car <name> | track <name>"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from ..bot import ACBot

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

        # 20 cars per message (message length limit); overflow goes in a thread.
        chunk_size = 20
        chunks = [cars[i:i + chunk_size] for i in range(0, len(cars), chunk_size)]
        lines = ["**Available Cars:**\n"]
        lines += [f"• `{car.car_id}` — {car.display_name}" for car in chunks[0]]
        msg = await interaction.edit_original_response(content="\n".join(lines))

        if len(chunks) > 1:
            thread = await msg.create_thread(name="Car List")
            for chunk in chunks[1:]:
                await thread.send("\n".join(
                    f"• `{car.car_id}` — {car.display_name}" for car in chunk))
            await thread.send("Use `/download car <name>` to get a download link.")

    @app_commands.command(name="tracks", description="List all available tracks")
    async def list_tracks(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        await self.app.content.ensure_loaded()
        tracks = self.app.content.all_tracks()
        if not tracks:
            await interaction.edit_original_response(content="No tracks found.")
            return

        chunk_size = 25
        chunks = [tracks[i:i + chunk_size] for i in range(0, len(tracks), chunk_size)]
        lines = ["**Available Tracks:**\n"]
        lines += [f"• `{track}`" for track in chunks[0]]
        msg = await interaction.edit_original_response(content="\n".join(lines))

        if len(chunks) > 1:
            thread = await msg.create_thread(name="Track List")
            for chunk in chunks[1:]:
                await thread.send("\n".join(f"• `{track}`" for track in chunk))
            await thread.send("Use `/download track <name>` to get a download link.")

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

        url = f"http://{self.app.public_ip}:8082/downloads/cars/{car.car_id}"
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

        url = f"http://{self.app.public_ip}:8082/downloads/tracks/{name}"
        embed = discord.Embed(title=name, color=discord.Color.green())
        embed.add_field(name="Download", value=f"[Click here]({url})", inline=False)
        embed.set_footer(text="Extract to your Assetto Corsa content/tracks folder")
        await interaction.edit_original_response(embed=embed)
