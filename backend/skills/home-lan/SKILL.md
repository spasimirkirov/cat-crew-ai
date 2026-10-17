---
name: home-lan
description: Local-network devices (Tapo bulbs, cameras, TV) are not wired yet — research next.
---

# Home LAN

There are **no** device-control tools yet. Do not pretend you can toggle Tapo bulbs,
view the camera, or change the TV. Say that clearly and offer to research next.

Next research (do not invent APIs until that pass):

- TP-Link Tapo lights — `python-kasa` / `tapo` Python libs, LAN credentials.
- Camera — ONVIF / RTSP stills, never stream secrets into chat.
- TV — brand protocol (Samsung / LG / Android TV ADB), confirm model first.
- Prefer one Home Assistant hub later over N vendor SDKs if many devices appear.

Until those tools exist, you can still search the web for manuals and open vendor apps
with `open_program` if the user has them installed.
