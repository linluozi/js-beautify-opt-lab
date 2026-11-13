#!/usr/bin/env python3
"""A/B benchmark for the js-beautify tokenizer patch.

The baseline package and the patched package cannot be imported into the same
interpreter under the same module name, so this file is both the driver and the
worker.  The driver spawns one worker per implementation with the right
``PYTHONPATH``; each worker writes one JSON file per timed sample into
``benchmark/raw/``.

Usage (from the repository root):

    python benchmark/bench.py --baseline /path/to/js-beautify/python

The baseline path must be a checkout of beautifier/js-beautify at
4f52c492a47d1aadfeb22b5350a4784a36f736b3 (see README).  ``benchmark/`` itself
is put on the worker's ``PYTHONPATH`` for the patched run.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(HERE, "raw")
JS_ROOT = "/usr/share/nodejs"

WARMUP = 3
REPS = 5
CORPUS_PASSES = 3
SEED = 4321


def corpus_files() -> list[str]:
    files = sorted(glob.glob(os.path.join(JS_ROOT, "**", "*.js"), recursive=True))
    files = [f for f in files if os.path.getsize(f) > 0]
    return files


def largest(n: int) -> list[str]:
    return sorted(corpus_files(), key=lambda f: -os.path.getsize(f))[:n]


def scales() -> dict[str, list[str]]:
    top = largest(6)
    return {
        "small": [top[1]],          # second largest: single 218 KB file
        "medium": [top[0]],         # largest single file, 470 KB
        "large": top,               # six largest files together
    }


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def now() -> float:
    return time.perf_counter()


def write_sample(impl: str, scale: str, index: int, seconds: float, nbytes: int, digest: str) -> None:
    os.makedirs(RAW, exist_ok=True)
    payload = {
        "impl": impl,
        "scale": scale,
        "sample": index,
        "seconds": seconds,
        "bytes": nbytes,
        "sha256": digest,
    }
    path = os.path.join(RAW, f"{impl}-{scale}-{index}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def worker(impl: str) -> dict:
    import jsbeautifier

    result = {"impl": impl, "scales": {}, "corpus": {}}
    for name, paths in scales().items():
        text = "\n".join(read(p) for p in paths)
        nbytes = len(text.encode())
        for _ in range(WARMUP):
            jsbeautifier.beautify(text)
        times = []
        digest = ""
        for i in range(REPS):
            start = now()
            out = jsbeautifier.beautify(text)
            times.append(now() - start)
            digest = hashlib.sha256(out.encode()).hexdigest()
            write_sample(impl, name, i, times[-1], nbytes, digest)
        result["scales"][name] = {
            "files": [os.path.relpath(p, JS_ROOT) for p in paths],
            "bytes": nbytes,
            "times": times,
            "sha256": digest,
        }

    # Real corpus: every shipped .js file, timed individually.
    files = corpus_files()
    per_file: dict[str, list[float]] = {f: [] for f in files}
    for _ in range(CORPUS_PASSES):
        for path in files:
            text = read(path)
            start = now()
            jsbeautifier.beautify(text)
            per_file[path].append(now() - start)
    result["corpus"] = {
        f: {
            "bytes": os.path.getsize(f),
            "times": per_file[f],
            "median": statistics.median(per_file[f]),
        }
        for f in files
    }
    result["runtime"] = {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "seed": SEED,
        "warmup": WARMUP,
        "reps": REPS,
        "corpus_passes": CORPUS_PASSES,
        "js_root": JS_ROOT,
    }
    return result


def aggregate(result: dict) -> dict:
    scales_out = {}
    for name, data in result["scales"].items():
        times = sorted(data["times"])
        scales_out[name] = {
            "files": data["files"],
            "bytes": data["bytes"],
            "median": statistics.median(times),
            "p25": statistics.quantiles(times, n=4)[0] if len(times) > 1 else times[0],
            "p75": statistics.quantiles(times, n=4)[2] if len(times) > 1 else times[-1],
            "min": times[0],
            "max": times[-1],
            "sha256": data["sha256"],
            "reps": len(times),
        }
    corpus = result["corpus"]
    big = [v for v in corpus.values() if v["bytes"] >= 16384]
    corpus_summary = {
        "files": len(corpus),
        "files_ge_16kb": len(big),
        "bytes_total": sum(v["bytes"] for v in corpus.values()),
        "per_file_median": {k: v["median"] for k, v in corpus.items()},
    }
    return {"scales": scales_out, "corpus": corpus_summary, "runtime": result["runtime"]}


def run_worker(impl: str, pythonpath: str, out_path: str) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath
    cmd = [sys.executable, os.path.abspath(__file__), "--worker", "--impl", impl]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"worker {impl} failed:\n{proc.stderr[-3000:]}")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(proc.stdout)


def cpu_line() -> str:
    with open("/proc/cpuinfo", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", help="path to upstream python/ directory")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--impl", default="")
    args = parser.parse_args()

    if args.worker:
        json.dump(worker(args.impl), sys.stdout)
        return 0

    if not args.baseline:
        parser.error("--baseline is required")
    baseline = os.path.abspath(args.baseline)
    if not os.path.isdir(os.path.join(baseline, "jsbeautifier")):
        parser.error(f"{baseline} does not contain jsbeautifier/")

    os.makedirs(RAW, exist_ok=True)
    tmp = {}
    for impl, path in (("baseline", baseline), ("optimized", os.path.join(ROOT, "src"))):
        out = os.path.join(RAW, f".{impl}-result.json")
        run_worker(impl, path, out)
        tmp[impl] = out

    summary = {"environment": {"cpu": cpu_line(), "python": sys.version.split()[0]}}
    data = {}
    for impl, path in tmp.items():
        with open(path, encoding="utf-8") as handle:
            data[impl] = aggregate(json.load(handle))
        with open(os.path.join(HERE, f"{impl}.json"), "w", encoding="utf-8") as handle:
            json.dump(data[impl], handle, indent=2, sort_keys=True)
        os.remove(path)

    speedups = {}
    for name in data["baseline"]["scales"]:
        base = data["baseline"]["scales"][name]["median"]
        opt = data["optimized"]["scales"][name]["median"]
        speedups[name] = base / opt
    # per-file real-corpus ratios
    base_files = data["baseline"]["corpus"]["per_file_median"]
    opt_files = data["optimized"]["corpus"]["per_file_median"]
    ratios = {}
    for path, base in base_files.items():
        if data["baseline"]["corpus"]["per_file_median"].get(path) and path in opt_files:
            if os.path.getsize(path) >= 16384:
                ratios[path] = base / opt_files[path]
    summary["speedups"] = speedups
    summary["real_corpus"] = {
        "files_ge_16kb": len(ratios),
        "median": statistics.median(ratios.values()) if ratios else None,
        "min": min(ratios.values()) if ratios else None,
        "max": max(ratios.values()) if ratios else None,
        "per_file": ratios,
    }
    with open(os.path.join(HERE, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
