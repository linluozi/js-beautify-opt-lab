#!/usr/bin/env python3
"""Count reader probes per token for one jsbeautifier build.

Every ``_get_next_token`` call in the unpatched tokenizer tries up to ten
readers.  The dispatch table exists to skip the ones that cannot match for a
given first character.  This script measures that directly: it wraps each
reader with a counter and reports probes per token, so the speedup can be
traced to a mechanism instead of asserted.

    python benchmark/probes.py --impl baseline --baseline /path/to/js-beautify/python
    python benchmark/probes.py --impl optimized
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JS_ROOT = "/usr/share/nodejs"

READERS = [
    "_readWhitespace",
    "_read_non_javascript",
    "_read_string",
    "_read_pair",
    "_read_word",
    "_read_singles",
    "_read_comment",
    "_read_regexp",
    "_read_xml",
    "_read_punctuation",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--impl", choices=["baseline", "optimized"], required=True)
    parser.add_argument("--baseline", help="upstream python/ directory (baseline only)")
    args = parser.parse_args()

    if args.impl == "baseline":
        if not args.baseline:
            parser.error("--baseline is required for --impl baseline")
        sys.path.insert(0, os.path.abspath(args.baseline))
    else:
        sys.path.insert(0, os.path.join(ROOT, "src"))

    import jsbeautifier
    from jsbeautifier.javascript import tokenizer as tok

    counts = {name: 0 for name in READERS}
    tokens = [0]

    def wrap(name):
        original = getattr(tok.Tokenizer, name)

        def counted(self, *a, **kw):
            counts[name] += 1
            return original(self, *a, **kw)

        setattr(tok.Tokenizer, name, counted)

    for name in READERS:
        wrap(name)
    original_create = tok.Tokenizer._create_token

    def counted_create(self, *a, **kw):
        tokens[0] += 1
        return original_create(self, *a, **kw)

    tok.Tokenizer._create_token = counted_create

    files = sorted(glob.glob(os.path.join(JS_ROOT, "**", "*.js"), recursive=True))
    path = max(files, key=os.path.getsize)
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    jsbeautifier.beautify(text)

    non_ws = sum(v for k, v in counts.items() if k != "_readWhitespace")
    result = {
        "impl": args.impl,
        "file": path,
        "bytes": len(text.encode()),
        "tokens": tokens[0],
        "probes_total_including_whitespace": sum(counts.values()),
        "probes_non_whitespace": non_ws,
        "probes_per_token": round(non_ws / tokens[0], 3),
        "per_reader": counts,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
