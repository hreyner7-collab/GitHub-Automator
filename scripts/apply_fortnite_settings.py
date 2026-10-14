"""Apply the guide's in-game Fortnite settings by editing GameUserSettings.ini.

Only touches values whose meaning is unambiguous. Backs up the original first.
Run while Fortnite is NOT running (the game overwrites this file on exit).
"""
import shutil
import sys
from pathlib import Path

INI = Path.home() / "AppData/Local/FortniteGame/Saved/Config/WindowsClient/GameUserSettings.ini"

# key -> desired value (Lecctron 2026 guide)
TARGETS = {
    "bUseVSync": "False",                    # V-Sync off
    "PreferredFullscreenMode": "0",          # 0 = exclusive fullscreen (latency)
    "LastConfirmedFullscreenMode": "0",
    "FrameRateLimit": "144.000000",          # cap = monitor refresh (144 Hz)
    "FrontendFrameRateLimit": "144",
    "bUseDynamicResolution": "False",        # Dynamic 3D res off
    "sg.ViewDistanceQuality": "3",           # Epic view distance
    "sg.TextureQuality": "0",                # Textures low
    "sg.ShadowQuality": "0",
    "sg.AntiAliasingQuality": "0",
    "bEnableIncompatibleGpuTextureStreamingCheckBox": None,  # (not present - skip)
    "LowInputLatencyModeIsEnabled": "True",  # keep Reflex-style low latency on
}

REPLAYS = ("bRecordReplays", "bSaveReplays")  # any replay keys -> False

def main():
    if not INI.exists():
        sys.exit(f"Config not found: {INI}")
    # Safety: don't edit while the game can overwrite us on exit
    import subprocess
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FortniteClient-Win64-Shipping.exe"],
                         capture_output=True, text=True).stdout
    if "FortniteClient" in out:
        sys.exit("Fortnite is RUNNING - close it first, then re-run this script.")

    backup = INI.with_suffix(".ini.pre-tweaks.bak")
    if not backup.exists():
        shutil.copy2(INI, backup)
        print(f"Backup: {backup}")

    lines = INI.read_text(encoding="utf-8", errors="replace").splitlines()
    seen = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith((";", "#")):
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in TARGETS and TARGETS[key] is not None:
            lines[i] = f"{key}={TARGETS[key]}"
            seen.add(key)
        elif key in REPLAYS:
            lines[i] = f"{key}=False"
            seen.add(key)

    # Append any targets missing from the file into [ScalabilityGroups]/main section
    missing = [k for k, v in TARGETS.items() if v is not None and k not in seen]
    if missing:
        lines += [""] + [f"{k}={TARGETS[k]}" for k in missing]

    INI.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Updated {len(seen)} existing keys, appended {len(missing)}: {INI}")

if __name__ == "__main__":
    main()
