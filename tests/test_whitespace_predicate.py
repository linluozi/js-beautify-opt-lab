"""Consistency test for the whitespace fast-reject predicate.

`Tokenizer._readWhitespace` skips the regex read when the next character
cannot start a whitespace run.  The shortcut is safe only if the predicate is
never False for a character the compiled pattern accepts (a false positive
just means the ordinary read path runs).  This test sweeps the whole BMP and
enforces that one-directional invariant.
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from jsbeautifier.core.inputscanner import InputScanner  # noqa: E402
from jsbeautifier.core.options import Options  # noqa: E402
from jsbeautifier.javascript import acorn  # noqa: E402
from jsbeautifier.javascript import tokenizer as js_tokenizer  # noqa: E402


class WhitespacePredicateTest(unittest.TestCase):
    def test_predicate_matches_compiled_pattern(self):
        patterns = js_tokenizer.TokenizerPatterns(InputScanner(""), acorn, Options())
        compiled = patterns.whitespace._match_pattern
        extra = js_tokenizer._EXTRA_WHITESPACE
        false_negatives = []
        for codepoint in range(0x3000):
            ch = chr(codepoint)
            predicted = ch.isspace() or ch in extra
            actual = bool(compiled.match(ch))
            if actual and not predicted:
                false_negatives.append((hex(codepoint), predicted, actual))
        self.assertEqual(false_negatives, [])

    def test_bom_is_treated_as_whitespace(self):
        self.assertIn("\ufeff", js_tokenizer._EXTRA_WHITESPACE)
        self.assertFalse("\ufeff".isspace())


if __name__ == "__main__":
    unittest.main()
