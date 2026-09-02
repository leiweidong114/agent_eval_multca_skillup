import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location(
    "solution", pathlib.Path.cwd() / "solution.py"
)
solution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solution)


class Tests(unittest.TestCase):
    def test_overlap_and_touching(self):
        self.assertEqual(
            solution.merge_intervals([(5, 7), (1, 3), (3, 4), (9, 10)]),
            [(1, 4), (5, 7), (9, 10)],
        )

    def test_nested(self):
        self.assertEqual(
            solution.merge_intervals([(1, 10), (2, 3), (4, 8)]),
            [(1, 10)],
        )

    def test_does_not_mutate(self):
        values = [(4, 5), (1, 2)]
        solution.merge_intervals(values)
        self.assertEqual(values, [(4, 5), (1, 2)])

    def test_empty(self):
        self.assertEqual(solution.merge_intervals([]), [])


if __name__ == "__main__":
    unittest.main()
