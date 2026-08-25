from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dctl_webui"))
sys.path.insert(0, str(ROOT / "scripts"))

import generate_reference_transfer_dctl as generator
import profile_bridge as bridge


class ProfileBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = json.loads((ROOT / "profiles" / "Mediterranean_Olive.rmatch.json").read_text(encoding="utf-8"))

    def test_example_profile_validates(self) -> None:
        validated = bridge.validate_profile(self.profile)
        self.assertEqual(validated["profile"]["name"], "Mediterranean Olive")
        self.assertEqual(validated["engine"]["minVersion"], bridge.ENGINE_VERSION)

    def test_out_of_range_control_is_rejected(self) -> None:
        self.profile["controls"]["mix"] = 1.5
        with self.assertRaisesRegex(bridge.ProfileValidationError, r"controls\.mix"):
            bridge.validate_profile(self.profile)

    def test_missing_required_calibration_fingerprint_is_rejected(self) -> None:
        del self.profile["calibration"]["reference"]["fingerprint"]
        with self.assertRaisesRegex(bridge.ProfileValidationError, r"calibration\.reference\.fingerprint"):
            bridge.validate_profile(self.profile)

    def test_header_contains_profile_constants_and_defaults(self) -> None:
        header = bridge.header_text(self.profile)
        self.assertIn("__CONSTANT__ float SRC_L = 0.480284535f;", header)
        self.assertIn("#define PROFILE_MIX 0.750000f", header)
        self.assertIn("#define PROFILE_INPUT_ENCODING 1", header)

    def test_generic_engine_uses_profile_header(self) -> None:
        engine = generator.generic_engine_text()
        self.assertIn('#include "ReferenceMatchProfile.h"', engine)
        self.assertIn("PROFILE_HIGHLIGHT_PROTECT", engine)
        self.assertNotIn("__CONSTANT__ float SRC_L =", engine)

    def test_activation_writes_workspace_and_optional_resolve_copy(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            engine = root / "ReferenceMatch.dctl"
            header = root / "ReferenceMatchProfile.h"
            snapshot = root / "ReferenceMatchProfile.active.json"
            resolve = root / "resolve-lut"
            engine.write_text(generator.generic_engine_text(), encoding="utf-8")
            result = bridge.activate_profile(
                self.profile,
                engine_path=engine,
                header_path=header,
                active_snapshot_path=snapshot,
                resolve_lut_dir=resolve,
            )
            self.assertTrue(result["resolveInstalled"])
            self.assertTrue(header.is_file())
            self.assertTrue(snapshot.is_file())
            self.assertTrue((resolve / engine.name).is_file())
            self.assertTrue((resolve / header.name).is_file())


if __name__ == "__main__":
    unittest.main()
