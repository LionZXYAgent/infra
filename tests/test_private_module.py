"""Private module boundaries, using disposable filesystem fixtures."""
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import secretctl


class PrivateModuleTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name).resolve()
        self.ns = self.root / 'infrastructure/namespaces'
        self.ns.mkdir(parents=True)
        (self.ns / 'kustomization.yaml').write_text(
            'apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: []\n')
        stack = self.enterContext(ExitStack())
        for name, value in [('ROOT', self.root), ('SECRET_DIR', self.root / 'secrets'),
                            ('NS_DIR', self.ns)]:
            stack.enter_context(patch.object(secretctl, name, value))

    def test_public_checks_work_without_private_access(self):
        self.assertEqual(secretctl.check_indexes(require_secrets=False), [])

    def test_secret_operations_reject_uninitialized_module(self):
        (self.root / 'secrets').mkdir()
        with self.assertRaisesRegex(ValueError, 'submodule'):
            secretctl.require_private_module()

    def test_public_checks_still_reject_broken_namespace_index(self):
        (self.ns / 'kustomization.yaml').write_text('resources: [missing.yaml]\n')
        with self.assertRaisesRegex(ValueError, 'Index differs'):
            secretctl.check_indexes(require_secrets=False)


if __name__ == '__main__':
    unittest.main()
