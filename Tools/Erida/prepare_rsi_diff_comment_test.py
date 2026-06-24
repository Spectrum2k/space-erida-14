# SPDX-FileCopyrightText: 2026 Yaroslav Yudaev <ydaevy10@gmail.com>
# SPDX-License-Identifier: MIT

import unittest

from Tools.Erida.prepare_rsi_diff_comment import prepare_comment


class PrepareRsiDiffCommentTest(unittest.TestCase):
    def test_small_comment_is_preserved(self):
        markdown = "RSI Diff Bot\n\n<details><summary>small.rsi</summary>ok</details>"

        prepared = prepare_comment(markdown, "https://example.invalid/run", comment_limit=1000)

        self.assertFalse(prepared.oversized)
        self.assertEqual(prepared.body, markdown)
        self.assertEqual(prepared.omitted_groups, 0)

    def test_large_comment_keeps_whole_details_blocks_under_limit(self):
        header = "RSI Diff Bot; head commit abc merging into def\n\n"
        block = (
            "<details><summary>Resources/Textures/Test{index}.rsi</summary>\n"
            "<p>\n\n"
            "| State | Old | New | Status |\n"
            "| --- | --- | --- | --- |\n"
            "| icon | ![](https://example.invalid/{index}/old.png) | "
            "![](https://example.invalid/{index}/new.png) | Modified |\n"
            "\n</p>\n</details>"
        )
        markdown = header + "".join(block.format(index=index) for index in range(100))

        prepared = prepare_comment(markdown, "https://example.invalid/run", comment_limit=5000)

        self.assertTrue(prepared.oversized)
        self.assertLessEqual(len(prepared.body), 5000)
        self.assertIn("workflow run", prepared.body)
        self.assertIn("_Omitted ", prepared.body)
        self.assertGreater(prepared.omitted_groups, 0)

    def test_large_comment_without_details_is_capped(self):
        markdown = "RSI Diff Bot\n\n" + ("x" * 10000)

        prepared = prepare_comment(markdown, "https://example.invalid/run", comment_limit=1000)

        self.assertTrue(prepared.oversized)
        self.assertLessEqual(len(prepared.body), 1000)
        self.assertIn("Full RSI diff is too large", prepared.body)


if __name__ == "__main__":
    unittest.main()
