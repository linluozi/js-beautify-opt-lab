#!/usr/bin/env python3
"""Per-hunk ablation for the js-beautify tokenizer patch.

Each variant starts from the patched ``src/`` tree, reverts exactly one hunk,
and renders the same 470 KB real file (largest ``.js`` under /usr/share/nodejs).
The reported number is the median of 5 timed runs after 3 warmups; the
"sum of single-hunk savings" line is the point of the exercise: it is compared
against the saving of the full patch to show how much a naive per-hunk
attribution over-states the gain.

    python benchmark/ablation.py --baseline /path/to/js-beautify/python
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
TOKENIZER = os.path.join("jsbeautifier", "javascript", "tokenizer.py")
JS_ROOT = "/usr/share/nodejs"
WARMUP = 3
REPS = 5

WHITESPACE_BLOCK = '''    def _readWhitespace(self):
        # Fast reject: whitespace.read() is only ever entered for characters
        # the pattern can match.  str.isspace() is the same predicate CPython
        # uses for \s, so a False answer proves the pattern cannot match here;
        # the two counters still get the reset that read() would have done.
        c = self._input.peek()
        if c is not None and not (c.isspace() or c in _EXTRA_WHITESPACE):
            whitespace = self._patterns.whitespace
            whitespace.newline_count = 0
            whitespace.whitespace_before_token = ""
            return ""
        return self._patterns.whitespace.read()

'''

DISPATCH_BLOCK = '''        kind = _FAST_START.get(c)
        if kind is not None:
            if kind == "word":
                token = self._read_word(previous_token)
            elif kind == "string":
                token = self._read_string(c)
            elif kind == "singles":
                token = self._read_singles(c)
            elif kind == "punct":
                token = self._read_punctuation()
            else:
                token = self._read_word(previous_token) or self._read_singles(c)
            if token is not None:
                return token

'''

WORD_BLOCK = '''            # Identifiers/numbers cannot contain a line terminator, so the
            # substitution is a no-op unless one is actually present.
            if not resulting_string.isascii() or "\\n" in resulting_string or "\\r" in resulting_string:
                resulting_string = self.acorn.allLineBreaks.sub("\\n", resulting_string)'''

WORD_ORIGINAL = '''            resulting_string = re.sub(self.acorn.allLineBreaks, "\\n", resulting_string)'''

REVERT = {
    "whitespace": [("whitespace", WHITESPACE_BLOCK, "")],
    "dispatch": [("dispatch", DISPATCH_BLOCK, "")],
    "word": [("word", WORD_BLOCK, WORD_ORIGINAL)],
}

VARIANTS = {
    "full-patch": [],
    # leave-one-out: the marginal contribution of a hunk is full - (full minus it)
    "no-whitespace-fast-reject": REVERT["whitespace"],
    "no-first-char-dispatch": REVERT["dispatch"],
    "no-identifier-sub-guard": REVERT["word"],
    # hunk-alone: start from pristine and keep only one hunk
    "only-whitespace-fast-reject": REVERT["dispatch"] + REVERT["word"],
    "only-first-char-dispatch": REVERT["whitespace"] + REVERT["word"],
    "only-identifier-sub-guard": REVERT["whitespace"] + REVERT["dispatch"],
}


def make_variant(name: str) -> str:
    target = tempfile.mkdtemp(prefix=f"jsb-abl-{name}-")
    tree = os.path.join(target, "src")
    shutil.copytree(SRC, tree)
    path = os.path.join(tree, TOKENIZER)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    for label, old, new in VARIANTS[name]:
        if text.count(old) != 1:
            raise SystemExit(f"variant {name}: {label} block not found exactly once")
        text = text.replace(old, new)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return target


def sample() -> str:
    files = sorted(glob.glob(os.path.join(JS_ROOT, "**", "*.js"), recursive=True))
    path = max(files, key=os.path.getsize)
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def worker() -> None:
    import jsbeautifier

    text = sample()
    for _ in range(WARMUP):
        jsbeautifier.beautify(text)
    times = []
    digest = ""
    for _ in range(REPS):
        start = time.perf_counter()
        out = jsbeautifier.beautify(text)
        times.append(time.perf_counter() - start)
        digest = __import__("hashlib").sha256(out.encode()).hexdigest()
    print(json.dumps({"median": statistics.median(times), "times": times, "sha256": digest}))


def run(pythonpath: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--worker"],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr[-2000:])
    return json.loads(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    if args.worker:
        worker()
        return 0
    if not args.baseline:
        parser.error("--baseline is required")

    results = {"baseline": run(os.path.abspath(args.baseline))}
    for name in VARIANTS:
        tree = make_variant(name)
        try:
            results[name] = run(os.path.join(tree, "src"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    base = results["baseline"]["median"]
    rows = {}
    for name, data in results.items():
        if name == "baseline":
            rows[name] = {"median": data["median"], "speedup": 1.0, "saving": 0.0}
        else:
            rows[name] = {
                "median": data["median"],
                "speedup": base / data["median"],
                "saving_vs_baseline": base - data["median"],
            }
    saving = rows["full-patch"]["saving_vs_baseline"]
    only = {
        "whitespace": rows["only-whitespace-fast-reject"]["saving_vs_baseline"],
        "dispatch": rows["only-first-char-dispatch"]["saving_vs_baseline"],
        "word": rows["only-identifier-sub-guard"]["saving_vs_baseline"],
    }
    leave_one_out = {
        "whitespace": saving - rows["no-whitespace-fast-reject"]["saving_vs_baseline"],
        "dispatch": saving - rows["no-first-char-dispatch"]["saving_vs_baseline"],
        "word": saving - rows["no-identifier-sub-guard"]["saving_vs_baseline"],
    }
    summary = {
        "workload": "largest real .js file under /usr/share/nodejs",
        "baseline_median_s": round(base, 4),
        "rows": {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in rows.items()},
        "full_patch_saving_s": round(saving, 4),
        "hunk_alone_saving_s": {k: round(v, 4) for k, v in only.items()},
        "hunk_alone_sum_s": round(sum(only.values()), 4),
        "hunk_alone_double_count_factor": round(sum(only.values()) / saving, 2) if saving else None,
        "leave_one_out_marginal_s": {k: round(v, 4) for k, v in leave_one_out.items()},
        "leave_one_out_sum_s": round(sum(leave_one_out.values()), 4),
        "leave_one_out_explained_fraction": round(sum(leave_one_out.values()) / saving, 4) if saving else None,
        "output_sha256_equal": len({v["sha256"] for v in results.values()}) == 1,
    }
    print(json.dumps(summary, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
