import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location(
    "solution", pathlib.Path.cwd() / "solution.py"
)
solution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solution)


class Tests(unittest.TestCase):
    def test_strings(self):
        self.assertEqual(
            solution.dedupe_stable(["b", "a", "b", "c", "a"]),
            ["b", "a", "c"],
        )

    def test_integers(self):
        self.assertEqual(solution.dedupe_stable([3, 3, 1, 2, 1]), [3, 1, 2])

    def test_empty(self):
        self.assertEqual(solution.dedupe_stable([]), [])


if __name__ == "__main__":
    unittest.main()
