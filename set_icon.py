#!/usr/bin/env python3
"""Pick any image and set it as the TokenTracker.app icon."""

import os
import sys
import subprocess
import shutil
from pathlib import Path

APP = Path(__file__).parent / "TokenTracker.app"
ICONSET = Path("/tmp/TokenTracker.iconset")
ICNS_DST = APP / "Contents/Resources/AppIcon.icns"

def pick_file():
    result = subprocess.run([
        "osascript", "-e",
        'tell application "Finder" to set f to choose file with prompt '
        '"Pick an image for Token Tracker:" of type {"public.image"}\n'
        'return POSIX path of f'
    ], capture_output=True, text=True)
    path = result.stdout.strip()
    if not path:
        print("No file chosen.")
        sys.exit(0)
    return Path(path)

def make_icns(src: Path):
    ICONSET.mkdir(exist_ok=True)
    sizes = [16, 32, 64, 128, 256, 512]
    for s in sizes:
        subprocess.run(["sips", "-z", str(s), str(s), str(src),
                        "--out", str(ICONSET / f"icon_{s}x{s}.png")],
                       capture_output=True)
        subprocess.run(["sips", "-z", str(s*2), str(s*2), str(src),
                        "--out", str(ICONSET / f"icon_{s}x{s}@2x.png")],
                       capture_output=True)
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS_DST)])
    shutil.rmtree(ICONSET, ignore_errors=True)

def refresh():
    subprocess.run([
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
        "LaunchServices.framework/Support/lsregister",
        "-f", str(APP)
    ], capture_output=True)
    # Touch the app so Finder reloads the icon
    subprocess.run(["touch", str(APP)])
    # Restart Dock so the pinned icon updates immediately
    subprocess.run(["killall", "Dock"])

if __name__ == "__main__":
    print("Opening file picker...")
    img = pick_file()
    print(f"Converting {img.name} → AppIcon.icns ...")
    make_icns(img)
    print("Refreshing icon cache...")
    refresh()
    print("✓ Done! Your new icon will appear in a moment.")
    subprocess.run(["osascript", "-e",
        'display notification "Icon updated!" with title "Token Tracker"'])
