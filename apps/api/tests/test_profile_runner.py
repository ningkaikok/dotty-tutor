from __future__ import annotations

import unittest

from evaluation.tutor.profile_runner import check_profile_cases


class ProfileRunnerTests(unittest.TestCase):
    def test_offline_profile_cases_pass(self) -> None:
        result = check_profile_cases()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["passed"], result["cases"])


if __name__ == "__main__":
    unittest.main()
