"""Keep the inactive suite isolated and route Telegram calls through its gateway."""
import copy
import ipaddress
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/telegram-bot-suite'
SUITE_IMAGE = (
    'ghcr.io/lionzxyorg/acorn-care-bot:sha-f6658c9c51c9396dd19c8f625e533df6e4296812'
    '@sha256:3ed868e07c00d0e9dabc0b5f578102552ab2dd0e8621227b20aecfc843f8a9ba'
)
BUILD = ([shutil.which('kubectl'), 'kustomize'] if shutil.which('kubectl') else
         [shutil.which('kustomize'), 'build'] if shutil.which('kustomize') else None)


def read(path):
    return yaml.safe_load(path.read_text())


def documents(path):
    return [obj for obj in yaml.safe_load_all(path.read_text()) if obj]


def environment(container):
    return {item['name']: item for item in container['env']}


class SuiteManifestTests(unittest.TestCase):
    def setUp(self):
        self.deployment = read(APP / 'deployment.yaml')
        self.spec = self.deployment['spec']
        self.pod = self.spec['template']['spec']
        self.containers = {item['name']: item for item in self.pod['containers']}
        self.volumes = {item['name']: item for item in self.pod['volumes']}
        self.configuration = read(APP / 'kustomization.yaml')
        self.flux = read(ROOT / 'clusters/home/telegram-bot-suite.yaml')

    def mounts(self, name):
        return {item['name']: item for item in self.containers[name]['volumeMounts']}

    def test_two_activation_guards_and_single_writer_strategy(self):
        self.assertIs(self.flux['spec']['suspend'], True)
        self.assertEqual(self.spec['replicas'], 0)
        self.assertLessEqual(self.spec['replicas'], 1)
        self.assertEqual(self.spec['strategy'], {'type': 'Recreate'})
        self.assertEqual([item['name'] for item in self.pod['initContainers']], ['seed-gateway'])
        self.assertNotIn('ephemeralContainers', self.pod)
        self.assertEqual(len(self.pod['containers']), 3)
        self.assertEqual(set(self.containers), {'acorn', 'greenwich', 'gateway'})
        objects = [obj for name in self.configuration['resources'] for obj in documents(APP / name)]
        self.assertEqual([obj['metadata']['name'] for obj in objects if obj['kind'] == 'Deployment'],
                         ['telegram-bot-suite'])
        self.assertEqual({obj['kind'] for obj in objects},
                         {'Deployment', 'PersistentVolumeClaim', 'NetworkPolicy'})

    def test_legacy_init_reads_care_without_token_or_worker_socket_access(self):
        init = self.pod['initContainers'][0]
        self.assertEqual(init['name'], 'seed-gateway')
        self.assertEqual(init['command'], ['python3', '/app/gateway_seed.py'])
        self.assertEqual(init['image'], self.containers['gateway']['image'])
        mounts = {item['name']: item for item in init['volumeMounts']}
        self.assertEqual(mounts, {
            'acorn-state': {'name': 'acorn-state', 'mountPath': '/legacy', 'readOnly': True},
            'gateway-state': {'name': 'gateway-state', 'mountPath': '/gateway'}})
        self.assertEqual(init['securityContext']['runAsUser'], 10001)
        self.assertIs(init['securityContext']['readOnlyRootFilesystem'], True)
        self.assertIs(init['securityContext']['allowPrivilegeEscalation'], False)
        self.assertEqual(init['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertFalse(any('TOKEN' in key or key == 'TELEGRAM_GATEWAY_SOCKET' for key in environment(init)))
        self.assertEqual(environment(init)['GATEWAY_STATE_PATH']['value'],
                         environment(self.containers['gateway'])['GATEWAY_STATE_PATH']['value'])

    def test_suite_does_not_claim_the_live_acorn_configmap_name(self):
        old = read(ROOT / 'apps/acorn-care-bot/kustomization.yaml')
        old_names = {item['name'] for item in old['configMapGenerator']}
        new_names = {item['name'] for item in self.configuration['configMapGenerator']}
        self.assertFalse(old_names & new_names)

    def test_namespace_dependencies_and_orphan_policy(self):
        self.assertEqual(self.deployment['metadata']['name'], 'telegram-bot-suite')
        self.assertEqual(self.spec['selector']['matchLabels'], {'app': 'telegram-bot-suite'})
        self.assertEqual(self.spec['template']['metadata']['labels'], {'app': 'telegram-bot-suite'})
        self.assertEqual(self.configuration['namespace'], 'acorn-care-bot')
        self.assertEqual(self.flux['metadata'], {'name': 'telegram-bot-suite', 'namespace': 'flux-system'})
        flux = self.flux['spec']
        self.assertEqual(flux['path'], './apps/telegram-bot-suite')
        self.assertEqual(flux['targetNamespace'], 'acorn-care-bot')
        self.assertEqual({item['name'] for item in flux['dependsOn']}, {'infra-namespaces', 'cluster-secrets'})
        self.assertEqual(flux['deletionPolicy'], 'Orphan')
        self.assertIs(flux['prune'], True)
        self.assertIs(flux['wait'], True)
        self.assertEqual(flux['sourceRef'], {'kind': 'GitRepository', 'name': 'flux-system'})

    def test_all_containers_use_the_published_immutable_gateway_image(self):
        self.assertNotIn('imagePullSecrets', self.pod)
        for name, container in self.containers.items():
            with self.subTest(container=name):
                self.assertEqual(container['image'], SUITE_IMAGE)
                self.assertEqual(container['imagePullPolicy'], 'IfNotPresent')
                self.assertRegex(container['image'], r'@sha256:[0-9a-f]{64}$')

    def test_distinct_nonroot_uids_with_a_shared_socket_group(self):
        self.assertIs(self.pod['automountServiceAccountToken'], False)
        self.assertIs(self.pod['shareProcessNamespace'], False)
        self.assertNotIn('serviceAccountName', self.pod)
        for field in ('hostNetwork', 'hostPID', 'hostIPC'):
            self.assertFalse(self.pod.get(field, False))
        security = self.pod['securityContext']
        self.assertIs(security['runAsNonRoot'], True)
        self.assertEqual(security['fsGroup'], 10001)
        self.assertEqual(security['fsGroupChangePolicy'], 'OnRootMismatch')
        self.assertEqual(security['seccompProfile'], {'type': 'RuntimeDefault'})
        for name, uid in {'acorn': 10001, 'greenwich': 10002, 'gateway': 10003}.items():
            with self.subTest(container=name):
                container = self.containers[name]
                security = container['securityContext']
                self.assertIs(security['runAsNonRoot'], True)
                self.assertEqual(security['runAsUser'], uid)
                self.assertEqual(security['runAsGroup'], 10001 if name == 'gateway' else uid)
                self.assertIs(security['allowPrivilegeEscalation'], False)
                self.assertIs(security['readOnlyRootFilesystem'], True)
                self.assertFalse(security.get('privileged', False))
                self.assertEqual(security['capabilities'], {'drop': ['ALL']})
                self.assertEqual(security['seccompProfile'], {'type': 'RuntimeDefault'})
                self.assertEqual(container['resources'], {
                    'requests': {'cpu': '10m', 'memory': '32Mi'},
                    'limits': {'cpu': '250m', 'memory': '128Mi'}})
                self.assertNotIn('volumeDevices', container)
        self.assertFalse(any('hostPath' in item or 'projected' in item for item in self.pod['volumes']))

    def test_exact_mount_boundaries_prevent_cross_worker_file_access(self):
        expected = {
            'acorn': {'acorn-state', 'acorn-configuration', 'acorn-socket', 'acorn-tmp'},
            'greenwich': {'greenwich-state', 'greenwich-property', 'greenwich-code',
                         'greenwich-socket', 'greenwich-tmp'},
            'gateway': {'gateway-state', 'telegram-token', 'gateway-registry', 'acorn-socket',
                        'greenwich-socket', 'gateway-tmp'},
        }
        for name, volumes in expected.items():
            with self.subTest(container=name):
                mounts = self.mounts(name)
                self.assertEqual(set(mounts), volumes)
                self.assertEqual(len(mounts), len(self.containers[name]['volumeMounts']))
                self.assertEqual(len({item['mountPath'] for item in mounts.values()}), len(mounts))
                for volume, mount in mounts.items():
                    self.assertIn(volume, self.volumes)
                    self.assertNotIn('subPath', mount)
                    self.assertNotIn('mountPropagation', mount)
                    self.assertNotIn(mount['mountPath'], {'/', '/app', '/run', '/var/run'})
        self.assertFalse(set(self.mounts('acorn')) & set(self.mounts('greenwich')))
        self.assertEqual(set(self.mounts('gateway')) & set(self.mounts('acorn')), {'acorn-socket'})
        self.assertEqual(set(self.mounts('gateway')) & set(self.mounts('greenwich')), {'greenwich-socket'})
        self.assertEqual(set(self.volumes), set().union(*expected.values()))

    def test_gateway_alone_mounts_the_existing_token_secret(self):
        secrets = {name: item['secret'] for name, item in self.volumes.items() if 'secret' in item}
        self.assertEqual(set(secrets), {'telegram-token', 'greenwich-property'})
        self.assertEqual(secrets['telegram-token'], {
            'secretName': 'acorn-care-bot', 'defaultMode': 0o440,
            'items': [{'key': 'bot-token', 'path': 'token'}]})
        self.assertEqual(self.mounts('gateway')['telegram-token'], {
            'name': 'telegram-token', 'mountPath': '/var/run/acorn', 'readOnly': True})
        self.assertEqual(environment(self.containers['gateway'])['TELEGRAM_BOT_TOKEN_FILE']['value'],
                         '/var/run/acorn/token')
        for name, container in self.containers.items():
            with self.subTest(container=name):
                self.assertNotIn('envFrom', container)
                for item in container['env']:
                    self.assertNotIn('secretKeyRef', item.get('valueFrom', {}))
                    self.assertNotEqual(item['name'], 'TELEGRAM_BOT_TOKEN')
                if name != 'gateway':
                    self.assertFalse(any('TOKEN' in key for key in environment(container)))
                    self.assertNotIn('telegram-token', self.mounts(name))

    def test_private_property_and_public_configuration_stay_separate(self):
        self.assertEqual(self.volumes['greenwich-property']['secret'], {
            'secretName': 'greenwich-bin-bot-property', 'defaultMode': 0o440,
            'items': [{'key': 'property.json', 'path': 'property.json'}]})
        for worker, volume, path in (
                ('greenwich', 'greenwich-property', '/etc/bins'),
                ('greenwich', 'greenwich-code', '/opt/bins'),
                ('acorn', 'acorn-configuration', '/etc/acorn'),
                ('gateway', 'gateway-registry', '/etc/gateway')):
            with self.subTest(container=worker, volume=volume):
                self.assertEqual(self.mounts(worker)[volume], {
                    'name': volume, 'mountPath': path, 'readOnly': True})
        self.assertEqual(self.volumes['acorn-configuration']['configMap']['items'],
                         [{'key': 'config.json', 'path': 'config.json'}])
        self.assertEqual(self.volumes['gateway-registry']['configMap']['items'],
                         [{'key': 'registry.json', 'path': 'registry.json'}])

    def test_state_claims_have_one_owner_each_and_new_claims_are_retained(self):
        expected = {'acorn': ('acorn-state', 'acorn-care-bot-state', '/data'),
                    'greenwich': ('greenwich-state', 'greenwich-bin-bot-state', '/data'),
                    'gateway': ('gateway-state', 'telegram-bot-gateway-state', '/gateway')}
        claims = {item['metadata']['name']: item for item in documents(APP / 'storage.yaml')}
        self.assertEqual(set(claims), {'greenwich-bin-bot-state', 'telegram-bot-gateway-state'})
        for owner, (volume, claim, mount_path) in expected.items():
            with self.subTest(container=owner):
                self.assertEqual(self.volumes[volume]['persistentVolumeClaim'], {'claimName': claim})
                self.assertEqual(self.mounts(owner)[volume], {'name': volume, 'mountPath': mount_path})
                self.assertEqual([name for name in self.containers if volume in self.mounts(name)], [owner])
        for claim in claims.values():
            self.assertEqual(claim['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'], 'disabled')
            self.assertEqual(claim['spec']['accessModes'], ['ReadWriteOnce'])
            self.assertEqual(claim['spec']['storageClassName'], 'local-path')
            self.assertEqual(claim['spec']['resources']['requests']['storage'], '128Mi')

    def test_socket_directories_are_separate_and_tmp_is_never_shared(self):
        registry = json.loads((APP / 'registry.json').read_text())
        routes = {worker['id']: worker for worker in registry['workers']}
        for worker in ('acorn', 'greenwich'):
            volume = worker + '-socket'
            self.assertEqual(self.volumes[volume]['emptyDir'], {'medium': 'Memory', 'sizeLimit': '1Mi'})
            self.assertEqual(self.mounts(worker)[volume], {'name': volume, 'mountPath': '/run/telegram', 'readOnly': True})
            gateway_path = self.mounts('gateway')[volume]['mountPath']
            self.assertEqual(gateway_path, '/run/workers/' + worker)
            self.assertEqual(routes[worker]['socket_path'], gateway_path + '/private/api.sock')
            self.assertEqual(environment(self.containers[worker])['TELEGRAM_GATEWAY_SOCKET']['value'],
                             '/run/telegram/private/api.sock')
            self.assertEqual(routes[worker]['uid'], self.containers[worker]['securityContext']['runAsUser'])
        for name in self.containers:
            volume = name + '-tmp'
            self.assertEqual(self.volumes[volume]['emptyDir'], {'sizeLimit': '16Mi'})
            self.assertEqual(self.mounts(name)[volume], {'name': volume, 'mountPath': '/tmp'})
            self.assertEqual([owner for owner in self.containers if volume in self.mounts(owner)], [name])

    def test_entrypoints_and_state_paths_match_the_image_and_runner_contract(self):
        for name, command in {'acorn': ['python3', '/app/service.py'],
                              'greenwich': ['python3', '/opt/bins/runner.py'],
                              'gateway': ['python3', '/app/gateway.py']}.items():
            self.assertEqual(self.containers[name]['command'], command)
            self.assertNotIn('args', self.containers[name])
            env = environment(self.containers[name])
            self.assertEqual(env['PYTHONDONTWRITEBYTECODE']['value'], '1')
            self.assertEqual(env['PYTHONUNBUFFERED']['value'], '1')
        expected = {
            'acorn': {'ACORN_CONFIG_PATH': '/etc/acorn/config.json', 'ACORN_STATE_PATH': '/data/state.json'},
            'greenwich': {'PYTHONPATH': '/app', 'BINS_PROPERTY_PATH': '/etc/bins/property.json',
                         'BINS_STATE_PATH': '/data/state.json', 'BINS_CACHE_PATH': '/data/council-cache.json'},
            'gateway': {'GATEWAY_CONFIG_PATH': '/etc/gateway/registry.json',
                        'GATEWAY_STATE_PATH': '/gateway/private/state.sqlite3', 'GATEWAY_HTTP_PORT': '8081'},
        }
        for name, values in expected.items():
            env = environment(self.containers[name])
            for key, value in values.items():
                self.assertEqual(env[key]['value'], value)
        env = environment(self.containers['acorn'])
        for name in ('TELEGRAM_CHAT_ID', 'TELEGRAM_TOPIC_ID', 'TELEGRAM_ALLOWED_USER_IDS', 'ACORN_FRESH_START'):
            self.assertEqual(env[name]['valueFrom'], {
                'configMapKeyRef': {'name': 'telegram-suite-acorn-config', 'key': name}})

    def test_health_probe_ports_and_paths_match_the_runtime_contract(self):
        for name, port, port_name in (('acorn', 8080, 'acorn-health'), ('gateway', 8081, 'gateway-health')):
            container = self.containers[name]
            self.assertEqual(container['ports'], [{'name': port_name, 'containerPort': port, 'protocol': 'TCP'}])
            for probe, path in (('startupProbe', '/livez'), ('livenessProbe', '/livez'),
                                ('readinessProbe', '/readyz')):
                self.assertEqual(container[probe]['httpGet'], {'path': path, 'port': port_name})
                self.assertNotIn('tcpSocket', container[probe])
        bins = self.containers['greenwich']
        self.assertNotIn('ports', bins)
        for probe in ('startupProbe', 'livenessProbe', 'readinessProbe'):
            self.assertEqual(bins[probe]['exec'], {'command': ['python3', '/opt/bins/runner.py', 'health']})

    def test_configmap_generators_reference_only_local_public_sources(self):
        self.assertEqual(self.configuration['resources'], ['deployment.yaml', 'storage.yaml', 'networkpolicy.yaml'])
        self.assertEqual(self.configuration['configMapGenerator'], [
            {'name': 'telegram-suite-acorn-config', 'envs': ['runtime.properties'], 'files': ['config.json']},
            {'name': 'greenwich-bin-bot-code',
             'files': ['src/bot.py', 'src/council.py', 'src/storage.py', 'src/runner.py']},
            {'name': 'telegram-bot-gateway-registry', 'files': ['registry.json']}])
        self.assertNotIn('secretGenerator', self.configuration)
        self.assertFalse(self.configuration.get('generatorOptions', {}).get('disableNameSuffixHash', False))
        for generator in self.configuration['configMapGenerator']:
            for source in generator.get('files', []) + generator.get('envs', []):
                self.assertFalse(PurePosixPath(source).is_absolute())
                self.assertNotIn('..', PurePosixPath(source).parts)
                self.assertTrue((APP / source).is_file(), f'Missing parent-supplied source: {source}')

    def test_copied_acorn_configuration_preserves_the_approved_plan(self):
        original = ROOT / 'apps/acorn-care-bot'
        for name in ('config.json', 'runtime.properties'):
            with self.subTest(source=name):
                self.assertEqual((APP / name).read_text(), (original / name).read_text())


class SuiteRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads((APP / 'registry.json').read_text())

    def assert_routes(self, registry):
        """Check the public registry contract without importing the bot runtime."""
        self.assertEqual(registry['state_path'], '/gateway/private/state.sqlite3')
        workers = registry['workers']
        self.assertTrue(workers)
        for key in ('id', 'uid', 'socket_path'):
            self.assertEqual(len({worker[key] for worker in workers}), len(workers), key)
        self.assertEqual(len({(worker['chat_id'], worker['topic_id']) for worker in workers}), len(workers))
        for worker in workers:
            self.assertRegex(worker['id'], r'^[a-z][a-z0-9-]*$')
            self.assertIs(type(worker['uid']), int)
            self.assertGreaterEqual(worker['uid'], 10001)
            self.assertNotEqual(worker['uid'], 10003)
            self.assertEqual(worker['socket_path'], '/run/workers/' + worker['id'] + '/private/api.sock')
            self.assertIs(type(worker['chat_id']), int)
            self.assertLess(worker['chat_id'], 0)
            self.assertIs(type(worker['topic_id']), int)
            self.assertGreater(worker['topic_id'], 0)
            self.assertIs(type(worker['receives_updates']), bool)
            self.assertTrue(worker['methods'])
            self.assertEqual(len(set(worker['methods'])), len(worker['methods']))
            self.assertNotIn('*', worker['methods'])
            self.assertEqual('getUpdates' in worker['methods'], worker['receives_updates'])
            if worker['receives_updates']:
                self.assertTrue(worker['allowed_user_ids'])
                self.assertEqual(len(set(worker['allowed_user_ids'])), len(worker['allowed_user_ids']))
                for user in worker['allowed_user_ids']:
                    self.assertIs(type(user), int)
                    self.assertGreater(user, 0)

    def test_routes_use_unique_peer_uids_sockets_and_chat_topics(self):
        self.assert_routes(self.registry)
        workers = {worker['id']: worker for worker in self.registry['workers']}
        self.assertEqual(set(workers), {'acorn', 'greenwich'})
        for name, uid, topic in (('acorn', 10001, 210), ('greenwich', 10002, 45)):
            self.assertEqual(workers[name]['uid'], uid)
            self.assertEqual(workers[name]['chat_id'], -1004424243504)
            self.assertEqual(workers[name]['topic_id'], topic)

    def test_explicit_methods_keep_polling_and_care_permissions_out_of_bins(self):
        workers = {worker['id']: worker for worker in self.registry['workers']}
        self.assertEqual(workers['acorn']['allowed_user_ids'], [474744725, 1235433892])
        self.assertIs(workers['acorn']['receives_updates'], True)
        self.assertEqual(set(workers['acorn']['methods']), {
            'getMe', 'getWebhookInfo', 'getChatMember', 'getUpdates', 'sendMessage',
            'editMessageText', 'answerCallbackQuery'})
        self.assertIs(workers['greenwich']['receives_updates'], False)
        self.assertEqual(set(workers['greenwich']['methods']), {
            'getMe', 'getChatMember', 'sendMessage', 'sendRichMessage', 'pinChatMessage',
            'unpinChatMessage', 'deleteMessage'})
        self.assertNotIn('getUpdates', workers['greenwich']['methods'])
        self.assertEqual(workers['greenwich']['allowed_user_ids'], [])
        self.assertEqual([worker['id'] for worker in workers.values() if worker['receives_updates']], ['acorn'])

    def test_future_module_can_add_a_distinct_registry_route(self):
        extended = copy.deepcopy(self.registry)
        extended['workers'].append({
            'id': 'future', 'uid': 10004, 'socket_path': '/run/workers/future/private/api.sock',
            'chat_id': -1004424243504, 'topic_id': 46, 'receives_updates': False,
            'methods': ['getMe', 'sendMessage'], 'allowed_user_ids': []})
        self.assert_routes(extended)
        self.assertEqual(extended['workers'][:-1], self.registry['workers'])
        self.assertEqual(json.loads(json.dumps(extended)), extended)

    def test_registry_assertions_reject_identity_and_route_collisions(self):
        for key in ('id', 'uid', 'socket_path', 'topic_id'):
            invalid = copy.deepcopy(self.registry)
            invalid['workers'][1][key] = invalid['workers'][0][key]
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.assert_routes(invalid)
        invalid = copy.deepcopy(self.registry)
        invalid['workers'][1]['methods'].append('getUpdates')
        with self.assertRaises(AssertionError):
            self.assert_routes(invalid)


class SuiteNetworkPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = read(APP / 'networkpolicy.yaml')['spec']

    def test_no_ingress_and_only_dns_or_public_https_egress(self):
        self.assertEqual(self.policy['podSelector'], {'matchLabels': {'app': 'telegram-bot-suite'}})
        self.assertEqual(set(self.policy['policyTypes']), {'Ingress', 'Egress'})
        self.assertEqual(self.policy['ingress'], [])
        self.assertEqual(len(self.policy['egress']), 2)
        dns, public = self.policy['egress']
        self.assertEqual(dns, {
            'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                    'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
            'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]})
        self.assertEqual(public['ports'], [{'protocol': 'TCP', 'port': 443}])
        self.assertEqual(len(public['to']), 2)
        self.assertTrue(all(set(peer) == {'ipBlock'} for peer in public['to']))
        self.assertEqual({peer['ipBlock']['cidr'] for peer in public['to']}, {'0.0.0.0/0', '::/0'})
        for peer in public['to']:
            block = peer['ipBlock']
            network = ipaddress.ip_network(block['cidr'])
            for excluded in block['except']:
                self.assertTrue(ipaddress.ip_network(excluded).subnet_of(network))

    def test_public_https_excludes_cluster_private_linklocal_and_multicast_addresses(self):
        blocks = [peer['ipBlock'] for peer in self.policy['egress'][1]['to']]

        def allowed(address):
            address = ipaddress.ip_address(address)
            return any(address in ipaddress.ip_network(block['cidr']) and
                       not any(address in ipaddress.ip_network(item) for item in block['except'])
                       for block in blocks)

        for address in (
                '0.0.0.0', '10.42.0.5', '10.43.0.1', '100.64.0.1', '127.0.0.1',
                '169.254.169.254', '172.16.1.1', '172.31.255.255', '192.168.1.1',
                '224.0.0.1', '239.255.255.250', '255.255.255.255', '::', '::1',
                '::ffff:10.42.0.1', '64:ff9b::a2a:1', 'fc00::1', 'fd00::1', 'fe80::1',
                'fec0::1', 'ff02::1'):
            with self.subTest(blocked=address):
                self.assertFalse(allowed(address))
        for address in ('149.154.167.220', '1.1.1.1', '2606:4700:4700::1111', '2001:4860:4860::8888'):
            with self.subTest(public=address):
                self.assertTrue(allowed(address))


@unittest.skipUnless(BUILD, 'kubectl or kustomize required for offline rendering')
class SuiteRenderTests(unittest.TestCase):
    def render(self, path):
        raw = subprocess.check_output(BUILD + [str(path)])
        return [obj for obj in yaml.safe_load_all(raw) if obj]

    def generated_names(self, objects):
        configs = {obj['metadata']['name']: obj for obj in objects if obj['kind'] == 'ConfigMap'}
        names = {}
        for prefix in ('telegram-suite-acorn-config', 'greenwich-bin-bot-code', 'telegram-bot-gateway-registry'):
            matches = [name for name in configs if name.startswith(prefix + '-')]
            self.assertEqual(len(matches), 1)
            self.assertRegex(matches[0], '^' + prefix + r'-[a-z0-9]+$')
            names[prefix] = matches[0]
        pod = next(obj for obj in objects if obj['kind'] == 'Deployment')['spec']['template']['spec']
        references = [volume['configMap']['name'] for volume in pod['volumes'] if 'configMap' in volume]
        self.assertEqual(set(references), set(names.values()))
        for container in pod['containers']:
            for item in container['env']:
                if 'valueFrom' in item:
                    self.assertEqual(item['valueFrom']['configMapKeyRef']['name'], names['telegram-suite-acorn-config'])
        return names

    def test_rendered_resources_are_private_namespaced_and_inactive(self):
        objects = self.render(APP)
        self.assertEqual(len(objects), 7)
        self.assertEqual({obj['kind'] for obj in objects},
                         {'ConfigMap', 'Deployment', 'PersistentVolumeClaim', 'NetworkPolicy'})
        self.assertTrue(all(obj['metadata']['namespace'] == 'acorn-care-bot' for obj in objects))
        deployment = next(obj for obj in objects if obj['kind'] == 'Deployment')
        self.assertEqual(deployment['spec']['replicas'], 0)
        self.assertEqual(deployment['spec']['strategy'], {'type': 'Recreate'})
        names = self.generated_names(objects)
        configs = {obj['metadata']['name']: obj['data'] for obj in objects if obj['kind'] == 'ConfigMap'}
        self.assertEqual(set(configs[names['telegram-suite-acorn-config']]), {
            'config.json', 'TELEGRAM_CHAT_ID', 'TELEGRAM_TOPIC_ID', 'TELEGRAM_ALLOWED_USER_IDS', 'ACORN_FRESH_START'})
        self.assertEqual(set(configs[names['greenwich-bin-bot-code']]),
                         {'bot.py', 'council.py', 'storage.py', 'runner.py'})
        self.assertEqual(set(configs[names['telegram-bot-gateway-registry']]), {'registry.json'})
        self.assertEqual(json.loads(configs[names['telegram-bot-gateway-registry']]['registry.json']),
                         json.loads((APP / 'registry.json').read_text()))

    def test_future_route_is_generated_and_referenced_only_by_the_gateway(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'application'
            shutil.copytree(APP, target)
            before = self.generated_names(self.render(target))
            registry_path = target / 'registry.json'
            registry = json.loads(registry_path.read_text())
            registry['workers'].append({
                'id': 'future', 'uid': 10004, 'socket_path': '/run/workers/future/private/api.sock',
                'chat_id': -1004424243504, 'topic_id': 46, 'receives_updates': False,
                'methods': ['getMe', 'sendMessage'], 'allowed_user_ids': []})
            registry_path.write_text(json.dumps(registry))
            objects = self.render(target)
            after = self.generated_names(objects)
            name = after['telegram-bot-gateway-registry']
            self.assertNotEqual(name, before['telegram-bot-gateway-registry'])
            for worker_config in ('telegram-suite-acorn-config', 'greenwich-bin-bot-code'):
                self.assertEqual(before[worker_config], after[worker_config])
            config = next(obj for obj in objects if obj['metadata']['name'] == name)
            self.assertEqual(json.loads(config['data']['registry.json']), registry)
            pod = next(obj for obj in objects if obj['kind'] == 'Deployment')['spec']['template']['spec']
            registry_volume = next(volume['name'] for volume in pod['volumes']
                                   if volume.get('configMap', {}).get('name') == name)
            owners = [container['name'] for container in pod['containers']
                      if any(mount['name'] == registry_volume for mount in container['volumeMounts'])]
            self.assertEqual(owners, ['gateway'])

    def test_source_changes_update_only_the_matching_generated_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'application'
            shutil.copytree(APP, target)
            before = self.generated_names(self.render(target))
            for source, generator in (
                    ('config.json', 'telegram-suite-acorn-config'),
                    ('runtime.properties', 'telegram-suite-acorn-config'),
                    ('src/runner.py', 'greenwich-bin-bot-code'),
                    ('registry.json', 'telegram-bot-gateway-registry')):
                path = target / source
                original = path.read_text()
                if source.endswith('.json'):
                    path.write_text(original + '\n')
                elif source == 'runtime.properties':
                    path.write_text(original.replace('TELEGRAM_TOPIC_ID=210', 'TELEGRAM_TOPIC_ID=211'))
                else:
                    path.write_text(original + '\n# offline source change\n')
                after = self.generated_names(self.render(target))
                with self.subTest(source=source):
                    self.assertNotEqual(before[generator], after[generator])
                    for other in before.keys() - {generator}:
                        self.assertEqual(before[other], after[other])
                path.write_text(original)


if __name__ == '__main__':
    unittest.main()
