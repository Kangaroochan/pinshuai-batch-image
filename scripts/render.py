"""Render images for each job in a jobs.json through an OpenAI-compatible relay.

Each job is one prompt file plus optional product reference images. Jobs with
references go to /images/edits (every reference as its own image[] part);
jobs without go to /images/generations. The prompt file is sent byte-for-byte
(UTF-8, BOM stripped), never trimmed or rewritten.

Credentials: OPENAI_API_KEY and OPENAI_BASE_URL from the environment, else
from the first .env found in: --env-file, the jobs.json folder and its
parents, the current directory. The key is never printed or logged.

Usage:
  python render.py jobs.json --dry-run
  python render.py jobs.json                    # full batch, per-job counts
  python render.py jobs.json --count 5          # five images per job
  python render.py jobs.json --only 2-做钻石画 --count 3

Output: the images go in <jobs.json folder>/出图/<timestamp>/ (or --out), and
everything else (run.json, later report.md, visual-review.json, lesson-sync.json)
goes in its other/ subfolder, so the image folder holds only images.
"""
import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

DEFAULTS = {
    "model": "gpt-image-2.5-sunburst",
    "size": "1024x1536",
    "quality": "medium",
    "input_fidelity": "high",
    "image_field": "image[]",
}
RETRYABLE = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, status, text):
        super().__init__(f"HTTP {status}: {text}")
        self.status = status


def find_env_file(explicit, jobs_path):
    if explicit:
        return Path(explicit)
    for folder in [jobs_path.parent, *jobs_path.parent.parents, Path.cwd()]:
        if (folder / ".env").is_file():
            return folder / ".env"
    return None


def load_env(path):
    if not path:
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def read_prompt(path):
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    text = data.decode("utf-8")  # strict: refuse a file that is not UTF-8
    if not text.strip():
        raise ValueError(f"prompt file is empty: {path}")
    return text


def load_jobs(jobs_path, only):
    spec = json.loads(jobs_path.read_text(encoding="utf-8-sig"))
    base = jobs_path.parent
    settings = {k: spec.get(k, v) for k, v in DEFAULTS.items()}
    jobs, problems, seen = [], [], set()
    for i, raw in enumerate(spec.get("jobs", []), 1):
        name = raw.get("name") or f"job{i}"
        if name in seen:
            problems.append(f"duplicate job name: {name}")
        seen.add(name)
        prompt_path = (base / raw.get("prompt", "")).resolve()
        refs = [(base / r).resolve() for r in raw.get("refs", [])]
        if not prompt_path.is_file():
            problems.append(f"{name}: prompt file not found: {prompt_path}")
        for r in refs:
            if not r.is_file():
                problems.append(f"{name}: reference image not found: {r}")
        count = raw.get("count", spec.get("count", 1))
        if not isinstance(count, int) or count < 1:
            problems.append(f"{name}: count must be a positive integer")
        jobs.append({"name": name, "prompt_path": prompt_path, "refs": refs, "count": count,
                     "size": raw.get("size", settings["size"])})
    if not jobs:
        problems.append("jobs.json has no jobs")
    if only:
        unknown = set(only) - {j["name"] for j in jobs}
        if unknown:
            problems.append(f"--only names not in jobs.json: {', '.join(sorted(unknown))}")
        jobs = [j for j in jobs if j["name"] in only]
    return spec, settings, jobs, problems


def post(url, key, body, content_type, timeout):
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace")[:500]) from None


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                     + str(value).encode("utf-8") + b"\r\n")
    for name, path in files:
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n').encode("utf-8")
        parts.append(head + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def image_ext(data):
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("response is not a PNG, JPEG or WebP image")


def generate(task, settings, base_url, key, timeout):
    fields = {"model": settings["model"], "prompt": task["prompt"], "size": task["size"],
              "quality": settings["quality"], "n": 1}
    if task["refs"]:
        if settings["input_fidelity"]:
            fields["input_fidelity"] = settings["input_fidelity"]
        body, ctype = multipart(fields, [(settings["image_field"], r) for r in task["refs"]])
        data = post(f"{base_url}/images/edits", key, body, ctype, timeout)
    else:
        data = post(f"{base_url}/images/generations", key,
                    json.dumps(fields).encode("utf-8"), "application/json", timeout)
    item = data["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    with urllib.request.urlopen(item["url"], timeout=timeout) as resp:  # CDN link, no key sent
        return resp.read()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # Windows consoles default to GBK; job names are Chinese
    p = argparse.ArgumentParser(description="Render jobs.json through an image relay")
    p.add_argument("jobs", help="path to jobs.json")
    p.add_argument("--count", type=int, help="images per job, overrides jobs.json")
    p.add_argument("--only", action="append", help="run only this job name; repeatable")
    p.add_argument("--out", help="image folder (default: <jobs.json folder>/出图/<timestamp>); "
                                 "run.json goes in its other/ subfolder")
    p.add_argument("--model", help="override the model in jobs.json")
    p.add_argument("--retries", type=int, default=1, help="extra attempts on 429/5xx/network errors")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--timeout", type=int, default=600, help="seconds per request")
    p.add_argument("--env-file", help=".env holding OPENAI_API_KEY / OPENAI_BASE_URL")
    p.add_argument("--dry-run", action="store_true", help="check jobs and print the plan; no network, no key")
    args = p.parse_args()

    jobs_path = Path(args.jobs).resolve()
    spec, settings, jobs, problems = load_jobs(jobs_path, args.only)
    if args.model:
        settings["model"] = args.model
    if args.count is not None:
        if args.count < 1:
            problems.append("--count must be at least 1")
        for j in jobs:
            j["count"] = args.count
    for j in jobs:
        if j["prompt_path"].is_file():
            try:
                j["prompt"] = read_prompt(j["prompt_path"])
            except (UnicodeDecodeError, ValueError) as e:
                problems.append(f"{j['name']}: {e}")
    if problems:
        sys.exit("jobs.json problems:\n  " + "\n  ".join(problems))

    total = sum(j["count"] for j in jobs)
    print(f"model={settings['model']} quality={settings['quality']} jobs={len(jobs)} images={total}")
    for j in jobs:
        mode = f"edits, {len(j['refs'])} ref(s)" if j["refs"] else "generations, no ref"
        print(f"  {j['name']}: {j['count']} x {j['size']} via {mode}; prompt sha256 "
              f"{sha256(j['prompt'].encode('utf-8'))[:12]}")
    if args.dry_run:
        print("dry run: nothing sent")
        return

    env_file = find_env_file(args.env_file, jobs_path)
    load_env(env_file)
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit("OPENAI_API_KEY not set (environment or .env)")
    base_url = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")

    out_dir = Path(args.out).resolve() if args.out else \
        jobs_path.parent / "出图" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    ref_hashes = {r: sha256(r.read_bytes()) for j in jobs for r in j["refs"]}
    tasks = []
    for j in jobs:
        for k in range(1, j["count"] + 1):
            tasks.append({**j, "file_stem": f"{j['name']}-{k:02d}"})

    def redact(text):
        return str(text).replace(key, "[REDACTED]")

    def run(task):
        attempts = 0
        while True:
            attempts += 1
            try:
                data = generate(task, settings, base_url, key, args.timeout)
                path = out_dir / (task["file_stem"] + image_ext(data))
                path.write_bytes(data)
                return {"status": "ok", "file": path.name, "attempts": attempts, "sha256": sha256(data)}
            except HttpError as e:
                if e.status not in RETRYABLE or attempts > args.retries:
                    return {"status": "failed", "attempts": attempts, "error": redact(e)}
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempts > args.retries:
                    return {"status": "failed", "attempts": attempts, "error": redact(e)}
            except Exception as e:  # bad response body: retrying would pay for another image
                return {"status": "failed", "attempts": attempts, "error": redact(e)}
            print(f"[retry] {task['file_stem']}")

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.concurrency, len(tasks)))) as pool:
        futures = {pool.submit(run, t): t for t in tasks}
        for fut in as_completed(futures):
            t = futures[fut]
            r = results[t["file_stem"]] = fut.result()
            print(f"[{r['status']}] {t['file_stem']}" + (f": {r['error']}" if r["status"] != "ok" else ""))

    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "jobs_file": str(jobs_path),
        "images_dir": str(out_dir),  # images[].file is relative to this folder, not to run.json
        "endpoint_host": base_url.split("/")[2] if "//" in base_url else base_url,
        "prompt_skill": spec.get("prompt_skill"),  # None for jobs.json written before this field existed
        **({"template": spec["template"]} if spec.get("template") else {}),  # pinshuai-image-template: which template's lessons.md
        "settings": settings,
        "images": [{
            "job": t["name"], "file_stem": t["file_stem"], "prompt_file": str(t["prompt_path"]),
            "prompt_sha256": sha256(t["prompt"].encode("utf-8")),
            "refs": [str(r) for r in t["refs"]], "ref_sha256": [ref_hashes[r] for r in t["refs"]],
            "size": t["size"], **results[t["file_stem"]],
        } for t in tasks],
    }
    side_dir = out_dir / "other"
    side_dir.mkdir(exist_ok=True)
    (side_dir / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(r["status"] == "ok" for r in results.values())
    print(f"done: {ok}/{len(tasks)} saved to {out_dir}; run.json in {side_dir}")
    sys.exit(0 if ok == len(tasks) else 1)


if __name__ == "__main__":
    main()
