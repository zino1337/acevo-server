# Assetto Corsa EVO Dedicated Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/zino1337/acevo-server/blob/main/LICENSE)
[![CI](https://github.com/zino1337/acevo-server/actions/workflows/ci.yml/badge.svg)](https://github.com/zino1337/acevo-server/actions/workflows/ci.yml)
[![Docker Pulls](https://img.shields.io/docker/pulls/zino1337/acevo-server?cacheSeconds=3600)](https://hub.docker.com/r/zino1337/acevo-server)
[![Docker Image Size](https://img.shields.io/docker/image-size/zino1337/acevo-server/latest?cacheSeconds=3600)](https://hub.docker.com/r/zino1337/acevo-server)
[![GitHub Repository](https://img.shields.io/badge/GitHub-Repository-181717?logo=github)](https://github.com/zino1337/acevo-server)

Highly customizable Assetto Corsa Evo Dedicated Server running on Linux via Proton.<br/>
Configure server and event settings via environment variables, web dashboard or server_launcher.json.<br/>
Copy `.env.example` to `.env` and start with Docker Compose.<br/>
Not affiliated with Kunos Simulazioni or Assetto Corsa.

This Docker Hub README is shortened. Full documentation is available on GitHub.
Full README: https://github.com/zino1337/acevo-server

## Features

- Web dashboard to configure and control the server, including a live driver overview
- Mod support
- Environment variables or server_launcher.json for easy server configuration
- SteamCMD auto-update
- Practice and Race Weekend support
- Linux server via Proton
- Headless deployment by default - no GPU needed
- Host user/group mapping for volumes

## Requirements

- Docker and Docker Compose
- Steam account owning [Assetto Corsa EVO](https://store.steampowered.com/app/3058630/Assetto_Corsa_EVO/)
- Recommended: 2 CPU cores and 4 GiB RAM or more

## Quickstart

```bash
git clone https://github.com/zino1337/acevo-server.git
cd acevo-server
cp .env.example .env
```

Set `STEAM_USERNAME` to your Steam account name (not your email).<br/>
Set `STEAM_PASSWORD` before first start.<br/>

First login may fail when Steam Guard sends a code.<br/>
Put the code into `STEAM_AUTH_CODE` and run `docker compose up -d` again.

```bash
docker compose up -d
```

## Volumes

The Steam volume keeps SteamCMD login state so Steam Guard is not required on every restart of the server.

| Volume / Path                | Description                                                                 |
| ---------------------------- | --------------------------------------------------------------------------- |
| `/data`                      | Persistent server data, saves, and generated config files                   |
| `/data/mods`                 | Persistent `.kspkg` car mods                                                |
| `/root/.local/share/Steam`   | SteamCMD cache and login state                                              |
| `/data/server_launcher.json` | Optional official Windows launcher config loaded via `SERVER_LAUNCHER_JSON` |

Environment variables override values from `server_launcher.json`.

## Ports

`SERVER_TCP_PORT` and `SERVER_UDP_PORT` can be changed, but both must use the same port value or clients cannot connect.

| Port   | Protocol | Default | Purpose           |
| ------ | -------- | ------- | ----------------- |
| `9700` | TCP      | yes     | Server TCP port   |
| `9700` | UDP      | yes     | Server UDP port   |
| `8080` | TCP      | yes     | HTTP/listing port |
| `8090` | TCP      | yes     | Web dashboard     |

## Web Dashboard

Open **`http://<host>:8090`** in a browser (change the port with `DASHBOARD_PORT`). Protect it by setting `DASHBOARD_USER` / `DASHBOARD_PASSWORD` (an empty password makes it public).

From the dashboard you can:

- Configure everything visually - server name, ports, passwords, cars, track, weather etc.
- Install and remove `.kspkg` car mods from the **Mods** tab
- Start/Stop/Restart the server
- Watch live logs

> **Windows:** start the stack with `docker compose -f docker-compose.winvol.yml up -d` instead.

## Mod Support

AC EVO supports modded cars in multiplayer. Every driver needs exactly the same mod file as the server; otherwise, AC
EVO rejects the connection.

1. Open **Mods**, upload a `.kspkg` file, and click **Install**. Car variants are detected automatically.
2. Open the **Configuration** tab, select the newly added car, and click **Save & Apply**.
3. Every driver must install the exact same `.kspkg` locally at `%USERPROFILE%\Saved Games\ACE\mods` before joining.

If an upload is interrupted, select the same file again within 24 hours and click **Install** to resume it.

Without the dashboard, stop the server and copy `.kspkg` files to `./volumes/data/mods` (`/data/mods` inside the
container).

Only `.kspkg` car mods are managed. Additional SDK JSON files are not required and remain untouched in the volume. Cars
and variants are identified by their mechanical preset IDs inside the package; invalid packages and conflicting IDs
are not added to the car selector. An explicit `EVENT_CARS=all` also includes all conflict-free installed mods.

## Docker Compose Examples

```bash
# Practice server
docker compose up -d

# Race Weekend server
docker compose -f docker-compose-race.yml up -d
```

## Environment Variables

This section is shortened on Docker Hub. See the full table in the GitHub README: https://github.com/zino1337/acevo-server#environment-variables

| Name                                   | Default                        | Description                                        |
| -------------------------------------- | ------------------------------ | -------------------------------------------------- |
| `STEAM_USERNAME`                       | empty                          | Steam account name, not email.                     |
| `STEAM_PASSWORD`                       | empty                          | Steam account password for SteamCMD.               |
| `STEAM_AUTH_CODE`                      | empty                          | Steam Guard auth code for the next login.          |
| `SERVER_LAUNCHER_JSON`                 | `/data/server_launcher.json`   | Optional official tool config loaded as base.      |
| `SERVER_NAME`                          | `AC EVO Nordschleife Trackday` | Public server name.                                |
| `SERVER_TCP_PORT`                      | `9700`                         | TCP listener port. Must match `SERVER_UDP_PORT`.   |
| `SERVER_UDP_PORT`                      | `9700`                         | UDP listener port. Must match `SERVER_TCP_PORT`.   |
| `SERVER_HTTP_PORT`                     | `8080`                         | HTTP/listing port.                                 |
| `SERVER_MAX_PLAYERS`                   | `20`                           | Maximum player slots; downscaled to track max.     |
| `SERVER_TYPE`                          | `Ranked`                       | `Ranked` or `Unranked`.                            |
| `SERVER_TUNING_TYPE`                   | `TuningAllowed`                | `TuningAllowed` or `TuningDenied`.                 |
| `SERVER_ENTRY_LIST_URL`                | empty                          | Remote entry list URL.                             |
| `SERVER_ENTRY_LIST_PATH`               | empty                          | Local entry list JSON path.                        |
| `SERVER_RESULTS_POST_URL`              | empty                          | Result POST endpoint; put auth in URL query.       |
| `SERVER_RESULTS_PATH`                  | empty                          | Local results save folder.                         |
| `EVENT_TYPE`                           | `Practice`                     | `Practice` or `Race_Weekend`.                      |
| `EVENT_TRACK`                          | `Nurburgring_Touristenfahrten` | Track token.                                       |
| `EVENT_CARS`                           | `all`                          | Comma-separated car names/substrings, or `all`.    |
| `EVENT_CAR_CATEGORY`                   | `all`                          | Car filters such as `Road`, `Track`, or `EV`.      |
| `EVENT_BAN_CARS`                       | empty                          | Comma-separated car names/substrings to remove.    |
| `EVENT_BAN_CAR_CATEGORY`               | empty                          | Comma-separated category filters to remove.        |
| `PRACTICE_DURATION_MINUTES`            | `180`                          | Practice duration in minutes.                      |
| `QUALIFY_DURATION_MINUTES`             | `10`                           | Qualify duration in minutes for race weekends.     |
| `WARMUP_DURATION_MINUTES`              | `5`                            | Warmup duration in minutes for race weekends.      |
| `RACE_DURATION_MINUTES`                | `25`                           | Race duration in minutes when type is `Time`.      |
| `RACE_DURATION_LAPS`                   | `10`                           | Race duration in laps when type is `Laps`.         |
| `RACE_DURATION_TYPE`                   | `Time`                         | Race duration mode: `Time` or `Laps`.              |
| `RACE_MIN_WAITING_FOR_PLAYERS_SECONDS` | `10`                           | The minimum wait to start the race.                |
| `RACE_MAX_WAITING_FOR_PLAYERS_SECONDS` | `60`                           | The maximum wait to start the race.                |
| `AUTO_UPDATE`                          | `true`                         | Container-start update; server restarts skip it.   |
| `AUTO_START_SERVER`                    | `true`                         | Start the server automatically with the container. |
| `ACEVO_FORCE_SOFTWARE_RENDERING`       | `true`                         | Enables default no-GPU host compatibility.         |
| `DASHBOARD_PORT`                       | `8090`                         | Web dashboard port.                                |
| `DASHBOARD_USER`                       | `admin`                        | Web dashboard Basic Auth username.                 |
| `DASHBOARD_PASSWORD`                   | empty                          | Web dashboard Basic Auth password; empty = public. |

## Car Categories

This section is shortened on Docker Hub. See the full table in the GitHub README: https://github.com/zino1337/acevo-server#car-categories

Use `EVENT_CAR_CATEGORY` with `all`, type, era, or engine categories.<br/>
You can set multiple car categories separated by commas, like `EVENT_CAR_CATEGORY=Road,Track`.

Examples: `all`, `Road`, `Track`, `Modern`, `Vintage`, `ICE`, `EV`, `Hybrid`.

## Cars

This section is shortened on Docker Hub. See the full table in the GitHub README: https://github.com/zino1337/acevo-server#cars

Use `EVENT_CARS` with `all` or comma-separated car names/substrings.<br/>
Example: `EVENT_CARS=Abarth_695_Biposto,Caterham_Academy`.

## Tracks

This section is shortened on Docker Hub. See the full table in the GitHub README: https://github.com/zino1337/acevo-server#tracks

Use `EVENT_TRACK` with an underscore token.<br/>
Example: `EVENT_TRACK=Nurburgring_Touristenfahrten`.
