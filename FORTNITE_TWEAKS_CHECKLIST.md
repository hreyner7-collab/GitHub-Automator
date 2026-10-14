# Fortnite Optimization — FINAL STATUS

Based on Lecctron's "The Only Fortnite Optimization Guide You Will Ever Need in 2026".
Machine: ASUS laptop · i7-11370H (4C/8T) · RTX 3050 Ti Laptop (4 GB) · 40 GB RAM · Win 11 Home · 144 Hz 1080p.

## ✅ Windows & registry — done & verified

| Tweak | Value | Status |
|---|---|---|
| System restore point | "Before Fortnite optimization tweaks" | ✅ created first |
| Game Mode | On | ✅ |
| Optimizations for windowed games | On (`SwapEffectUpgradeEnable=1`) | ✅ |
| Visual effects | Best performance, font smoothing kept | ✅ |
| MMCSS Games task | Scheduling Category = High | ✅ |
| SystemResponsiveness | 10 | ✅ |
| NetworkThrottlingIndex | **0xFFFFFFFF** (throttling disabled) | ✅ |
| Win32PrioritySeparation | 0x26 (38) | ✅ |
| HAGS | On (`HwSchMode=2`) — needs reboot | ✅ set |
| Memory Integrity | **Off** — needs reboot | ✅ set |
| Ultimate Performance plan | Unlocked + active | ✅ |
| Winutil debloat GUI | Launched elevated (you choose what to apply) | ✅ |

## ✅ NVIDIA driver (global profile) — done & verified via driver export

| Setting | Value |
|---|---|
| Shader Cache Size | **Unlimited** (0xFFFFFFFF — the driver's max) |
| Vertical Sync | **Force off** |
| Power Management Mode | **Prefer maximum performance** |
| Texture Filtering - Quality | **High performance** |

Skipped "OpenGL rendering GPU": it's an Optimus app-routing control, irrelevant for
Fortnite (DX12), and its ID isn't confirmed in this driver's database — not guessing.

## ✅ In-game Fortnite settings — written to GameUserSettings.ini (backup kept)

Full screen · 1920×1080 · V-Sync off · FPS cap 144 (matches monitor) · Dynamic res off ·
View Distance Epic · Textures Low · Meshes Low (Performance Mode) · Low Latency on ·
Replays off. Applied with [scripts/apply_fortnite_settings.py](scripts/apply_fortnite_settings.py).

## ✅ BIOS items — already satisfied, no BIOS visit needed

- **XMP/DOCP**: RAM already runs at rated 3200 MT/s ✅
- **Resizable BAR / Above 4G**: BAR1 = full 4096 MB ✅
- **SMT**: keep ON — 4-core CPU, guide agrees ✅

## 🖐️ The only two things left for your hands

1. **Reboot** — activates HAGS + Memory Integrity-off.
2. **Two toggles only Fortnite's own UI can set** (config file is cloud-synced/overwritten):
   - Settings → Graphics → Rendering Mode → **Performance Mode (New)** — enables the DX12 draw-batch mode the guide wants
   - Settings → Graphics → NVIDIA Reflex Low Latency → **On + Boost** if thermals allow (low-latency framework is already on; Boost needs the UI)

## Rollback

- Restore point: `rstrui.exe` → "Before Fortnite optimization tweaks"
- ASUS Turbo power plan: `powercfg /setactive 6fecc5ae-f350-48a5-b669-b472cb895ccf`
- NVIDIA: import the backup `scripts/npi/CustomProfiles_2026-09-30_15-12-22.nip` (pre-tweak state) via Profile Inspector
- Fortnite config: `GameUserSettings.ini.pre-tweaks.bak` in the config folder
