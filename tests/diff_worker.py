"""Render a list of samples with one jsbeautifier build and dump the outputs."""
import base64
import json
import os
import sys

import jsbeautifier

samples_path, out_dir = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)
with open(samples_path, encoding="utf-8") as handle:
    samples = json.load(handle)
index = []
for i, item in enumerate(samples):
    text = base64.b64decode(item["b64"]).decode("utf-8", "surrogatepass")
    try:
        out = jsbeautifier.beautify(text)
        data = out.encode("utf-8", "surrogatepass")
        status = "ok"
    except Exception as exc:  # record, never hide
        data = f"{type(exc).__name__}: {exc}".encode()
        status = "error"
    with open(os.path.join(out_dir, f"{i:05d}.bin"), "wb") as handle:
        handle.write(data)
    index.append({"i": i, "sha": __import__("hashlib").sha256(data).hexdigest(), "len": len(data), "status": status})
with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as handle:
    json.dump(index, handle)
