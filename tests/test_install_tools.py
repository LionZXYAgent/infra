"""Verify release checksum failures prevent tool installation and execution."""
import hashlib
import io
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import unittest


class GitleaksInstallerTests(unittest.TestCase):
    def install(self, correct_checksum):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)
            assets = path / 'assets'
            assets.mkdir()
            operating_system = 'darwin' if sys.platform == 'darwin' else 'linux'
            architecture = 'arm64' if platform.machine() in ('arm64', 'aarch64') else 'x64'
            name = f'gitleaks_8.30.1_{operating_system}_{architecture}.tar.gz'
            archive = assets / name
            raw = b'#!/bin/sh\nprintf "8.30.1\\n"\ntouch "$CI_TOOL_MARKER"\n'
            with tarfile.open(archive, 'w:gz') as bundle:
                item = tarfile.TarInfo('gitleaks')
                item.size = len(raw)
                item.mode = 0o755
                bundle.addfile(item, io.BytesIO(raw))
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest() if correct_checksum else '0' * 64
            (assets / 'gitleaks_8.30.1_checksums.txt').write_text(f'{checksum}  {name}\n')
            fake_bin = path / 'fake-bin'
            fake_bin.mkdir()
            curl = fake_bin / 'curl'
            curl.write_text(f'#!{sys.executable}\nimport os, pathlib, shutil, sys\n'
                            'args=sys.argv[1:]\nurl=next(x for x in args if x.startswith("https://"))\n'
                            'shutil.copyfile(pathlib.Path(os.environ["CI_ASSETS"]) / url.rsplit("/",1)[1], '
                            'args[args.index("-o")+1])\n')
            curl.chmod(0o755)
            home = path / 'home'
            home.mkdir()
            env = os.environ.copy()
            env.update(HOME=str(home), CI_ASSETS=str(assets), CI_TOOL_MARKER=str(path / 'executed'),
                       PATH=str(fake_bin) + os.pathsep + env['PATH'])
            result = subprocess.run(['bash', str(root / 'scripts/install-tools.sh'), 'gitleaks'],
                                    env=env, capture_output=True, timeout=15)
            return result, (home / '.local/bin/gitleaks').exists(), (path / 'executed').exists()

    def test_verified_release_is_installed_and_runs(self):
        result, installed, executed = self.install(True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(installed)
        self.assertTrue(executed)

    def test_bad_checksum_never_installs_or_runs(self):
        result, installed, executed = self.install(False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'SHA-256 verification failed', result.stderr)
        self.assertFalse(installed)
        self.assertFalse(executed)


if __name__ == '__main__':
    unittest.main()
