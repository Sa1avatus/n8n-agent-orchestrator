#!/usr/bin/env python3
"""Fake ruff: prints a canned C901 JSON report for the file it is handed.

The message shape is the installed ruff 0.17 one ("`count_marks` is too complex (7 > 5)"),
so the parsing test does not depend on the real ruff being present.
"""

import json
import sys

print(
    json.dumps(
        [
            {
                "code": "C901",
                "filename": sys.argv[-1],
                "location": {"row": 1, "column": 1},
                "message": "`count_marks` is too complex (7 > 5)",
            }
        ]
    )
)
