# jinx-ai

**Jinx AI** is a Windows **multi-bot studio**: Team Members → Teams → Projects → Project Explorer (group chat + 1:1). Local agents that code, plus the tools already in this repo (files, shell, git, search, ComfyUI, llama-server).

Product spec (also loaded every chat turn): **[docs/JINX.md](docs/JINX.md)**. Overlay internals: [docs/body.md](docs/body.md).

Launch lands on **Home** (Projects and Teams). **New project** can pick the shared Computer folder, staff teams, and optionally Kickoff Chief. There is no Assistant/Developer chat-mode landing.

## Requirements

- Python 3.10+ (tested with 3.13)
- A GPU is recommended. **AI Models → Local** downloads llama-server; **Hugging Face Hub** pulls GGUFs.

## Install

```powershell
cd c:\Development\jinx-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

Tray: **Open Jinx**. Ctrl+C in the terminal stops the app. One process per `data/` folder.

Catalog: `data/platform/registry.json`. Official built-in team: `python scripts/init_arcane_team.py` (Arcane). Optional: `python scripts/init_marketplace_bots.py --overwrite`.

Brains, keys, connectors, and agent timeout live under **AI Models** and **Settings** (see JINX.md). Default OS home for models: `C:\Jinx-AI`.
