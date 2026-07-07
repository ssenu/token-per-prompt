import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibrate  # noqa: E402

STATIC = {"pro": 600_000, "max5": 3_000_000, "max20": 12_000_000}


class CalibrationStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_dir = calibrate.DATA_DIR
        calibrate.DATA_DIR = self.tmp

    def tearDown(self):
        calibrate.DATA_DIR = self._orig_dir

    def _write_calib(self, data):
        with open(os.path.join(self.tmp, "calib.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_autocalibrate_off_is_static(self):
        cfg = {"plan": "pro", "autocalibrate": False}
        limit, state = calibrate.calibration_status(cfg, None, STATIC, "s1")
        self.assertEqual(state, "static")
        self.assertEqual(limit, 600_000)

    def test_no_learned_value_is_cold(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        # no calib.json written → no learned limit
        limit, state = calibrate.calibration_status(cfg, None, STATIC, "s1")
        self.assertEqual(state, "cold")
        self.assertEqual(limit, 600_000)

    def test_sane_learned_unconfirmed_is_learned(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        self._write_calib({"limit": 900_000, "samples": 3,
                           "confirmed_session": "other-session"})
        limit, state = calibrate.calibration_status(cfg, None, STATIC, "s1")
        self.assertEqual(state, "learned")
        self.assertEqual(limit, 900_000)

    def test_sane_learned_confirmed_this_session_is_ok(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        self._write_calib({"limit": 900_000, "samples": 2,
                           "confirmed_session": "s1"})
        limit, state = calibrate.calibration_status(cfg, None, STATIC, "s1")
        self.assertEqual(state, "ok")
        self.assertEqual(limit, 900_000)

    def test_insane_learned_falls_back_to_cold(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        # 100k is below the sane band for pro (0.25*600k = 150k) → rejected.
        self._write_calib({"limit": 100_000, "samples": 5,
                           "confirmed_session": "s1"})
        limit, state = calibrate.calibration_status(cfg, None, STATIC, "s1")
        self.assertEqual(state, "cold")
        self.assertEqual(limit, 600_000)

    def test_confirmed_but_no_session_id_is_learned(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        self._write_calib({"limit": 900_000, "samples": 2,
                           "confirmed_session": "s1"})
        limit, state = calibrate.calibration_status(cfg, None, STATIC, None)
        self.assertEqual(state, "learned")
        self.assertEqual(limit, 900_000)


if __name__ == "__main__":
    unittest.main()
