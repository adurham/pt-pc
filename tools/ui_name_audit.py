#!/usr/bin/env python3
"""Audit the port's UI animation lookups against the shipped layout records (local, asset-dependent).

Every Play/Find/TrackAlpha("UI_...") literal in the UI sources must name an animation record that exists
in one of the game's .uilb layouts. A missing one is the silent-failure class the option menu's set-in
hit: 1.0.5 asked for "UI_sys_opt_setin_s" -- a material file's name, not a record -- and the set-in never
played (only a warning in the log).

Reads the .uilb files of the extracted ui_default_data.fpk (tools/fpk.py --extract) and hashes names with
the fork's own StrCode64 (a tiny helper compiled from src/engine/core/strcode.cpp).

  python3 ui_name_audit.py                       # audit the fork's src/
  python3 ui_name_audit.py --source /tmp/old_src  # audit another tree (used to validate the checker:
                                                  # at the fix's parent it must flag UI_sys_opt_setin_s)

Exit code 0 when every name resolves, 1 when something is missing (or when validation fails).
Limits: names built at runtime (string tables, hashes) are out of reach of a static scan; the layouts
searched are all the ones in ui_default_data.fpk, not only the one a given call site uses.
"""
import argparse
import re
import struct
import subprocess
import sys
from pathlib import Path

MASK = (1 << 48) - 1


def parse_names(uilb: Path):
    d = uilb.read_bytes()
    if d[:4] != b"UILB":
        return None
    u16 = lambda o: struct.unpack_from("<H", d, o)[0]
    u32 = lambda o: struct.unpack_from("<I", d, o)[0]
    u64 = lambda o: struct.unpack_from("<Q", d, o)[0]
    names, data_size, base = u16(0x10), u32(0x24), u32(0x2C)
    return {u64(base + data_size + 8 * i) & MASK for i in range(names)}


def ensure_strcode(here: Path, repo: Path) -> Path:
    exe, src = here / "strcode", here / "strcode.cpp"
    if not src.exists():
        src.write_text(Path(repo / "src/engine/core/strcode.cpp").read_text())
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["clang++", "-std=c++20", "-O2", "-I", str(repo / "src"), str(src),
                        str(repo / "src/engine/core/strcode.cpp"), "-o", str(exe)], check=True)
    return exe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--ui", default=str(Path.home() / ".hermes/cache/scratch/pt-ui"))
    ap.add_argument("--source", default=None, help="source tree to scan (default: <repo>/src)")
    args = ap.parse_args()
    here, repo = Path(__file__).resolve().parent, Path(args.repo)
    source = Path(args.source) if args.source else repo / "src"

    layouts = {}
    for uilb in sorted(Path(args.ui).rglob("*.uilb")):
        names = parse_names(uilb)
        if names:
            layouts[uilb.stem] = names
    if not layouts:
        print(f"no .uilb layouts under {args.ui} (extract ui_default_data.fpk first)")
        return 1
    all_names = set().union(*layouts.values())

    call = re.compile(r'\b(?:Play|Find|TrackAlpha)\("(UI_[A-Za-z0-9_]+)"')
    sites = []
    if args.source:
        files = sorted(Path(args.source).rglob("*.cpp"))
    else:
        # only the UI animation callers: the option menu family and the main loop drive the UI players;
        # other Play/Find in the tree are the demo player's or JSON lookups and are not layout records
        files = sorted((repo / "src/game/ui").rglob("*.cpp")) + [repo / "src/main.cpp"]
    for path in files:
        for line_no, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for name in call.findall(line):
                fn = "Find" if "Find(" in line else "Play"
                sites.append((name, str(path.relative_to(source if args.source else repo)), line_no, fn))
    if not sites:
        print(f"no Play/Find/TrackAlpha literals under {source}")
        return 1

    exe = ensure_strcode(here, repo)
    unique = sorted({name for name, *_ in sites})
    out = subprocess.run([str(exe), *unique], capture_output=True, text=True, check=True).stdout
    code_of = {}
    for line in out.splitlines():
        code, _, name = line.partition("  ")
        code_of[name] = int(code, 16)

    missing = []
    for name, file, line_no, fn in sites:
        code = code_of[name]
        if code not in all_names:
            missing.append((name, file, line_no, fn))
        else:
            where = sum(1 for names in layouts.values() if code in names)
            print(f"ok    {name:36s} in {where:2d} layout(s)  ({file}:{line_no} {fn})")
    for name, file, line_no, fn in missing:
        print(f"MISS  {name:36s} in no layout      ({file}:{line_no} {fn})")
    print(f"\n{len(sites) - len(missing)}/{len(sites)} call sites resolve across {len(layouts)} layouts")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
