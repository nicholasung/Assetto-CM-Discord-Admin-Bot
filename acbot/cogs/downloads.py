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
    
    async def _car_autocomplete(self, interaction: discord.Interaction,
                                current: str) -> list[app_commands.Choice]:
        """Autocomplete for car names."""
        cars = self.app.content.search(current, limit=20)
        return [app_commands.Choice(name=car.label, value=car.car_id) for car in cars]
    
    async def _track_autocomplete(self, interaction: discord.Interaction,
                                  current: str) -> list[app_commands.Choice]:
        """Autocomplete for track names (scan tracks folder)."""
        tracks_dir = self.app.cfg.paths.ac_root / "content" / "tracks"
        if not tracks_dir.is_dir():
            return []
        
        matches = []
        query = current.lower()
        for track_dir in sorted(tracks_dir.iterdir()):
            if track_dir.is_dir() and not track_dir.name.startswith("."):
                if query in track_dir.name.lower():
                    matches.append(app_commands.Choice(
                        name=track_dir.name,
                        value=track_dir.name
                    ))
        
        return matches[:20]
    
    @app_commands.command(name="cars", description="List all available cars")
    async def list_cars(self, interaction: discord.Interaction) -> None:
        cars = self.app.content.all_cars()
        if not cars:
            await interaction.response.send_message("No cars found.", ephemeral=True)
            return
        
        # Split into chunks of 20 cars per message (to avoid hitting message length limit)
        chunk_size = 20
        chunks = [cars[i:i + chunk_size] for i in range(0, len(cars), chunk_size)]
        
        # Send first message as response
        first_chunk = chunks[0]
        lines = ["**Available Cars:**\n"]
        for car in first_chunk:
            lines.append(f"• `{car.car_id}` — {car.display_name}")
        
        await interaction.response.send_message("\n".join(lines))
        
        # Send remaining chunks as follow-ups
        for chunk in chunks[1:]:
            lines = []
            for car in chunk:
                lines.append(f"• `{car.car_id}` — {car.display_name}")
            await interaction.followup.send("\n".join(lines))
        
        # Send final message with instruction
        await interaction.followup.send("Use `/download car <name>` to get a download link.")
    
    @app_commands.command(name="tracks", description="List all available tracks")
    async def list_tracks(self, interaction: discord.Interaction) -> None:
        tracks_dir = self.app.cfg.paths.ac_root / "content" / "tracks"
        if not tracks_dir.is_dir():
            await interaction.response.send_message("Tracks folder not found.", ephemeral=True)
            return
        
        # Scan tracks
        tracks = sorted([
            d.name for d in tracks_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])
        
        if not tracks:
            await interaction.response.send_message("No tracks found.", ephemeral=True)
            return
        
        # Split into chunks of 25 tracks per message
        chunk_size = 25
        chunks = [tracks[i:i + chunk_size] for i in range(0, len(tracks), chunk_size)]
        
        # Send first message as response
        first_chunk = chunks[0]
        lines = ["**Available Tracks:**\n"]
        for track in first_chunk:
            lines.append(f"• `{track}`")
        
        await interaction.response.send_message("\n".join(lines))
        
        # Send remaining chunks as follow-ups
        for chunk in chunks[1:]:
            lines = []
            for track in chunk:
                lines.append(f"• `{track}`")
            await interaction.followup.send("\n".join(lines))
        
        # Send final message with instruction
        await interaction.followup.send("Use `/download track <name>` to get a download link.")
    
    @app_commands.command(name="car", description="Get download link for a car")
    @app_commands.autocomplete(name=_car_autocomplete)
    async def download_car(self, interaction: discord.Interaction, name: str) -> None:
        car = self.app.content.get(name)
        if not car:
            await interaction.response.send_message(
                f"Car `{name}` not found. Use `/download cars` to see available cars.",
                ephemeral=True
            )
            return
        
        if not self.app.public_ip:
            await interaction.response.send_message(
                "Download server not ready (no public IP).",
                ephemeral=True
            )
            return
        
        # Build the download link
        url = f"http://{self.app.public_ip}:8082/downloads/cars/{car.car_id}"
        
        embed = discord.Embed(title=car.label, color=discord.Color.green())
        embed.add_field(name="Download", value=f"[Click here]({url})", inline=False)
        embed.set_footer(text="Extract to your Assetto Corsa content/cars folder")
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="track", description="Get download link for a track")
    @app_commands.autocomplete(name=_track_autocomplete)
    async def download_track(self, interaction: discord.Interaction, name: str) -> None:
        tracks_dir = self.app.cfg.paths.ac_root / "content" / "tracks"
        track_path = tracks_dir / name
        
        if not track_path.is_dir():
            await interaction.response.send_message(
                f"Track `{name}` not found. Use `/download tracks` to see available tracks.",
                ephemeral=True
            )
            return
        
        if not self.app.public_ip:
            await interaction.response.send_message(
                "Download server not ready (no public IP).",
                ephemeral=True
            )
            return
        
        # Build the download link
        url = f"http://{self.app.public_ip}:8082/downloads/tracks/{name}"
        
        embed = discord.Embed(title=name, color=discord.Color.green())
        embed.add_field(name="Download", value=f"[Click here]({url})", inline=False)
        embed.set_footer(text="Extract to your Assetto Corsa content/tracks folder")
        
        await interaction.response.send_message(embed=embed)
