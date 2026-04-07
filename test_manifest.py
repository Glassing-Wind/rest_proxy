import tempfile
import unittest
from pathlib import Path

from graphrag_core.indexing.manifest import build_manifest


class ManifestTests(unittest.TestCase):
    def test_includes_apple_resource_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            image_contents = project_path / "ios/App/Assets.xcassets/hero.imageset/Contents.json"
            color_contents = project_path / "ios/App/Assets.xcassets/brand.colorset/Contents.json"
            storyboard = project_path / "ios/App/Main.storyboard"
            xib = project_path / "ios/App/HeroView.xib"
            png = project_path / "ios/App/Assets.xcassets/hero.imageset/hero.png"
            pbxproj = project_path / "ios/App.xcodeproj/project.pbxproj"

            image_contents.parent.mkdir(parents=True, exist_ok=True)
            color_contents.parent.mkdir(parents=True, exist_ok=True)
            storyboard.parent.mkdir(parents=True, exist_ok=True)
            pbxproj.parent.mkdir(parents=True, exist_ok=True)

            image_contents.write_text("{}", encoding="utf-8")
            color_contents.write_text("{}", encoding="utf-8")
            storyboard.write_text("<storyboard />", encoding="utf-8")
            xib.write_text("<xib />", encoding="utf-8")
            png.write_bytes(b"\x89PNG")
            pbxproj.write_text("// !$*UTF8*$!\n", encoding="utf-8")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("ios/App/Assets.xcassets/hero.imageset/Contents.json", rel_paths)
        self.assertIn("ios/App/Assets.xcassets/brand.colorset/Contents.json", rel_paths)
        self.assertIn("ios/App/Main.storyboard", rel_paths)
        self.assertIn("ios/App/HeroView.xib", rel_paths)
        self.assertIn("ios/App.xcodeproj/project.pbxproj", rel_paths)
        self.assertNotIn("ios/App/Assets.xcassets/hero.imageset/hero.png", rel_paths)


if __name__ == "__main__":
    unittest.main()
