# Lux Viewer

[Tiếng Việt](README-vi.md) | English

[![GitHub Release](https://img.shields.io/github/v/release/hoang-rio/lux-web-viewer)](https://github.com/hoang-rio/lux-web-viewer/releases) [![License](https://img.shields.io/github/license/hoang-rio/lux-web-viewer?color=blue)](LICENSE) [![Commit activity](https://img.shields.io/github/commit-activity/m/hoang-rio/lux-web-viewer)](https://github.com/hoang-rio/lux-web-viewer/commits/main/) [![GitHub last commit](https://img.shields.io/github/last-commit/hoang-rio/lux-web-viewer?color=blue)](https://github.com/hoang-rio/lux-web-viewer)

[![Docker publish action status](https://img.shields.io/github/actions/workflow/status/hoang-rio/lux-web-viewer/docker-publish.yml?label=docker%20publish%20action)](https://github.com/hoang-rio/lux-web-viewer/actions/workflows/docker-publish.yml) [![Docker Image Latest](https://ghcr-badge.egpl.dev/hoang-rio/lux-web-viewer/latest_tag?trim=major&label=latest%20image%20tag)](https://github.com/hoang-rio/lux-web-viewer/pkgs/container/lux-web-viewer) [![Docker Image Latest](https://ghcr-badge.egpl.dev/hoang-rio/lux-web-viewer/tags?trim=major)](https://github.com/hoang-rio/lux-web-viewer/pkgs/container/lux-web-viewer) [![Docker Image Size](https://ghcr-badge.egpl.dev/hoang-rio/lux-web-viewer/size)](https://github.com/hoang-rio/lux-web-viewer/pkgs/container/lux-web-viewer)

## About
<p align="center">
    <img src="misc/lux-viewer-logo.png" alt="Lux Viewer Logo"/>
</p>
A python script allow watch Luxpower SNA inverter in realtime. Tested in Luxpower SNA 6K with old wifi dongle (BAxxxxxx wifi name)

## Inverter setup
See wiki from `lxp-bridge` [here](https://github.com/celsworth/lxp-bridge/wiki/Inverter-Setup). _(You can ignore AC charge setup)_

## Configuration
* Copy `.env.example` to `.env`
* Update configuration in `.env` with your info

### Multi-tenant foundation (PostgreSQL)

This branch now includes a PostgreSQL + Alembic foundation for upcoming multi-user and multi-inverter support.

Required new `.env` variables:

* `POSTGRES_DB_URL` (example: `postgresql+psycopg://postgres:postgres@localhost:5432/lux_web_viewer`)
* `JWT_SECRET`, `JWT_ACCESS_EXPIRE_MINUTES`, `JWT_REFRESH_EXPIRE_DAYS`
* `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`, `SMTP_SSL`

Run initial migration:

* `alembic upgrade head`

### ReadInput Mode (DONGLE/SERVER)
You can control which inverter input frame is requested by setting `READ_INPUT_MODE` in `.env`:

* `READ_INPUT_MODE=INPUT1` (aliases: `INPUT1`, `READINPUT1`, `READ_INPUT1`): request only ReadInput1 (register `0`, count `40`)
* `READ_INPUT_MODE=ALL` : request ReadInput1 -> ReadInput4 sequentially (registers `0`, `40`, `80`, `120`)

`ALL` provides the most complete data set by combining ReadInput1-4, while `INPUT1` uses smaller payloads.

## Installation and run
* Sync gitsubmodule with `git submodule init && git submodule update`
* Python 3 required
* Setup python venv with `python -m venv venv`
* Activate python venv `source venv/Scripts/activate` on git-bash Windows or `source venv/bin/activate` on Unix/Linux
* Install dependencies with `pip install -r requirements.txt` or `./pip-binary-install.sh` on low-end device (example: OpenWrt router)
* Run application with `python app.py`
> If you can't install and run you can use docker method below

## Locking for docker? Here is step
* cd to `docker` folder
* run command `docker compose up -d` to run docker container

## Mobile Application
You can implement your own notification app for Android/iOS and save tokens to `devices.json` to receive push alerts when grid connection state changes.

The author also provides a **Lux App Viewer (Android/iOS)**. If you need it, feel free to contact for more support.

The built-in web server exposes mobile-friendly APIs:
* `POST /fcm/register` with JSON (or form body) `token=<firebase_device_token>` to register a device token
* `GET /mobile/state` to read current grid connection state and state-change history

## Web Viewer
* Build FE with command `cd web_viewer/fe_src && yarn install && yarn build` (Ignore this step if you run via docker)
* Now you can see LuxPower realtime web viewer in http://localhost:88 (or another port if you changed `PORT` in `.env`).
* HTTPS is also supported; enable it by setting `HTTPS_ENABLED=true` and providing `HTTPS_PORT`, `HTTPS_CERT_FILE`, and `HTTPS_KEY_FILE` in `.env`.

## Modbus Register Read/Write
The app can read and write inverter registers over the Modbus protocol through the existing dongle connection — no extra hardware or configuration is required. In SERVER mode Modbus requests are seamlessly interleaved with the normal ReadInput polling so real-time monitoring keeps running; in DONGLE mode requests go directly to the dongle.

### Features
* Read/write Modbus registers (fc 3/4 read, fc 6/16 write) via the dongle
* ~60 settings grouped into categories: Beep/Audio, Application, Charge, Discharge, Battery
* Admin dashboard (Settings → Modbus) to browse, read, and apply register values
* Reads are cached (default 30s, `MODBUS_READ_CACHE_TTL`) and invalidated on write; idempotent reads (fc 3/4) are retried once on timeout

### REST APIs
* `GET /modbus/status` — controller status
* `GET /modbus/registers` — register catalog grouped by category
* `GET /modbus/read?category=<key>` — read current values (omit `category` to read all)
* `POST /modbus/write` with JSON `{ "key": <register key>, "value": <new value> }` to write a register

Access requires a valid JWT and is scoped to the logged-in user's registered inverters.

<center>
<picture style="max-width: 800px">
    <source srcset="misc/screenshot-light.png" media="(prefers-color-scheme: light)"/>
    <source srcset="misc/screenshot-dark.png"  media="(prefers-color-scheme: dark)"/>
    <img src="misc/screenshot-light.png"/>
</picture>
</center>

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Third-party

Thanks to [@celsworth](https://github.com/celsworth) by awesome project [celsworth/lxp-packet](https://github.com/celsworth/lxp-packet) *(has been deleted)* and [celsworth/lxp-bridge](https://github.com/celsworth/lxp-bridge) (under MIT License)

This project includes code from `aiohttp` library (https://github.com/aio-libs/aiohttp.git) which is licensed under the Apache License 2.0.
