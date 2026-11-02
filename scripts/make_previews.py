"""Make same-size JPEG review copies of a render folder, and group them into review sessions.

A Codex session keeps every image it has viewed and re-sends all of them with each
request; the relay rejects requests whose inline images exceed 32 MiB (lessons R007).
Rendered PNGs are 3-8 MiB each, so about ten originals overflow one session. A
quality-90 JPEG at the original pixel size is 1/7 to 1/11 of the PNG and keeps
fingers and small text readable. The originals are never touched; deliver those.

Writes into <render folder>/other/:
  检查副本/<image stem>.jpg   one copy per image, same width and height
  previews.json               each copy's size and dimensions, plus review sessions:
                              whole sets (第01套-...) packed so that one session's
                              copies stay under --budget-mb (default 12 MiB, leaving
                              room for zoomed crops and base64 overhead)

Encoding uses the Windows imaging codecs through PowerShell, so no Pillow is needed.

Usage:
  python make_previews.py D:/素材/圣诞/出图-20261102_150000      # the folder holding the images
  python make_previews.py <render folder> --budget-mb 8 --quality 85
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
SET_RE = re.compile(r"^(第\d+套)-")

# Reads the job list from a UTF-8 JSON file so Chinese paths never pass through the
# command line or the console code page. Prints one ASCII line per image.
PS_ENCODER = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
Add-Type -AssemblyName PresentationCore
$jobs = (Get-Content -LiteralPath $env:PREVIEW_JOBS -Raw -Encoding UTF8 | ConvertFrom-Json)
$q = [int]$jobs.quality
$i = 0
foreach ($item in $jobs.items) {
    try {
        $stream = [IO.File]::OpenRead($item.src)
        try {
            $frame = [Windows.Media.Imaging.BitmapDecoder]::Create($stream, 'None', 'OnLoad').Frames[0]
            $rgb = New-Object Windows.Media.Imaging.FormatConvertedBitmap $frame, ([Windows.Media.PixelFormats]::Bgr24), $null, 0
            $enc = New-Object Windows.Media.Imaging.JpegBitmapEncoder
            $enc.QualityLevel = $q
            $enc.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($rgb))
            $fs = [IO.File]::Create($item.dst)
            try { $enc.Save($fs) } finally { $fs.Close() }
            Write-Output ("{0}`tok`t{1}`t{2}" -f $i, $rgb.PixelWidth, $rgb.PixelHeight)
        } finally { $stream.Close() }
    } catch {
        Write-Output ("{0}`tfail`t{1}" -f $i, ($_.Exception.Message -replace "[`r`n`t]", " "))
    }
    $i++
}
"""


def mib(n):
    return round(n / 1048576, 2)


def encode(pairs, quality):
    """pairs: [(src, dst)]. Returns one result dict per pair, in order."""
    with tempfile.TemporaryDirectory() as tmp:
        jobs_file = Path(tmp) / "jobs.json"
        jobs_file.write_text(json.dumps({"quality": quality, "items": [
            {"src": str(s), "dst": str(d)} for s, d in pairs]}, ensure_ascii=False), encoding="utf-8")
        cmd = base64.b64encode(PS_ENCODER.encode("utf-16-le")).decode("ascii")
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", cmd],
                              capture_output=True, env={**os.environ, "PREVIEW_JOBS": str(jobs_file)})
    results = [{"error": "no result from PowerShell"} for _ in pairs]
    for line in proc.stdout.decode("utf-8", "replace").lstrip("\ufeff").splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 3 or not parts[0].isdigit() or int(parts[0]) >= len(pairs):
            continue
        if parts[1] == "ok" and len(parts) == 4:
            results[int(parts[0])] = {"width": int(parts[2]), "height": int(parts[3])}
        else:
            results[int(parts[0])] = {"error": parts[2]}
    if proc.returncode != 0 and all("error" in r for r in results):
        sys.exit("PowerShell encoder failed:\n" + proc.stderr.decode("utf-8", "replace")[-800:])
    return results


def sessions(entries, budget):
    """Pack whole sets in name order; a set larger than the budget gets a session of its own."""
    units = {}
    for e in entries:
        m = SET_RE.match(e["image"])
        units.setdefault(m.group(1) if m else e["image"], []).append(e)
    out, cur, size = [], [], 0
    for key, items in units.items():
        s = sum(e["copy_mib"] for e in items)
        if cur and size + s > budget:
            out.append((cur, size))
            cur, size = [], 0
        cur.append((key, items))
        size += s
    if cur:
        out.append((cur, size))
    return [{"session": n, "sets": [k for k, _ in units_], "images": [e["image"] for _, items in units_ for e in items],
             "views": [e["view"] for _, items in units_ for e in items], "copy_mib": round(size, 2)}
            for n, (units_, size) in enumerate(out, 1)]


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # Windows consoles default to GBK; file names are Chinese
    p = argparse.ArgumentParser(description="Make JPEG review copies of a render folder")
    p.add_argument("folder", help="render folder (the one holding the images and other/)")
    p.add_argument("--quality", type=int, default=90, help="JPEG quality 1-100 (default 90)")
    p.add_argument("--budget-mb", type=float, default=12, help="max copy MiB per review session (default 12)")
    args = p.parse_args()

    folder = Path(args.folder).resolve()
    images = sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXT) if folder.is_dir() else []
    if not images:
        sys.exit(f"no images in {folder}")
    stems = [f.stem for f in images]
    if len(set(stems)) != len(stems):
        sys.exit("two images share a name with different extensions; their copies would collide")
    out_dir = folder / "other" / "检查副本"
    out_dir.mkdir(parents=True, exist_ok=True)

    pairs = [(f, out_dir / (f.stem + ".jpg")) for f in images]
    entries = []
    for (src, dst), r in zip(pairs, encode(pairs, args.quality)):
        e = {"image": src.name, "original_mib": mib(src.stat().st_size)}
        if "error" in r:
            e.update({"copy": None, "copy_mib": e["original_mib"], "view": src.name, "error": r["error"]})
        else:
            e.update({"copy": f"other/检查副本/{dst.name}", "copy_mib": mib(dst.stat().st_size),
                      "view": f"other/检查副本/{dst.name}", "dimensions": [r["width"], r["height"]]})
        entries.append(e)
    groups = sessions(entries, args.budget_mb)
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "folder": str(folder),
                "quality": args.quality, "budget_mib": args.budget_mb, "images": entries, "sessions": groups}
    (folder / "other" / "previews.json").write_bytes(
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    failed = [e for e in entries if e.get("error")]
    print(f"copies: {len(entries) - len(failed)}/{len(entries)}, "
          f"{mib(sum(src.stat().st_size for src, _ in pairs))} MiB originals -> "
          f"{round(sum(e['copy_mib'] for e in entries if not e.get('error')), 2)} MiB copies, in {out_dir}")
    for e in failed:
        print(f"  [fail] {e['image']}: {e['error']} (view the original instead)")
    for g in groups:
        over = " (over budget: one set alone)" if g["copy_mib"] > args.budget_mb else ""
        print(f"  session {g['session']}: {len(g['images'])} images, {g['copy_mib']} MiB, "
              f"{'、'.join(g['sets'])}{over}")
    print(f"manifest: {folder / 'other' / 'previews.json'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
