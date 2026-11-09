"""Check resource policy on real pinned chart output and storage helpers."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import migration_check as migration


def containers(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'containers', 'initContainers', 'ephemeralContainers'} and isinstance(child, list):
                yield from child
            else:
                yield from containers(child)
    elif isinstance(value, list):
        for child in value:
            yield from containers(child)


class MigrationResourceTests(unittest.TestCase):
    def test_rendered_applications_and_restore_helpers_have_no_compute_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, contract in migration.contracts():
                with self.subTest(service=name):
                    objects = migration.definitions(ROOT, name)
                    rendered = migration.render(ROOT, name, contract, objects, Path(directory))
                    migration.check_workloads(name, contract, rendered)
                    for helper in sorted((ROOT / 'apps' / name).glob('restore*.yaml')):
                        rendered.extend(doc for doc in yaml.safe_load_all(helper.read_text()) if doc)
                    workloads = list(containers(rendered))
                    self.assertTrue(workloads, 'No rendered containers were inspected')
                    for container in workloads:
                        with self.subTest(container=container['name']):
                            limits = container.get('resources', {}).get('limits') or {}
                            self.assertFalse({'cpu', 'memory'} & set(limits),
                                             'A chart or storage helper adds compute limits')
                            requests = container.get('resources', {}).get('requests') or {}
                            self.assertIsNone(requests.get('memory'),
                                              'A chart or storage helper adds a memory request')
                            self.assertTrue(requests.get('cpu'), 'CPU requests must remain set')


class MemoryResourceTests(unittest.TestCase):
    def assert_no_memory(self, objects):
        inspected = list(containers(objects))
        self.assertTrue(inspected, 'No containers were inspected')
        for container in inspected:
            with self.subTest(container=container['name']):
                resources = container.get('resources') or {}
                for field in ('requests', 'limits'):
                    self.assertIsNone((resources.get(field) or {}).get('memory'),
                                      f'A generated container adds memory {field}')
        return inspected

    def build(self, path):
        output = subprocess.check_output(['kubectl', 'kustomize', str(path)], timeout=60)
        return [obj for obj in yaml.safe_load_all(output) if obj]

    def test_direct_workloads_and_flux_patch_remove_memory_and_keep_cpu(self):
        flux = self.build(ROOT / 'clusters/home/flux-system')
        controllers = self.assert_no_memory([obj for obj in flux if obj['kind'] == 'Deployment'])
        self.assertEqual(len(controllers), 4)
        for container in controllers:
            self.assertIn(container['resources']['requests']['cpu'], ('50m', '100m'))
            self.assertEqual(container['resources']['limits']['cpu'], '1000m')
        for directory in (ROOT / 'apps', ROOT / 'infrastructure', ROOT / 'tests/isolated/demo'):
            for path in directory.rglob('*.yaml'):
                for obj in yaml.safe_load_all(path.read_text()):
                    if obj and obj.get('kind') in {'Pod', 'Deployment', 'StatefulSet', 'DaemonSet', 'Job', 'CronJob'}:
                        with self.subTest(path=path.relative_to(ROOT)):
                            for container in self.assert_no_memory(obj):
                                self.assertTrue(container['resources']['requests']['cpu'])

    def test_pinned_infrastructure_charts_do_not_restore_memory_defaults(self):
        objects = self.build(ROOT / 'infrastructure/controllers')
        sources = {obj['metadata']['name']: obj for obj in objects
                   if obj['kind'] in {'HelmRepository', 'OCIRepository'}}
        for release in (obj for obj in objects if obj['kind'] == 'HelmRelease'):
            spec = release['spec']
            with self.subTest(release=release['metadata']['name']), tempfile.TemporaryDirectory() as directory:
                values = Path(directory) / 'values.yaml'
                values.write_text(yaml.safe_dump(spec['values']))
                args = ['helm', 'template', spec['releaseName']]
                if 'chartRef' in spec:
                    source = sources[spec['chartRef']['name']]['spec']
                    args += [source['url'], '--version', source['ref']['tag']]
                else:
                    chart = spec['chart']['spec']
                    source = sources[chart['sourceRef']['name']]['spec']
                    args += [chart['chart'], '--repo', source['url'], '--version', chart['version']]
                args += ['--namespace', release['metadata']['namespace'], '-f', str(values)]
                result = subprocess.run(args, capture_output=True, check=True, timeout=120)
                self.assert_no_memory(list(yaml.safe_load_all(result.stdout)))
