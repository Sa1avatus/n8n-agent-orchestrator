import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from durations import humanize  # noqa: E402


class HumanizeTest(unittest.TestCase):
    def test_seconds_and_minutes(self) -> None:
        self.assertEqual(humanize(0), "0s")
        self.assertEqual(humanize(59), "59s")
        self.assertEqual(humanize(60), "1m")
        self.assertEqual(humanize(90), "1m 30s")

    def test_hours(self) -> None:
        self.assertEqual(humanize(3600), "60m")
        self.assertEqual(humanize(3661), "61m")


if __name__ == "__main__":
    unittest.main()
