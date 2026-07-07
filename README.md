
A vibecoded discord bot so that I can try out claude fable. Probably easily adapted for other peoples' use case



# Assetto Corsa CM Discord Admin Bot

A Discord bot that administers an Assetto Corsa dedicated server (managed with
Content Manager) running on a Windows VM. Trusted users — gated by Discord
roles — can control the server without ever touching the VM:

- **Reboot** the AC server (`/server start|stop|restart`), with confirm buttons
  and a cooldown against restart spam
- **Switch Content Manager presets** (`/preset list|apply`) — presets are
  discovered from your CM install and never modified
- **Swap entry cars & skins** (`/entry setcar|setskin`) with autocomplete backed
  by the cars/skins actually installed on the server
- **Damage & collisions** (`/settings damage 0-100`, `/settings collisions`)
- **Time of day** (`/settings time HH:MM`)
- **Join links** (`/join`) — `acstuff.club` links that open Content Manager
- **Auto-updating status message** (`/status pin`) — who's online, track,
  session, join button; refreshes continuously
- **Local leaderboard** (`/lb top|me|recent|link`) — best clean laps per
  **driver + car + track**, fed live from the server's UDP plugin protocol and
  backfilled from session results files
- **Safeguards**: only members with configured roles can change anything, every
  admin action is audit-logged (file + optional channel), and the bot enforces
  a single AC server instance (it detects and can take over stray
  `acServer.exe`/`AssettoServer.exe` processes)

Supports two backends: the stock Kunos **`acServer.exe`** and
**[AssettoServer](https://assettoserver.org)**.

---

## How it works

```
CM preset folder (read-only)
   └─ /preset apply  →  data/active/          ← the bot's staged config
         └─ /entry, /settings edit the staged INI files (line-preserving)
               └─ /server start|restart  →  copied into the server's cfg dir → launch
```

Config edits are **staged** and take effect on restart — every change replies
with a "Restart server now" button. The bot pins the server's UDP plugin
(`UDP_PLUGIN_ADDRESS`/`UDP_PLUGIN_LOCAL_PORT`) to itself on every deploy, so
live data (players, laps) always flows regardless of what the preset says.

### Known limitations (by design of AC, not the bot)

- **Collisions can't be disabled on the vanilla server.** `/settings damage 0`
  makes contact harmless; true ghost-car mode needs the AssettoServer backend
  (see `assettoserver.collisions_yaml_key` in the config).
- **Client car setups never reach the server.** TC/ABS levels, tire pressures
  and alignment are client-side. Each lap instead stores a **policy snapshot**
  of what the server enforced (allowed assists, tire blankets, wear/fuel
  rates, damage %, temps, grip) plus the tyre compound when the session
  results file provides it. The DB has nullable columns for real setup data if
  a richer source (stracker/ptracker, AssettoServer plugin) is added later.
- **Vanilla time of day is 08:00–18:00** (sun-angle limit). Night/real-time
  needs CSP WeatherFX or AssettoServer.

---

## Setup (on the Windows VM)

### 1. Prerequisites

- Windows VM with **Assetto Corsa** (full install — skins come from
  `content\cars`), the **dedicated server** (`steamcmd` app 302550 or the
  `server` folder of the game install), and **Content Manager** (your server
  presets)
- **Python 3.11+** — [python.org](https://www.python.org/downloads/), tick
  *Add python.exe to PATH*
- Server ports forwarded on your router: TCP+UDP 9600, TCP 8081 (or whatever
  your presets use), or the `/join` link won't work from outside

### 2. Create the Discord application

1. <https://discord.com/developers/applications> → *New Application* → *Bot*
2. Copy the **bot token** (needed as an environment variable below)
3. *Installation* → guild install with the `bot` + `applications.commands`
   scopes; permissions: *Send Messages*, *Embed Links*, *Read Message History*
4. Invite it to your server
5. In Discord (Developer Mode on): right-click your server → *Copy Server ID*
   (`guild_id`), right-click the admin role → *Copy Role ID*, right-click the
   status channel → *Copy Channel ID*

### 3. Install the bot

```powershell
git clone <this repo> C:\acbot
cd C:\acbot
py -m venv .venv
.venv\Scripts\pip install .
copy config.example.yaml config.yaml
notepad config.yaml        # fill in ids + paths (comments explain everything)
setx ACBOT_DISCORD_TOKEN "your-bot-token"   # then open a NEW terminal
```

### 4. Validate & run

```powershell
.venv\Scripts\python -m acbot doctor   # checks paths, presets, ports, token
.venv\Scripts\python -m acbot run
```

`doctor` must be clean before `run` will start. It also prints the CM preset
folders it found — if auto-detection misses yours, set `paths.cm_presets_dir`
explicitly (each preset is a folder containing `server_cfg.ini` +
`entry_list.ini`; save presets from CM's *Server* tab).

In Discord: `/preset apply` → `/server start` → `/status pin` in your status
channel. Done.

### 5. Start automatically with the VM

First, decide whether the **AC server** should launch itself too, or wait for
`/server start`. To have it launch automatically (using whichever preset was
last applied — that choice persists in `data/state.json`/`data/active/`
across restarts), set in `config.yaml`:

```yaml
server:
  autostart: true
```

Run `/preset apply` at least once beforehand so there's something staged; if
autostart is on with nothing staged yet, the bot logs a warning and just sits
there waiting for `/preset apply`. If it finds a stray AC server already
running (e.g. survived a bot crash), it leaves it alone and logs that instead
of touching it — check `/server status`.

Then pick how the **bot process** itself starts with the VM:

**Option A — Startup folder (simplest).** Works when the VM automatically
logs into a desktop session on boot (common for a dedicated game-server VM).
Press <kbd>Win</kbd>+<kbd>R</kbd> → `shell:startup` → drop a shortcut to
[`start_acbot.bat`](start_acbot.bat) in the folder that opens. A console
window stays open showing the bot's logs; if it crashes on startup the window
pauses instead of vanishing so you can read the error.

**Option B — Task Scheduler (no auto-login needed).** Works even if nobody
ever logs into the VM's desktop:
*Task Scheduler* → *Create Task* → trigger **At startup**, check *Run whether
user is logged on or not*, action = start a program pointing at the full path
to `start_acbot.bat`. (No console window will be visible with this option —
watch `data\logs\acbot.log` instead.)

**Option C — Windows service via [NSSM](https://nssm.cc)** (most robust: also
auto-restarts the bot if it crashes):

```powershell
nssm install acbot C:\acbot\.venv\Scripts\python.exe -m acbot run
nssm set acbot AppDirectory C:\acbot
nssm set acbot AppEnvironmentExtra ACBOT_DISCORD_TOKEN=your-bot-token
nssm set acbot AppStdout C:\acbot\data\logs\service.log
nssm set acbot AppStderr C:\acbot\data\logs\service.log
nssm start acbot
```

---

## Commands

| Command | What it does | Who |
|---|---|---|
| `/server status` | State, uptime, preset, players, join link | everyone |
| `/server start [preset]` | Deploy staged config and launch | admin roles |
| `/server stop` / `restart` | With confirmation buttons | admin roles |
| `/preset list` | CM presets with track/cars/slots | everyone |
| `/preset apply <name>` | Stage a preset (restart to apply) | admin roles |
| `/entry list` | Slots with car, skin, current occupant | everyone |
| `/entry setcar <slot> <car> [skin]` | Swap a slot's car (validated) | admin roles |
| `/entry setskin <slot> <skin>` | Swap a slot's skin (validated) | admin roles |
| `/settings damage <0-100>` | Damage multiplier | admin roles |
| `/settings collisions <on\|off>` | AssettoServer toggle; vanilla explains | admin roles |
| `/settings time <HH:MM>` | Sun angle (vanilla) / live console (AS) | admin roles |
| `/join` | Content Manager join link | everyone |
| `/status pin` | (Re)create the auto-updating status message | admin roles |
| `/lb top <car> [track]` | Best clean lap per driver for the combo | everyone |
| `/lb me` | Your personal bests (after `/lb link`) | everyone |
| `/lb recent` | Latest recorded laps | everyone |
| `/lb link <steamid64>` | Link your Discord ↔ Steam GUID | everyone |

Leaderboard rules: only clean laps (0 cuts) count; drivers are identified by
Steam GUID, so changing entry slot or skin never splits or resets anyone's
times.

## AssettoServer backend

Set `server.backend: assettoserver` and `paths.assettoserver_dir`. The bot
manages `cfg\server_cfg.ini` / `entry_list.ini` the same way and starts
`AssettoServer.exe` instead. Two extras are wired through config because AS
option names change between versions — check your `cfg\extra_cfg.yml`:

```yaml
assettoserver:
  collisions_yaml_key: SomeCollisionOption    # true = collisions disabled
  settime_console_template: "/settime {hour:02d}:{minute:02d}"
```

## Development (any OS)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                      # 52 tests, no game needed
.venv/bin/ruff check .
python tools/replay_udp.py --target 127.0.0.1:12000   # fake a session against a running bot
```

`tools/replay_udp.py` fabricates ACSP traffic (joins, laps, session end) so you
can watch the status embed and `/lb` fill up without Assetto Corsa installed.

## Troubleshooting

- **Slash commands don't show up** — commands are registered guild-scoped at
  startup; check `discord.guild_id` and that the bot was invited with the
  `applications.commands` scope.
- **`/join` says public IP unknown** — set `server.public_ip` explicitly.
- **No laps recorded** — the bot pins the UDP plugin config at deploy time, so
  laps only flow for servers the bot itself started. Check
  `data/logs/acbot.log` and that nothing else occupies UDP 12000.
- **Presets missing** — run `acbot doctor`; set `paths.cm_presets_dir`.
- **Status message stopped updating** — it was probably deleted; run
  `/status pin` again.
