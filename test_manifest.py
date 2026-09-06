import tempfile
import unittest
from pathlib import Path

from graphrag_core.indexing.manifest import build_manifest, suggest_indexignore_entries


class ManifestTests(unittest.TestCase):
    def test_includes_apple_resource_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            image_contents = project_path / "ios/App/Assets.xcassets/hero.imageset/Contents.json"
            color_contents = project_path / "ios/App/Assets.xcassets/brand.colorset/Contents.json"
            storyboard = project_path / "ios/App/Main.storyboard"
            xib = project_path / "ios/App/HeroView.xib"
            plist = project_path / "ios/App/Info.plist"
            png = project_path / "ios/App/Assets.xcassets/hero.imageset/hero.png"
            pbxproj = project_path / "ios/App.xcodeproj/project.pbxproj"
            xcscheme = project_path / "ios/App.xcodeproj/xcshareddata/xcschemes/App.xcscheme"
            workspace = project_path / "ios/App.xcworkspace/contents.xcworkspacedata"

            image_contents.parent.mkdir(parents=True, exist_ok=True)
            color_contents.parent.mkdir(parents=True, exist_ok=True)
            storyboard.parent.mkdir(parents=True, exist_ok=True)
            pbxproj.parent.mkdir(parents=True, exist_ok=True)
            xcscheme.parent.mkdir(parents=True, exist_ok=True)
            workspace.parent.mkdir(parents=True, exist_ok=True)

            image_contents.write_text("{}", encoding="utf-8")
            color_contents.write_text("{}", encoding="utf-8")
            storyboard.write_text("<storyboard />", encoding="utf-8")
            xib.write_text("<xib />", encoding="utf-8")
            plist.write_text("<plist />", encoding="utf-8")
            png.write_bytes(b"\x89PNG")
            pbxproj.write_text("// !$*UTF8*$!\n", encoding="utf-8")
            xcscheme.write_text("<Scheme />\n", encoding="utf-8")
            workspace.write_text("<Workspace />\n", encoding="utf-8")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("ios/App/Assets.xcassets/hero.imageset/Contents.json", rel_paths)
        self.assertIn("ios/App/Assets.xcassets/brand.colorset/Contents.json", rel_paths)
        self.assertIn("ios/App/Main.storyboard", rel_paths)
        self.assertIn("ios/App/HeroView.xib", rel_paths)
        self.assertIn("ios/App/Info.plist", rel_paths)
        self.assertIn("ios/App.xcodeproj/project.pbxproj", rel_paths)
        self.assertIn("ios/App.xcodeproj/xcshareddata/xcschemes/App.xcscheme", rel_paths)
        self.assertIn("ios/App.xcworkspace/contents.xcworkspacedata", rel_paths)
        self.assertNotIn("ios/App/Assets.xcassets/hero.imageset/hero.png", rel_paths)

    def test_includes_workspace_inside_xcodeproj_package(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            workspace = project_path / "App.xcodeproj/project.xcworkspace/contents.xcworkspacedata"
            workspace.parent.mkdir(parents=True, exist_ok=True)
            workspace.write_text("<Workspace />\n", encoding="utf-8")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("App.xcodeproj/project.xcworkspace/contents.xcworkspacedata", rel_paths)

    def test_required_apple_graph_files_override_indexignore_patterns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            workspace = project_path / "App.xcodeproj/project.xcworkspace/contents.xcworkspacedata"
            pbxproj = project_path / "App.xcodeproj/project.pbxproj"
            asset = project_path / "App/Assets.xcassets/hero.imageset/Contents.json"
            indexignore = project_path / ".indexignore"
            workspace.parent.mkdir(parents=True, exist_ok=True)
            pbxproj.parent.mkdir(parents=True, exist_ok=True)
            asset.parent.mkdir(parents=True, exist_ok=True)
            workspace.write_text("<Workspace />\n", encoding="utf-8")
            pbxproj.write_text("// !$*UTF8*$!\n", encoding="utf-8")
            asset.write_text("{}", encoding="utf-8")
            indexignore.write_text("**/*.xcodeproj/**\n**/*.xcworkspace/**\n**/*.xcassets/**\n", encoding="utf-8")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("App.xcodeproj/project.pbxproj", rel_paths)
        self.assertIn("App.xcodeproj/project.xcworkspace/contents.xcworkspacedata", rel_paths)
        self.assertIn("App/Assets.xcassets/hero.imageset/Contents.json", rel_paths)

    def test_suggest_indexignore_entries_flags_egg_info_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            egg_info = project_path / "demo.egg-info"
            egg_info.mkdir(parents=True, exist_ok=True)
            (egg_info / "PKG-INFO").write_text("metadata", encoding="utf-8")

            suggestions = suggest_indexignore_entries(str(project_path))

        self.assertEqual(
            suggestions,
            [
                {
                    "pattern": "demo.egg-info/**",
                    "reason": "Generated packaging metadata directory not covered by the built-in manifest skip set.",
                }
            ],
        )

    def test_global_skip_defaults_exclude_packaging_and_migration_scaffolding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            (project_path / "demo.egg-info").mkdir(parents=True, exist_ok=True)
            (project_path / "demo.egg-info" / "PKG-INFO").write_text("metadata", encoding="utf-8")
            (project_path / "migrations").mkdir(parents=True, exist_ok=True)
            (project_path / "migrations" / "README").write_text("migration notes", encoding="utf-8")
            (project_path / "migrations" / "script.py.mako").write_text("template", encoding="utf-8")
            (project_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
            (project_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("app.py", rel_paths)
        self.assertNotIn("demo.egg-info/PKG-INFO", rel_paths)
        self.assertNotIn("migrations/README", rel_paths)
        self.assertNotIn("migrations/script.py.mako", rel_paths)
        self.assertNotIn("VERSION", rel_paths)

    def test_global_skip_defaults_exclude_low_value_swift_toolchain_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            (project_path / "Brewfile").write_text("brew 'swift'\n", encoding="utf-8")
            (project_path / "benchmark").mkdir(parents=True, exist_ok=True)
            (project_path / "benchmark" / "StringWalk.swift.gyb").write_text("// template\n", encoding="utf-8")
            (project_path / "stdlib").mkdir(parents=True, exist_ok=True)
            (project_path / "stdlib" / "Swift.swiftinterface").write_text("// interface\n", encoding="utf-8")
            (project_path / "clang").mkdir(parents=True, exist_ok=True)
            (project_path / "clang" / "std.apinotes").write_text("---\n", encoding="utf-8")
            (project_path / "bindings").mkdir(parents=True, exist_ok=True)
            (project_path / "bindings" / "comment-xml-schema.rng").write_text("<rng />\n", encoding="utf-8")
            (project_path / "toolchain").mkdir(parents=True, exist_ok=True)
            (project_path / "toolchain" / "CompatibilityOverride.def").write_text("ENTRY\n", encoding="utf-8")
            (project_path / "test").mkdir(parents=True, exist_ok=True)
            (project_path / "test" / "remote-run.test-sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (project_path / "notes.sil").write_text("// sil\n", encoding="utf-8")
            (project_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("app.py", rel_paths)
        self.assertNotIn("Brewfile", rel_paths)
        self.assertNotIn("benchmark/StringWalk.swift.gyb", rel_paths)
        self.assertNotIn("stdlib/Swift.swiftinterface", rel_paths)
        self.assertNotIn("clang/std.apinotes", rel_paths)
        self.assertNotIn("bindings/comment-xml-schema.rng", rel_paths)
        self.assertNotIn("toolchain/CompatibilityOverride.def", rel_paths)
        self.assertNotIn("test/remote-run.test-sh", rel_paths)
        self.assertNotIn("notes.sil", rel_paths)

    def test_global_skip_defaults_do_not_exclude_real_parsers_source_tree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            (project_path / "parsers").mkdir(parents=True, exist_ok=True)
            (project_path / "parsers" / "unified_parser.py").write_text(
                "class UnifiedParser:\n    pass\n",
                encoding="utf-8",
            )
            (project_path / "parsers" / "query_patterns").mkdir(parents=True, exist_ok=True)
            (project_path / "parsers" / "query_patterns" / "python.py").write_text(
                "def pattern():\n    return 'ok'\n",
                encoding="utf-8",
            )
            (project_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("app.py", rel_paths)
        self.assertIn("parsers/unified_parser.py", rel_paths)
        self.assertIn("parsers/query_patterns/python.py", rel_paths)

    def test_vendor_parser_corpus_shape_is_still_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            (project_path / "parsers").mkdir(parents=True, exist_ok=True)
            (project_path / "parsers" / ".cache_manifest.json").write_text("{}", encoding="utf-8")
            (project_path / "parsers" / "swift" / "src").mkdir(parents=True, exist_ok=True)
            (project_path / "parsers" / "swift" / "src" / "parser.c").write_text(
                "/* generated parser */\n",
                encoding="utf-8",
            )
            (project_path / "crates").mkdir(parents=True, exist_ok=True)
            (project_path / "crates" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("crates/lib.rs", rel_paths)
        self.assertNotIn("parsers/.cache_manifest.json", rel_paths)
        self.assertNotIn("parsers/swift/src/parser.c", rel_paths)

    def test_skips_large_test_cassette_payloads_but_keeps_test_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            (project_path / "tests" / "models" / "cassettes").mkdir(parents=True, exist_ok=True)
            (project_path / "tests" / "unit").mkdir(parents=True, exist_ok=True)
            (project_path / "tests" / "models" / "cassettes" / "big.yaml").write_text(
                "payload: large recorded cassette\n",
                encoding="utf-8",
            )
            (project_path / "tests" / "unit" / "test_api.py").write_text(
                "def test_api():\n    assert True\n",
                encoding="utf-8",
            )
            (project_path / "src").mkdir(parents=True, exist_ok=True)
            (project_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("src/app.py", rel_paths)
        self.assertIn("tests/unit/test_api.py", rel_paths)
        self.assertNotIn("tests/models/cassettes/big.yaml", rel_paths)

    def test_skips_generated_translation_memory_jsonl_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            (project_path / "ui" / "src" / "i18n" / ".i18n").mkdir(parents=True, exist_ok=True)
            (project_path / "ui" / "src").mkdir(parents=True, exist_ok=True)
            (project_path / "ui" / "src" / "app.ts").write_text(
                "export const app = true;\n",
                encoding="utf-8",
            )
            (
                project_path
                / "ui"
                / "src"
                / "i18n"
                / ".i18n"
                / "fr.tm.jsonl"
            ).write_text('{"source":"hello","target":"bonjour"}\n', encoding="utf-8")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("ui/src/app.ts", rel_paths)
        self.assertNotIn("ui/src/i18n/.i18n/fr.tm.jsonl", rel_paths)

    def test_objcpp_mm_files_are_not_globally_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            (project_path / "src").mkdir(parents=True, exist_ok=True)
            (project_path / "src" / "bridge.mm").write_text(
                "#import <Foundation/Foundation.h>\nint answer() { return 42; }\n",
                encoding="utf-8",
            )
            (project_path / "src" / "blob.bin").write_bytes(b"\x00\x01\x02")

            manifest = build_manifest(str(project_path))
            rel_paths = {entry["rel_path"] for entry in manifest}

        self.assertIn("src/bridge.mm", rel_paths)
        self.assertNotIn("src/blob.bin", rel_paths)


if __name__ == "__main__":
    unittest.main()
