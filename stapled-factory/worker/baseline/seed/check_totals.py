import unittest
from catalog import total


class TotalTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(total([]), 0)

    def test_prices(self):
        self.assertEqual(total([2, 3]), 5)


if __name__ == '__main__':
    unittest.main()
