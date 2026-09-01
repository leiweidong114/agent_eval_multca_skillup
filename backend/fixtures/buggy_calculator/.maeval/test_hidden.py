import os
import sys
import unittest

sys.path.insert(0, os.getcwd())

from calculator import median


class MedianTests(unittest.TestCase):
    def test_odd_length(self):
        self.assertEqual(median([9, 1, 3]), 3)

    def test_even_length(self):
        self.assertEqual(median([1, 2, 8, 10]), 5)

    def test_does_not_mutate_input(self):
        values = [4, 1, 3, 2]
        median(values)
        self.assertEqual(values, [4, 1, 3, 2])

    def test_empty_raises_value_error(self):
        with self.assertRaises(ValueError):
            median([])


if __name__ == "__main__":
    unittest.main()
