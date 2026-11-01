"""Build a jobs.json for render.py from existing prompt files and reference images.

pinshuai-prompt-diversify saves each prompt next to its reference image as
<image stem>-提示词.txt, then -提示词-2.txt, -3 ... for later versions. With
--images, each image is paired with the newest version of its prompt and is
sent as that prompt's reference. --job pairs a prompt with images by hand.
Output size follows the first reference image's aspect ratio unless --size
is given. Nothing is sent to the network.

Usage:
  python make_jobs.py --images D:/图/1-四宫格.webp D:/图/2-做钻石画.webp
  python make_jobs.py --images 2-做钻石画.webp --extra-ref 产品裁图/挂饰.png
  python make_jobs.py --job 3-挂在树上-提示词-2.txt 3-挂在树上.webp --count 5
  python make_jobs.py --job 1-四宫格-提示词.txt        # no image: text-only
"""
import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path


def image_size(path):
    d = path.read_bytes()
    if d.startswith(b"\x89PNG"):
        return struct.unpack(">II", d[16:24])
    if d[:4] == b"RIFF" and d[8:12] == b"WEBP":
        chunk = d[12:16]
        if chunk == b"VP8 ":
            w, h = struct.unpack("<HH", d[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L":
            bits = int.from_bytes(d[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X":
            return int.from_bytes(d[24:27], "little") + 1, int.from_bytes(d[27:30], "little") + 1
    if d.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(d):
            if d[i] != 0xFF:
                i += 1
                continue
            marker = d[i + 1]
            if marker in (0xD8, 0x01, 0xFF) or 0xD0 <= marker <= 0xD7:
                i += 2 if marker != 0xFF else 1
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", d[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", d[i + 2:i + 4])[0]
    return None


def size_for(path):
    dims = image_size(path)
    if not dims or not dims[1]:
        return "1024x1536", "unknown"
    ratio = dims[0] / dims[1]
    size = "1024x1536" if ratio < 0.85 else "1536x1024" if ratio > 1.18 else "1024x1024"
    return size, f"{dims[0]}x{dims[1]}"


def find_prompts(image):
    """All prompt versions for an image, oldest first."""
    pattern = re.compile(rf"^{re.escape(image.stem)}-提示词(?:-(\d+))?\.txt$")
    found = []
    for f in image.parent.iterdir():
        m = pattern.match(f.name)
        if m and f.is_file():
            found.append((int(m.group(1) or 1), f))
    return [f for _, f in sorted(found)]


def job_name(prompt):
    return re.sub(r"-提示词(?:-\d+)?$", "", prompt.stem) or prompt.stem


def rel(path, base):
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:  # different drive
        return path.as_posix()


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Pair existing prompts with reference images into jobs.json")
    p.add_argument("--images", nargs="+", default=[], help="reference images; each finds its own <stem>-提示词*.txt")
    p.add_argument("--job", nargs="+", action="append", default=[], metavar="PROMPT [IMAGE ...]",
                   help="explicit pairing: a prompt file followed by zero or more images; repeatable")
    p.add_argument("--extra-ref", action="append", default=[],
                   help="image attached to every job that has images (e.g. product photo); text-only jobs stay text-only")
    p.add_argument("--count", type=int, default=1, help="images per job (default 1)")
    p.add_argument("--size", help="fixed output size for all jobs, e.g. 1024x1536")
    p.add_argument("--model", help="model to write into jobs.json (render.py default otherwise)")
    p.add_argument("--prompt-skill", default="pinshuai-prompt-diversify",
                   help="skill that wrote the prompts; decides where prompt-side lessons are synced")
    p.add_argument("-o", "--output", help="jobs.json path (default: next to the first image, never overwritten)")
    args = p.parse_args()

    if not args.images and not args.job:
        p.error("give --images or --job")
    if args.count < 1:
        p.error("--count must be at least 1")

    problems, pairs = [], []
    extra = [Path(r).resolve() for r in args.extra_ref]
    for img in map(lambda s: Path(s).resolve(), args.images):
        if not img.is_file():
            problems.append(f"image not found: {img}")
            continue
        versions = find_prompts(img)
        if not versions:
            problems.append(f"no prompt for {img.name}: expected {img.stem}-提示词.txt in {img.parent}")
            continue
        pairs.append((versions[-1], [img], versions[:-1]))
    for entry in args.job:
        prompt = Path(entry[0]).resolve()
        pairs.append((prompt, [Path(i).resolve() for i in entry[1:]], []))
    for prompt, imgs, _ in pairs:
        if not prompt.is_file():
            problems.append(f"prompt not found: {prompt}")
        for i in imgs:
            if not i.is_file():
                problems.append(f"image not found: {i}")
    for r in extra:
        if not r.is_file():
            problems.append(f"extra ref not found: {r}")
    if problems:
        sys.exit("problems:\n  " + "\n  ".join(problems))

    first = pairs[0][1][0] if pairs[0][1] else pairs[0][0]
    if args.output:
        out = Path(args.output).resolve()
    else:
        out, n = first.parent / "jobs.json", 2
        while out.exists():
            out, n = first.parent / f"jobs-{n}.json", n + 1
    if out.exists() and args.output:
        sys.exit(f"{out} already exists; pick another -o")
    base = out.parent

    jobs, names = [], set()
    print(f"{'job':<20} {'prompt':<28} {'refs':<5} {'size':<10} note")
    for prompt, imgs, older in pairs:
        name, k = job_name(prompt), 2
        while name in names:
            name, k = f"{job_name(prompt)}-{k}", k + 1
        names.add(name)
        # a --job with no images is text-only on purpose; extra refs stay off it
        refs = imgs + [r for r in extra if r not in imgs] if imgs else []
        if args.size:
            size, note = args.size, "fixed by --size"
        elif refs:
            size, dims = size_for(refs[0])
            note = f"from {refs[0].name} {dims}"
        else:
            size, note = "1024x1536", "text-only, default size"
        if older:
            note += f"; older prompts: {', '.join(o.name for o in older)}"
        jobs.append({"name": name, "prompt": rel(prompt, base),
                     "refs": [rel(r, base) for r in refs], "count": args.count, "size": size})
        print(f"{name:<20} {prompt.name:<28} {len(refs):<5} {size:<10} {note}")

    spec = {"prompt_skill": args.prompt_skill}
    if args.model:
        spec["model"] = args.model
    spec["jobs"] = jobs
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(jobs)} jobs, {sum(j['count'] for j in jobs)} images)")


if __name__ == "__main__":
    main()
