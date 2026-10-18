# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

import io
import os
import re
import unittest
from unittest import mock

from cdpc import __main__ as cli
from cdpc import diff, query, style
from cdpc.tests.query_test import QueryTestBase

ANSI = re.compile(r"\033\[[0-9;]*m")


class StyleTest(unittest.TestCase):
    def tearDown(self):
        style.configure("never")

    def test_off_unless_configured(self):
        self.assertEqual(style.paint("x", "red"), "x")
        style.configure("always")
        self.assertEqual(style.paint("x", "red"), "\033[31mx\033[0m")
        self.assertEqual(style.change("added", "y"), "\033[32m+ y\033[0m")

    def test_auto_follows_terminal_and_conventions(self):
        tty = mock.Mock(isatty=lambda: True)
        pipe = io.StringIO()
        with mock.patch.dict(os.environ, {"TERM": "xterm"}, clear=True):
            self.assertTrue(style.configure("auto", tty))
            self.assertFalse(style.configure("auto", pipe))
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=True):
            self.assertFalse(style.configure("auto", tty))
        with mock.patch.dict(os.environ, {"TERM": "dumb"}, clear=True):
            self.assertFalse(style.configure("auto", tty))
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}, clear=True):
            self.assertTrue(style.configure("auto", pipe))

    def test_cdpc_takes_global_options_anywhere(self):
        options = cli.global_options
        self.assertEqual(options(["list", "--color=never"]), ("never", None, ["list"]))
        self.assertEqual(options(["--color", "always", "diff"]), ("always", None, ["diff"]))
        self.assertEqual(
            options(["--no-color", "show", "A.b", "--width=70"]), ("never", 70, ["show", "A.b"])
        )
        self.assertEqual(options(["--width", "60", "list"]), ("auto", 60, ["list"]))
        for bad in (["--color=rainbow"], ["--width=wide"], ["--width"]):
            with self.assertRaises(ValueError):
                options(bad)


class WrapTest(unittest.TestCase):
    def tearDown(self):
        style.configure("never")

    def test_wraps_between_words_with_hanging_indent(self):
        style.configure("never", width=40)
        text = style.wrap("alpha beta gamma delta epsilon zeta eta theta", "  - label: ")
        self.assertEqual(
            text.split("\n"),
            ["  - label: alpha beta gamma delta", "           epsilon zeta eta theta"],
        )

    def test_keeps_long_words_whole(self):
        style.configure("never", width=40)
        path = "devtool/lynx_devtool/agent/domain_agent/x.cc"
        lines = style.wrap(f"see {path} now", "x ").split("\n")
        self.assertIn(f"  {path}", lines)

    def test_messages_keep_item_indent(self):
        style.configure("never", width=40)
        text = style.error("first line of an error message that wraps\n  item one two three four five six")
        self.assertEqual(
            text.split("\n"),
            [
                "error: first line of an error message",
                "       that wraps",
                "  item one two three four five six",
            ],
        )

    def test_moves_a_last_column_that_does_not_fit_below_the_row(self):
        style.configure("never", width=40)
        text = style.wrap("upstream source", "    name  " + "x" * 24 + "  ")
        self.assertEqual(
            text.split("\n"), ["    name  " + "x" * 24, "        upstream source"]
        )

    def test_colored_prefix_does_not_change_wrapping(self):
        style.configure("never", width=40)
        plain = style.item("x", "one two three four five six seven eight nine", "red")
        style.configure("always", width=40)
        colored = style.item("x", "one two three four five six seven eight nine", "red")
        self.assertEqual(ANSI.sub("", colored), plain)


class ColorKeepsTextTest(QueryTestBase):
    """Color must only add escape codes; symbols carry the meaning."""

    def tearDown(self):
        style.configure("never")

    def render(self):
        entries, contracts, pristine, display = self.query()
        report = diff.build_report(
            pristine, contracts, [("DOM", "enable")], [], display
        )
        return [
            diff.format_report(report),
            query.format_list(entries),
            query.show("DOM.enable", entries, contracts, pristine, display),
            query.show("DOM.paused", entries, contracts, pristine, display),
        ]

    def test_stripped_color_output_equals_plain_output(self):
        plain = self.render()
        style.configure("always")
        colored = self.render()
        for plain_text, colored_text in zip(plain, colored):
            self.assertNotIn("\033[", plain_text)
            self.assertIn("\033[", colored_text)
            self.assertEqual(ANSI.sub("", colored_text), plain_text)

    def test_lines_fit_the_width_except_single_words(self):
        for width in (60, 100):
            style.configure("never", width=width)
            for text in self.render():
                for line in text.splitlines():
                    if len(line) > width:
                        # Only a word too long for any line, such as a URL,
                        # may overflow.
                        longest = max(line.split(), key=len)
                        self.assertGreater(len(longest), width // 2, line)


if __name__ == "__main__":
    unittest.main()
