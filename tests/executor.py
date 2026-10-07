# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless native executor tests; pass this script to stock FreeCADCmd."""
import os
import unittest
import TestAnthracite

if os.environ.get("ANTHRACITE_SMOKE") != "1":
    print("Run through just test with isolated preferences.", flush=True)
    os._exit(1)
result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromModule(TestAnthracite)
)
if not result.wasSuccessful():
    # FreeCAD catches script exceptions; give command-line callers a real failure.
    os._exit(1)
print("ANTHRACITE_EXECUTOR_TESTS_OK", flush=True)
