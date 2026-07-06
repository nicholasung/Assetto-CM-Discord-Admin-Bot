"""/lb top | me | recent | link — the local leaderboard."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..leaderboard.queries import (
    fmt_laptime,
    known_combos,
    personal_bests,
    recent_laps,
    top_for_combo,
)
from ..util import truncate

if TYPE_CHECKING:
    from ..bot import ACBot

MEDALS = ("🥇", "🥈", "🥉")
STEAMID64 = re.compile(r"^\d{17}$")


def _rank_icon(rank: int) -> str:
    return MEDALS[rank - 1] if rank <= 3 else f"`{rank:>2}.`"


def _track_value(track: str, layout: str) -> str:
    return f"{track}|{layout}"


def _parse_track_value(value: str) -> tuple[str, str]:
    if "|" in value:
        t, sep, layout = value.partition("|")
        return t, layout
    return value, ""


async def lb_car_autocomplete(interaction: discord.Interaction,
                              current: str) -> list[app_commands.Choice[str]]:
    bot: ACBot = interaction.client  # type: ignore[assignment]
    q = current.lower()
    seen: dict[str, None] = {}
    for _t, _l, car in await known_combos(bot.app.db):
        if car and (not q or q in car.lower()):
            seen.setdefault(car)
    if len(seen) < 25:  # pad with installed cars so new combos are reachable
        for c in bot.app.content.search(current, limit=25):
            seen.setdefault(c.car_id)
    return [app_commands.Choice(name=truncate(c), value=c) for c in list(seen)[:25]]


async def lb_track_autocomplete(interaction: discord.Interaction,
                                current: str) -> list[app_commands.Choice[str]]:
    bot: ACBot = interaction.client  # type: ignore[assignment]
    q = current.lower()
    seen: dict[str, str] = {}
    for track, layout, _car in await known_combos(bot.app.db):
        label = f"{track} ({layout})" if layout else track
        if not q or q in label.lower():
            seen.setdefault(_track_value(track, layout), label)
    return [app_commands.Choice(name=truncate(label), value=truncate(value))
            for value, label in list(seen.items())[:25]]


class LeaderboardCog(commands.GroupCog, group_name="lb",
                     group_description="Lap time leaderboard"):
    def __init__(self, bot: ACBot):
        self.bot = bot
        self.app = bot.app
        super().__init__()

    @app_commands.command(name="top", description="Best clean laps for a car (per driver)")
    @app_commands.describe(car="Car model", track="Track (defaults to the current one)")
    @app_commands.autocomplete(car=lb_car_autocomplete, track=lb_track_autocomplete)
    async def top(self, interaction: discord.Interaction, car: str,
                  track: str | None = None) -> None:
        await interaction.response.defer()
        if track:
            track_id, layout = _parse_track_value(track)
        else:
            try:
                track_id, layout = self.app.staging.track()
            except Exception:
                track_id, layout = "", ""
            if not track_id:
                await interaction.followup.send(
                    "No track staged — pass the `track` option.", ephemeral=True)
                return
        rows = await top_for_combo(self.app.db, track_id, layout, car)
        track_label = f"{track_id} ({layout})" if layout else track_id
        if not rows:
            await interaction.followup.send(
                f"No clean laps recorded yet for **{car}** @ **{track_label}**.")
            return
        lines = []
        for r in rows:
            extra = [x for x in (r.skin or None, r.tyre) if x]
            suffix = f" · {', '.join(extra)}" if extra else ""
            lines.append(
                f"{_rank_icon(r.rank)} **{fmt_laptime(r.laptime_ms)}** — "
                f"{r.driver_name}{suffix} ({r.lap_count} laps)"
            )
        embed = discord.Embed(
            title=f"🏁 {car} @ {track_label}",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Clean laps only (0 cuts) · skins/slots don't split drivers")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="me", description="Your personal bests")
    async def me(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guid = await self.app.db.guid_for_discord(interaction.user.id)
        if guid is None:
            await interaction.followup.send(
                "Your Discord isn't linked to a Steam GUID yet. Use "
                "`/lb link <steam_guid>` (your SteamID64 — 17 digits, shown in "
                "Content Manager → Settings → Content Manager → General, or steamid.io).",
                ephemeral=True)
            return
        rows = await personal_bests(self.app.db, guid)
        if not rows:
            await interaction.followup.send("No clean laps recorded for you yet — go drive!",
                                            ephemeral=True)
            return
        name = await self.app.db.driver_name(guid)
        lines = [
            f"**{fmt_laptime(r.laptime_ms)}** — {r.car_model} @ "
            f"{r.track}{f' ({r.layout})' if r.layout else ''} · {r.lap_count} laps"
            for r in rows
        ]
        embed = discord.Embed(
            title=f"Personal bests — {name}",
            description="\n".join(lines)[:4000],
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="recent", description="Recently recorded laps")
    async def recent(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        rows = await recent_laps(self.app.db)
        if not rows:
            await interaction.followup.send("No laps recorded yet.")
            return
        lines = [
            f"{'✅' if r['cuts'] == 0 else '⚠️'} **{fmt_laptime(r['laptime_ms'])}** — "
            f"{r['name']} · {r['car_model']} @ {r['track']}"
            + (f" ({r['layout']})" if r["layout"] else "")
            for r in rows
        ]
        embed = discord.Embed(title="Recent laps", description="\n".join(lines)[:4000],
                              color=discord.Color.blurple())
        embed.set_footer(text="⚠️ = lap had cuts (not leaderboard-eligible)")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="link", description="Link your Discord to your Steam GUID for /lb me")
    @app_commands.describe(steam_guid="Your SteamID64 (17 digits)")
    async def link(self, interaction: discord.Interaction, steam_guid: str) -> None:
        steam_guid = steam_guid.strip()
        if not STEAMID64.match(steam_guid):
            await interaction.response.send_message(
                "That doesn't look like a SteamID64 (expected 17 digits, e.g. "
                "7656119…). Find yours at steamid.io or in Content Manager.",
                ephemeral=True)
            return
        await self.app.db.link_discord(steam_guid, interaction.user.id)
        await interaction.response.send_message(
            f"🔗 Linked {interaction.user.mention} ↔ `{steam_guid}`. "
            "`/lb me` will show your times.", ephemeral=True)
