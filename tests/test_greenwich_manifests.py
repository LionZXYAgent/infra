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
APP = ROOT / 'apps/greenwich-bin-bot'


def read(path):
    return yaml.safe_load(path.read_text())


class GreenwichManifestTests(unittest.TestCase):
    def setUp(self):
        self.cron = read(APP / 'cronjob.yaml')
        self.job = self.cron['spec']['jobTemplate']['spec']
        self.pod = self.job['template']['spec']
        self.container = self.pod['containers'][0]
        self.flux = read(ROOT / 'clusters/home/greenwich-bin-bot.yaml')

    def test_preparation_cannot_start_a_second_sender(self):
        self.assertIs(self.flux['spec']['suspend'], True)
        self.assertIs(self.cron['spec']['suspend'], True)
        self.assertEqual(self.flux['spec']['path'], './apps/greenwich-bin-bot')
        self.assertEqual(self.flux['spec']['targetNamespace'], 'acorn-care-bot')
        self.assertEqual(self.flux['spec']['deletionPolicy'], 'Orphan')
        self.assertEqual({x['name'] for x in self.flux['spec']['dependsOn']},
                         {'infra-namespaces', 'cluster-secrets', 'acorn-care-bot'})
        self.assertIn('greenwich-bin-bot.yaml', read(ROOT / 'clusters/home/kustomization.yaml')['resources'])
        self.assertEqual(read(APP / 'kustomization.yaml')['namespace'], 'acorn-care-bot')
        self.assertFalse((ROOT / 'infrastructure/namespaces/greenwich-bin-bot.yaml').exists())

    def test_one_bounded_job_at_monday_noon_london(self):
        self.assertEqual(self.cron['spec']['schedule'], '0 12 * * 1')
        self.assertEqual(self.cron['spec']['timeZone'], 'Europe/London')
        self.assertEqual(self.cron['spec']['concurrencyPolicy'], 'Forbid')
        self.assertEqual(self.cron['spec']['startingDeadlineSeconds'], 3600)
        self.assertEqual(self.job['backoffLimit'], 0)
        self.assertEqual(self.job['activeDeadlineSeconds'], 600)
        self.assertEqual(self.pod['restartPolicy'], 'Never')
        self.assertEqual(self.container['command'], ['python3', '/opt/bins/bot.py', 'run'])
        self.assertNotIn('args', self.container)
        self.assertNotIn('ports', self.container)

    def test_token_reference_is_identical_to_acorn_without_a_copy(self):
        acorn = read(ROOT / 'apps/acorn-care-bot/deployment.yaml')['spec']['template']['spec']
        own = next(x for x in self.pod['volumes'] if x['name'] == 'token')
        existing = next(x for x in acorn['volumes'] if x['name'] == 'token')
        self.assertEqual(own, existing)
        self.assertEqual(own['secret']['secretName'], 'acorn-care-bot')
        self.assertEqual(own['secret']['items'], [{'key': 'bot-token', 'path': 'token'}])
        env = {x['name']: x for x in self.container['env']}
        self.assertEqual(env['TELEGRAM_BOT_TOKEN_FILE']['value'], '/var/run/acorn/token')
        self.assertNotIn('TELEGRAM_BOT_TOKEN', env)
        self.assertFalse(any('envFrom' in c for c in self.pod['containers']))
        token_mount = next(x for x in self.container['volumeMounts'] if x['name'] == 'token')
        self.assertEqual(token_mount, {'name': 'token', 'mountPath': '/var/run/acorn', 'readOnly': True})

    def test_property_stays_in_private_secret_and_never_in_generated_code(self):
        own = next(x for x in self.pod['volumes'] if x['name'] == 'property')
        self.assertEqual(own['secret']['secretName'], 'greenwich-bin-bot-property')
        self.assertEqual(own['secret']['items'], [{'key': 'property.json', 'path': 'property.json'}])
        self.assertNotIn('optional', own['secret'])
        generated = read(APP / 'kustomization.yaml')['configMapGenerator']
        self.assertEqual(generated, [{'name': 'greenwich-bin-bot-code',
                                     'files': ['src/bot.py', 'src/council.py', 'src/storage.py']}])
        self.assertFalse(read(APP / 'kustomization.yaml').get('generatorOptions', {}).get('disableNameSuffixHash', False))
        for path in APP.glob('*.yaml'):
            self.assertFalse(any(obj and obj.get('kind') == 'Secret' for obj in yaml.safe_load_all(path.read_text())))

    def test_independent_retained_state_never_mounts_acorn_data(self):
        pvc = read(APP / 'storage.yaml')
        self.assertEqual(pvc['metadata']['name'], 'greenwich-bin-bot-state')
        self.assertEqual(pvc['spec']['accessModes'], ['ReadWriteOnce'])
        self.assertEqual(pvc['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'], 'disabled')
        claims = [x['persistentVolumeClaim']['claimName'] for x in self.pod['volumes'] if 'persistentVolumeClaim' in x]
        self.assertEqual(claims, ['greenwich-bin-bot-state'])
        self.assertNotIn('acorn-care-bot-state', json.dumps(self.pod))
        self.assertFalse(any('hostPath' in x for x in self.pod['volumes']))

    def test_nonroot_readonly_job_has_no_cluster_access(self):
        self.assertIs(self.pod['automountServiceAccountToken'], False)
        self.assertEqual(self.pod['securityContext']['runAsUser'], 10001)
        self.assertIs(self.pod['securityContext']['runAsNonRoot'], True)
        self.assertEqual(self.pod['securityContext']['seccompProfile']['type'], 'RuntimeDefault')
        self.assertIs(self.container['securityContext']['readOnlyRootFilesystem'], True)
        self.assertIs(self.container['securityContext']['allowPrivilegeEscalation'], False)
        self.assertEqual(self.container['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertRegex(self.container['image'], r'^docker.io/library/python:3\.12-slim-bookworm@sha256:[0-9a-f]{64}$')
        self.assertEqual(self.container['resources']['limits']['memory'], '128Mi')
        self.assertFalse(any('service' in path.name or 'ingress' in path.name for path in APP.glob('*.yaml')))

    @unittest.skipUnless(shutil.which('kubectl'), 'kubectl required')
    def test_rendered_code_reference_matches_hash_and_namespace(self):
        objects = [x for x in yaml.safe_load_all(subprocess.check_output(['kubectl', 'kustomize', str(APP)])) if x]
        self.assertEqual({x['kind'] for x in objects}, {'ConfigMap', 'CronJob', 'PersistentVolumeClaim'})
        self.assertTrue(all(x['metadata']['namespace'] == 'acorn-care-bot' for x in objects))
        config = next(x for x in objects if x['kind'] == 'ConfigMap')
        self.assertEqual(set(config['data']), {'bot.py', 'council.py', 'storage.py'})
        self.assertRegex(config['metadata']['name'], r'^greenwich-bin-bot-code-[a-z0-9]+$')
        cron = next(x for x in objects if x['kind'] == 'CronJob')
        volumes = cron['spec']['jobTemplate']['spec']['template']['spec']['volumes']
        self.assertEqual(next(x['configMap']['name'] for x in volumes if 'configMap' in x), config['metadata']['name'])

    @unittest.skipUnless(shutil.which('kubectl'), 'kubectl required')
    def test_code_changes_update_generated_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'application'
            shutil.copytree(APP, target)
            def render_name():
                objects = list(yaml.safe_load_all(subprocess.check_output(['kubectl', 'kustomize', str(target)])))
                config = next(x for x in objects if x['kind'] == 'ConfigMap')
                cron = next(x for x in objects if x['kind'] == 'CronJob')
                volumes = cron['spec']['jobTemplate']['spec']['template']['spec']['volumes']
                self.assertEqual(next(x['configMap']['name'] for x in volumes if 'configMap' in x), config['metadata']['name'])
                return config['metadata']['name']
            before = render_name()
            code = target / 'src/bot.py'
            code.write_text(code.read_text() + '\n# offline change\n')
            self.assertNotEqual(before, render_name())


class GreenwichIsolationTests(unittest.TestCase):
    def test_fixture_includes_suspended_object_but_omits_active_object(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'infra/clusters/home'
            source.mkdir(parents=True)
            isolated_root = root / 'isolated/kustomization.yaml'
            isolated_root.parent.mkdir()
            data = read(ROOT / 'clusters/home/greenwich-bin-bot.yaml')
            path = source / 'greenwich-bin-bot.yaml'
            path.write_text(yaml.safe_dump(data))
            isolated = {'resources': []}
            include_suspended_application(root / 'infra', isolated_root, isolated, 'greenwich-bin-bot')
            self.assertEqual(isolated['resources'], ['greenwich-bin-bot.yaml'])
            data['spec']['suspend'] = False
            path.write_text(yaml.safe_dump(data))
            isolated = {'resources': []}
            include_suspended_application(root / 'infra', isolated_root, isolated, 'greenwich-bin-bot')
            self.assertEqual(isolated['resources'], [])

    def test_deployment_checks_actual_shared_namespace_and_cronjobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'clusters/home'
            path.mkdir(parents=True)
            shutil.copyfile(ROOT / 'clusters/home/greenwich-bin-bot.yaml', path / 'greenwich-bin-bot.yaml')
            fixture = Mock(infra_path=root)
            runner = deployment.Deployment(root / 'kubeconfig', '192.0.2.10', root / 'report')
            replies = [json.dumps({'spec': {'suspend': True}}), json.dumps({'items': []})]
            with patch.object(runner, 'kube', side_effect=replies) as kube:
                runner.verify_suspended_application(fixture, 'greenwich-bin-bot')
            self.assertEqual(kube.call_args.args[0],
                             ['-n', 'acorn-care-bot', 'get', 'pods,deployments,statefulsets,jobs,cronjobs', '-o', 'json'])
            replies[1] = json.dumps({'items': [{'kind': 'CronJob', 'metadata': {'name': 'unexpected'}}]})
            with patch.object(runner, 'kube', side_effect=replies), self.assertRaisesRegex(RuntimeError, 'started workloads'):
                runner.verify_suspended_application(fixture, 'greenwich-bin-bot')


if __name__ == '__main__':
    unittest.main()
