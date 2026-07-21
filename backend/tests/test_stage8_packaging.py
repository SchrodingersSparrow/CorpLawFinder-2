"""Stage 8 tests: everything about packaging that can be proven off-Windows.

The installer itself must be built on Windows (documented in
docs/BUILDING.md), but its ingredients are all verifiable here: the icon
generator produces a structurally valid multi-size .ico; the backend
resolves its paths correctly when frozen by PyInstaller; the spec ships the
schema and migrations; and the frozen entry point stays importable.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))


class TestIconGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="lkm-icon-")
        script = PROJECT_ROOT / "frontend" / "build" / "make_icon.py"
        # Run the generator with its output redirected into the temp dir by
        # copying the script there (it writes next to itself).
        target = Path(cls.tmp.name) / "make_icon.py"
        target.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
        subprocess.run([sys.executable, str(target)], check=True,
                       capture_output=True)
        cls.ico = (Path(cls.tmp.name) / "icon.ico").read_bytes()
        cls.png = (Path(cls.tmp.name) / "icon.png").read_bytes()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_ico_directory_structure(self) -> None:
        zero, kind, count = struct.unpack("<HHH", self.ico[:6])
        self.assertEqual((zero, kind), (0, 1))          # ICO magic
        self.assertEqual(count, 5)                       # 256/64/48/32/16
        sizes = set()
        for i in range(count):
            entry = self.ico[6 + 16 * i: 6 + 16 * (i + 1)]
            w, h, _, _, planes, bpp, length, offset = struct.unpack(
                "<BBBBHHII", entry
            )
            sizes.add(w or 256)
            self.assertEqual(bpp, 32)
            self.assertLessEqual(offset + length, len(self.ico))
        self.assertEqual(sizes, {256, 64, 48, 32, 16})

    def test_256_frame_is_png_and_smaller_frames_are_bmp(self) -> None:
        count = struct.unpack("<HHH", self.ico[:6])[2]
        for i in range(count):
            entry = self.ico[6 + 16 * i: 6 + 16 * (i + 1)]
            w, *_, length, offset = struct.unpack("<BBBBHHII", entry)
            blob = self.ico[offset: offset + length]
            if (w or 256) == 256:
                self.assertEqual(blob[:8], b"\x89PNG\r\n\x1a\n")
            else:
                header_size, width, double_height = struct.unpack(
                    "<Iii", blob[:12]
                )
                self.assertEqual(header_size, 40)        # BITMAPINFOHEADER
                self.assertEqual(double_height, width * 2)  # XOR + AND mask

    def test_standalone_png_exists_and_is_256(self) -> None:
        self.assertEqual(self.png[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", self.png[16:24])
        self.assertEqual((width, height), (256, 256))

    def test_repository_copy_matches_generator_output(self) -> None:
        """The committed icon.ico is the generator's output.

        Compared structurally, not byte-for-byte: PNG frames go through
        zlib, whose exact output may differ between platforms/builds, so
        PNG frames are compared as decoded pixels and BMP frames (raw
        bytes, deterministic) exactly.
        """
        import zlib

        def frames(data: bytes) -> dict[int, bytes]:
            count = struct.unpack("<HHH", data[:6])[2]
            out = {}
            for i in range(count):
                entry = data[6 + 16 * i: 6 + 16 * (i + 1)]
                w, *_, length, offset = struct.unpack("<BBBBHHII", entry)
                out[w or 256] = data[offset: offset + length]
            return out

        def png_pixels(png: bytes) -> bytes:
            i, idat = 8, b""
            while i < len(png):
                length = struct.unpack(">I", png[i:i + 4])[0]
                if png[i + 4:i + 8] == b"IDAT":
                    idat += png[i + 8:i + 8 + length]
                i += 12 + length
            return zlib.decompress(idat)

        committed = frames(
            (PROJECT_ROOT / "frontend" / "build" / "icon.ico").read_bytes()
        )
        fresh = frames(self.ico)
        self.assertEqual(set(committed), set(fresh))
        for size, blob in fresh.items():
            if size == 256:
                self.assertEqual(
                    png_pixels(committed[size]), png_pixels(blob)
                )
            else:
                self.assertEqual(committed[size], blob)  # BMP: deterministic


class TestFrozenPaths(unittest.TestCase):
    """defaults.py must resolve bundle paths when PyInstaller-frozen.

    ``sys.frozen`` must exist before the module is imported, so this runs in
    a subprocess that fakes the frozen environment.
    """

    def run_frozen(self, bundle: Path, overrides: dict[str, str]) -> list[str]:
        import os

        code = (
            "import sys, pathlib\n"
            f"sys.frozen = True\n"
            f"sys._MEIPASS = {str(bundle)!r}\n"
            f"sys.path.insert(0, {str(BACKEND_DIR)!r})\n"
            "from app.core import defaults\n"
            "print(defaults.FROZEN)\n"
            "print(defaults.SCHEMA_PATH)\n"
            "print(defaults.MIGRATIONS_DIR)\n"
            "print(defaults.APP_HOME)\n"
        )
        # Full environment (Windows Python needs SystemRoot etc.), with
        # LKM_HOME cleared so only the test's overrides steer the result.
        env = {**os.environ}
        env.pop("LKM_HOME", None)
        env.update(overrides)
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            check=True, env=env,
        )
        return out.stdout.strip().splitlines()

    def test_bundle_relative_schema_and_lkm_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "_internal"
            frozen, schema, migrations, home = self.run_frozen(
                bundle, {"LKM_HOME": str(Path(tmp) / "MyLibrary")},
            )
            self.assertEqual(frozen, "True")
            self.assertEqual(schema, str(bundle / "db" / "schema.sql"))
            self.assertEqual(migrations, str(bundle / "db" / "migrations"))
            self.assertEqual(home, str(Path(tmp) / "MyLibrary"))

    def test_without_lkm_home_falls_back_to_home_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Path.home() reads HOME on Linux and USERPROFILE on Windows —
            # override both so this passes on either build machine.
            _, _, _, home = self.run_frozen(
                Path(tmp) / "_internal", {"HOME": tmp, "USERPROFILE": tmp}
            )
            self.assertEqual(
                home, str((Path(tmp) / "Legal Knowledge Manager").resolve())
            )


class TestPackagingIngredients(unittest.TestCase):
    def test_spec_ships_schema_and_migrations(self) -> None:
        spec = (BACKEND_DIR / "build" / "backend.spec").read_text()
        self.assertIn("schema.sql", spec)
        self.assertIn("migrations", spec)
        self.assertIn("backend_entry.py", spec)
        self.assertIn('name="lkm-backend"', spec)
        self.assertIn("paddle", spec)  # the giant optional stack is excluded

    def test_entry_point_compiles_and_parses_args(self) -> None:
        out = subprocess.run(
            [sys.executable,
             str(BACKEND_DIR / "scripts" / "backend_entry.py"), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("--port", out.stdout)

    def test_electron_builder_config_is_consistent(self) -> None:
        import json

        package = json.loads(
            (PROJECT_ROOT / "frontend" / "package.json").read_text()
        )
        build = package["build"]
        self.assertEqual(build["win"]["icon"], "build/icon.ico")
        self.assertTrue(
            (PROJECT_ROOT / "frontend" / build["win"]["icon"]).is_file()
        )
        extra = build["extraResources"][0]
        self.assertEqual(extra["from"], "../backend-dist/lkm-backend")
        self.assertEqual(extra["to"], "backend")  # where main.cjs looks
        self.assertEqual(package["version"], "1.0.0")

    def test_backend_and_frontend_versions_match(self) -> None:
        from app.core.defaults import APP_VERSION

        import json

        package = json.loads(
            (PROJECT_ROOT / "frontend" / "package.json").read_text()
        )
        self.assertEqual(APP_VERSION, package["version"])


if __name__ == "__main__":
    unittest.main()
