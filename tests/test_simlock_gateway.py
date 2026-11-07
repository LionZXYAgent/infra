"""Simlock gateway uses authenticated public TLS ingress without emulator privileges."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class SimlockGatewayTests(unittest.TestCase):
    def load(self, path):
        return [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]

    def test_gateway_has_persistent_state_without_emulator_devices(self):
        deployment = self.load('apps/simlock/deployment.yaml')[0]
        self.assertEqual(deployment['spec']['replicas'], 1)
        pod = deployment['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertTrue(pod['securityContext']['runAsNonRoot'])
        self.assertNotIn('hostNetwork', pod)
        self.assertNotIn('nodeSelector', pod)
        self.assertEqual({v['name'] for v in pod['volumes']},
                         {'state', 'package', 'configuration', 'tmp'})
        self.assertEqual(next(v for v in pod['volumes'] if v['name'] == 'state')['persistentVolumeClaim']['claimName'],
                         'simlock-gateway-state')
        container = pod['containers'][0]
        self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
        self.assertIn('SIMLOCK_HOME', {e['name'] for e in container['env']})
        self.assertEqual(container['ports'][0]['containerPort'], 4700)
        for workload in pod['initContainers'] + pod['containers']:
            requests = workload['resources']['requests']
            self.assertEqual(requests['cpu'], '50m')
            self.assertNotIn('memory', requests)

    def test_public_https_and_worker_outbound_uplink(self):
        route = self.load('apps/simlock/ingressroute.yaml')[0]
        self.assertEqual(route['kind'], 'IngressRoute')
        self.assertEqual(route['spec']['tls'], {})
        self.assertNotIn('middleware.yaml',
                         self.load('apps/simlock/kustomization.yaml')[0]['resources'])
        self.assertFalse((ROOT / 'apps/simlock/middleware.yaml').exists())
        self.assertEqual(route['spec']['entryPoints'], ['websecure'])
        self.assertEqual(route['spec']['routes'][0]['match'],
                         'Host(`simlock.kulikov.uk`)')
        self.assertNotIn('middlewares', route['spec']['routes'][0])
        self.assertEqual(route['spec']['routes'][0]['services'][0]['port'], 4700)
        self.assertEqual(self.load('apps/simlock/service.yaml')[0]['spec']['type'],
                         'ClusterIP')

    def test_flux_and_namespace_are_registered(self):
        root = self.load('clusters/home/kustomization.yaml')[0]['resources']
        nsroot = self.load('infrastructure/namespaces/kustomization.yaml')[0]['resources']
        self.assertIn('simlock.yaml', root)
        self.assertIn('simlock.yaml', nsroot)
        flux = self.load('clusters/home/simlock.yaml')[0]['spec']
        self.assertEqual(flux['path'], './apps/simlock')
        self.assertTrue({'infra-namespaces', 'infra-tls'}.issubset(
            {x['name'] for x in flux['dependsOn']}))
