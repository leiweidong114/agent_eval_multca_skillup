import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location(
    "solution", pathlib.Path.cwd() / "solution.py"
)
solution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solution)


class Tests(unittest.TestCase):
    def test_typical(self):
        self.assertEqual(
            solution.sliding_window_max([1, 3, -1, -3, 5, 3, 6, 7], 3),
            [3, 3, 5, 5, 6, 7],
        )

    def test_edges(self):
        self.assertEqual(solution.sliding_window_max([2, 1], 1), [2, 1])
        self.assertEqual(solution.sliding_window_max([2, 1], 2), [2])

    def test_invalid(self):
        for k in (0, 4):
            with self.subTest(k=k):
                with self.assertRaises(ValueError):
                    solution.sliding_window_max([1, 2, 3], k)


if __name__ == "__main__":
    unittest.main()
