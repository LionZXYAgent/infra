"""Exercise authenticated smart HTTP with real Git, never a remote repository."""
import base64
from pathlib import Path
import subprocess
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ci.git_server import GitServer


def git(*args, cwd=None):
    result = subprocess.run(['git', *args], cwd=cwd, capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError('fixture Git command failed; output suppressed')
    return result.stdout


class GitServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.repositories = {}
        for name in ('infra', 'secrets'):
            checkout = self.root / name
            git('init', '--initial-branch=main', str(checkout))
            (checkout / 'marker').write_text(name)
            git('add', '.', cwd=checkout)
            git('-c', 'user.name=CI test', '-c', 'user.email=ci@example.invalid',
                'commit', '-m', 'fixture', cwd=checkout)
            bare = self.root / (name + '.git')
            git('clone', '--bare', str(checkout), str(bare))
            self.repositories[name + '.git'] = (bare, name, name + '-password')
        self.server = self.enterContext(GitServer(self.repositories))

    @staticmethod
    def authorization(user, password):
        return 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()

    def request(self, path, user=None, password=None):
        request = Request(self.server.base_url + path)
        if user:
            request.add_header('Authorization', self.authorization(user, password))
        return urlopen(request, timeout=5)

    def test_clone_and_fetch_succeed(self):
        target = self.root / 'client'
        header = 'http.extraHeader=Authorization: ' + self.authorization('infra', 'infra-password')
        git('-c', header, 'clone', self.server.base_url + '/infra.git', str(target))
        self.assertEqual((target / 'marker').read_text(), 'infra')
        git('-c', header, 'fetch', 'origin', cwd=target)

    def test_missing_wrong_and_cross_repository_credentials_fail(self):
        for user, password in ((None, None), ('secrets', 'wrong'), ('infra', 'infra-password')):
            with self.subTest(user=user):
                with self.assertRaises(HTTPError) as caught:
                    self.request('/secrets.git/info/refs?service=git-upload-pack', user, password)
                self.assertEqual(caught.exception.code, 401)
                caught.exception.close()

    def test_push_cannot_change_remote_ref(self):
        bare = self.repositories['infra.git'][0]
        original = git('--git-dir', str(bare), 'rev-parse', 'main')
        header = 'http.extraHeader=Authorization: ' + self.authorization('infra', 'infra-password')
        result = subprocess.run(['git', '-c', header, 'push',
                                 self.server.base_url + '/infra.git', 'main:other'],
                                cwd=self.root / 'infra', capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(git('--git-dir', str(bare), 'rev-parse', 'main'), original)
        self.assertEqual(git('--git-dir', str(bare), 'for-each-ref', '--format=%(refname)'),
                         b'refs/heads/main\n')

    def test_unknown_paths_traversal_and_push_advertisement_fail(self):
        for path in ('/../secrets.git/info/refs?service=git-upload-pack',
                     '/infra.git/%2e%2e/config', '/infra.git/config',
                     '/infra.git/info/refs?service=git-receive-pack'):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                self.request(path, 'infra', 'infra-password')
            self.assertIn(caught.exception.code, (403, 404))
            caught.exception.close()

    def test_context_exit_stops_server(self):
        server = GitServer({})
        with server:
            self.assertTrue(server.thread.is_alive())
        self.assertFalse(server.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
