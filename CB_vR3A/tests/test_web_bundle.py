import hashlib
import json
from pathlib import Path
import re
import unittest
import build_firmware

ROOT = Path(__file__).resolve().parents[1]


class WebBundleTest(unittest.TestCase):
    def test_device_pages_match_shared_sources_and_manifest(self):
        pages = build_firmware.build_web_pages()
        manifest = json.loads((ROOT / 'firmware_build.json').read_text())
        for name, expected in pages.items():
            self.assertEqual((ROOT / name).read_bytes(), expected)
            self.assertEqual(hashlib.sha256(expected).hexdigest(), manifest['web_pages'][name]['sha256'])
            page = expected.decode()
            self.assertIn((ROOT / 'grow.css').read_text(), page)
            self.assertNotRegex(page, r'<(?:script|img|link)[^>]+(?:src|href)="/(?:grow|wifi|icons)')
        self.assertIn((ROOT/'grow.js').read_text(), pages['settings.html'].decode())
        self.assertIn((ROOT/'wifi.js').read_text(), pages['index.html'].decode())
        self.assertIn('const GROW_ICON_URLS = ', pages['settings.html'].decode())
        self.assertIn('data:image/svg+xml;base64,', pages['settings.html'].decode())
