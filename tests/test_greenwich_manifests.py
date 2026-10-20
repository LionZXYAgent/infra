"""Keep the shared Telegram token separate from update polling and care state."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from ci import deployment
from ci.fixtures import include_suspended_application

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/telegram-bot-suite'


def read(path):
    return yaml.safe_load(path.read_text())


class GreenwichIsolationTests(unittest.TestCase):
    def test_fixture_includes_suspended_object_but_omits_active_object(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'infra/clusters/home'
            source.mkdir(parents=True)
            isolated_root = root / 'isolated/kustomization.yaml'
            isolated_root.parent.mkdir()
            data = read(ROOT / 'clusters/home/telegram-bot-suite.yaml')
            path = source / 'telegram-bot-suite.yaml'
            path.write_text(yaml.safe_dump(data))
            isolated = {'resources': []}
            include_suspended_application(root / 'infra', isolated_root, isolated, 'telegram-bot-suite')
            self.assertEqual(isolated['resources'], ['telegram-bot-suite.yaml'])
            data['spec']['suspend'] = False
            path.write_text(yaml.safe_dump(data))
            isolated = {'resources': []}
            include_suspended_application(root / 'infra', isolated_root, isolated, 'telegram-bot-suite')
            self.assertEqual(isolated['resources'], [])

    def test_deployment_checks_actual_shared_namespace_and_cronjobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'clusters/home'
            path.mkdir(parents=True)
            shutil.copyfile(ROOT / 'clusters/home/telegram-bot-suite.yaml', path / 'telegram-bot-suite.yaml')
            fixture = Mock(infra_path=root)
            runner = deployment.Deployment(root / 'kubeconfig', '192.0.2.10', root / 'report')
            replies = [json.dumps({'spec': {'suspend': True}}), json.dumps({'items': []})]
            with patch.object(runner, 'kube', side_effect=replies) as kube:
                runner.verify_suspended_application(fixture, 'telegram-bot-suite')
            self.assertEqual(kube.call_args.args[0],
                             ['-n', 'acorn-care-bot', 'get', 'pods,deployments,statefulsets,jobs,cronjobs', '-o', 'json'])
            replies[1] = json.dumps({'items': [{'kind': 'CronJob', 'metadata': {'name': 'unexpected'}}]})
            with patch.object(runner, 'kube', side_effect=replies), self.assertRaisesRegex(RuntimeError, 'started workloads'):
                runner.verify_suspended_application(fixture, 'telegram-bot-suite')


if __name__ == '__main__':
    unittest.main()
