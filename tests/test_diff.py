#!/usr/bin/env python3
"""Byte-for-byte differential test: upstream vs patched jsbeautifier.

Samples are real JavaScript files shipped on this machine plus seeded random
slices of them and hand-written edge cases.  Every sample is de-duplicated by
SHA-256, so the reported "independent samples" number is the number of distinct
inputs actually exercised -- not the number of loop iterations.

Usage:

    python tests/test_diff.py --baseline /path/to/js-beautify/python

Exit code 0 means every distinct sample produced byte-identical output.
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORKER = os.path.join(HERE, "diff_worker.py")
JS_ROOT = "/usr/share/nodejs"
SEED = 4321
TARGET_UNIQUE = 1200


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


EDGE_CASES = [
    "",
    " ",
    "\n\n\n",
    "\r\n\r\n",
    "\u2028\u2029",
    "var \\u2028x = 1;",
    "const s = `line\u2028sep`;",
    "let t = 'a\\u2028b';",
    "#!/usr/bin/env node\nconsole.log(1)",
    "<!-- html comment\nvar a=1",
    "-->",
    "var re = /[a-z\\/]+/gi; if (x) { y(); }",
    "function f(a={b:1}, ...rest){return `${a.b}${rest[0]}`}",
    "class A extends B { #p = 1; static { this.x = 2 } get y(){return this.#p} }",
    "let n = .5 + 0x1f + 0b1010 + 1_000n;",
    "a?.b ?? c; x ||= y; z **= 2;",
    "for (const [k,v] of Object.entries(o)) console.log(k,v);",
    "#!shebang\n#1=[a,b,c];\nvar x = #1[0];",
    "var obj = { get foo(){return 1}, set foo(v){} };",
    "/* block\n * comment */ var a = 1; // tail\n",
    "if(a){b()}else if(c){d()}else{e()}",
    "switch(x){case 1:y();break;default:z()}",
    "try{a()}catch(e){throw e}finally{b()}",
    "\ufeffvar bom = 1;",
    "function* g(){yield* h(); await x;}",
    "const tpl = `a${`b${c}d`}e`;",
    "const xml = <a b=\"c\">{d}</a>;",
    "a=b/c/d; e=/re/g.test(f);",
    "var 变量 = '非ASCII'; function 函数(){return 变量}",
]


def build_samples() -> tuple[list[str], dict]:
    rng = random.Random(SEED)
    files = sorted(glob.glob(os.path.join(JS_ROOT, "**", "*.js"), recursive=True))
    real = [read(p) for p in files if os.path.getsize(p) > 0]
    seen: dict[str, str] = {}
    stats = {"generated": 0, "real_files": len(real), "slices": 0, "edges": 0}

    def add(text: str) -> None:
        stats["generated"] += 1
        digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
        if digest not in seen:
            seen[digest] = text

    for text in real:
        add(text)
    stats["real_files"] = len(seen)
    for text in EDGE_CASES:
        add(text)
    stats["edges"] = len(seen) - stats["real_files"]

    while len(seen) < TARGET_UNIQUE:
        base = real[rng.randrange(len(real))]
        if not base:
            continue
        length = rng.choice([16, 32, 64, 128, 256, 512, 1024, 2048, 4096])
        if len(base) <= length:
            add(base)
            continue
        start = rng.randrange(0, len(base) - length)
        snippet = base[start : start + length]
        # Wrap slices so many of them are syntactically meaningful as well.
        shape = rng.randrange(4)
        if shape == 1:
            snippet = "function __s(){\n" + snippet + "\n}\n"
        elif shape == 2:
            snippet = "const o = {\n" + snippet + "\n};\n"
        elif shape == 3:
            snippet = "// slice\n" + snippet
        add(snippet)
        stats["slices"] += 1
    return list(seen.values()), stats


def run_impl(impl: str, pythonpath: str, samples_path: str, out_dir: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, WORKER, samples_path, out_dir],
        env=env,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        raise SystemExit(f"{impl} worker failed:\n{proc.stderr[-2000:]}")
    with open(os.path.join(out_dir, "index.json"), encoding="utf-8") as handle:
        index = json.load(handle)
    return {"index": index, "seconds": elapsed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, help="upstream python/ directory")
    args = parser.parse_args()
    baseline = os.path.abspath(args.baseline)

    samples, stats = build_samples()
    tmp = tempfile.mkdtemp(prefix="jsb-diff-")
    try:
        samples_path = os.path.join(tmp, "samples.json")
        with open(samples_path, "w", encoding="utf-8") as handle:
            json.dump(
                [{"b64": base64.b64encode(s.encode("utf-8", "surrogatepass")).decode()} for s in samples],
                handle,
            )
        base_dir = os.path.join(tmp, "baseline")
        opt_dir = os.path.join(tmp, "optimized")
        base = run_impl("baseline", baseline, samples_path, base_dir)
        opt = run_impl("optimized", os.path.join(ROOT, "src"), samples_path, opt_dir)

        mismatches = []
        errors = 0
        for i in range(len(samples)):
            with open(os.path.join(base_dir, f"{i:05d}.bin"), "rb") as handle:
                a = handle.read()
            with open(os.path.join(opt_dir, f"{i:05d}.bin"), "rb") as handle:
                b = handle.read()
            if base["index"][i]["status"] == "error" or opt["index"][i]["status"] == "error":
                errors += 1
            if a != b:
                mismatches.append(i)
        digest_set = {
            hashlib.sha256(s.encode("utf-8", "surrogatepass")).hexdigest() for s in samples
        }
        print(json.dumps({
            "independent_samples": len(samples),
            "unique_by_sha256": len(digest_set),
            "generated_attempts": stats["generated"],
            "real_files": stats["real_files"],
            "edge_cases": stats["edges"],
            "random_slices": stats["slices"],
            "seed": SEED,
            "baseline_seconds": round(base["seconds"], 2),
            "optimized_seconds": round(opt["seconds"], 2),
            "samples_with_error_either_side": errors,
            "mismatches": len(mismatches),
            "mismatch_indices": mismatches[:10],
            "verdict": "PASS" if not mismatches else "FAIL",
        }, indent=2))
        return 0 if not mismatches else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
