import unittest

from wg_drift.globmatch import match_any, match_path


class GlobMatchTests(unittest.TestCase):
    def test_exact(self) -> None:
        self.assertTrue(match_path("src/app/main.py", "src/**"))
        self.assertTrue(match_path("src/app/main.py", "src/app/*.py"))
        self.assertFalse(match_path("src/app/main.py", "src/*.py"))

    def test_double_star(self) -> None:
        self.assertTrue(match_path("a/b/c.md", "**/*.md"))
        self.assertTrue(match_path("a/b/c.md", "a/**/c.md"))
        self.assertTrue(match_path("a/b/c.md", "**"))
        self.assertTrue(match_path("a/b/c.md", "**/c.md"))

    def test_match_any(self) -> None:
        self.assertTrue(match_any("docs/readme.md", ["src/**", "docs/**"]))
        self.assertFalse(match_any("docs/readme.md", ["src/**"]))


if __name__ == "__main__":
    unittest.main()

