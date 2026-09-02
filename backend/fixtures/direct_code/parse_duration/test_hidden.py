import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location(
    "solution", pathlib.Path.cwd() / "solution.py"
)
solution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solution)


class Tests(unittest.TestCase):
    def test_combinations(self):
        self.assertEqual(solution.parse_duration("2h 5m 7s"), 7507)
        self.assertEqual(solution.parse_duration("1d 2h"), 93600)

    def test_whitespace_and_order(self):
        self.assertEqual(solution.parse_duration("  30s   2m "), 150)
        self.assertEqual(solution.parse_duration("5m 1h"), 3900)

    def test_invalid(self):
        for value in ("", "3x", "2h garbage", "-2m"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    solution.parse_duration(value)


if __name__ == "__main__":
    unittest.main()
