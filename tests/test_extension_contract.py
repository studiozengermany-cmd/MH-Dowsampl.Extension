from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"


class ExtensionContractTests(unittest.TestCase):
    def test_every_javascript_id_selector_exists_in_popup_html(self) -> None:
        html = (EXTENSION / "popup.html").read_text(encoding="utf-8")
        html_ids = set(re.findall(r'\bid="([^"]+)"', html))
        selectors: set[str] = set()
        for script_name in ("popup-core.js", "popup-settings.js"):
            script = (EXTENSION / script_name).read_text(encoding="utf-8")
            selectors.update(re.findall(r'querySelector\("#([^"]+)"\)', script))

        self.assertEqual(sorted(selectors - html_ids), [])

    def test_popup_referenced_assets_exist(self) -> None:
        html = (EXTENSION / "popup.html").read_text(encoding="utf-8")
        references = re.findall(r'(?:href|src)="([^"]+)"', html)
        local_references = [
            value
            for value in references
            if not value.startswith(("http://", "https://", "data:"))
        ]
        missing = [value for value in local_references if not (EXTENSION / value).is_file()]
        self.assertEqual(missing, [])

    def test_version_is_synchronized(self) -> None:
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        version = manifest["version"]
        server_source = (ROOT / "backend" / "server_hardening.py").read_text(encoding="utf-8")
        launcher = (ROOT / "START-SERVER.cmd").read_text(encoding="utf-8")

        self.assertIn(f'APP_VERSION = "{version}"', server_source)
        self.assertIn(f"MH-Dowsample Server {version}", launcher)


if __name__ == "__main__":
    unittest.main()
