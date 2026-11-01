import importlib.util
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


class ProbeTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('binding_probe', Path(__file__).with_name('probe_build_binding.py'))
        self.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.probe)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.args = [self.root / 'archive', 'revision', 'good-image', self.root / 'scenarios', self.root / 'diff', self.root / 'repo', 'baseline', self.root / 'build.json', self.root / 'build.sig', self.root / 'key']
        self.calls = []

    def validator(self, *args, evidence_dir=None):
        self.assertIsNotNone(evidence_dir)
        self.assertFalse(evidence_dir.exists())
        self.assertTrue(evidence_dir.parent.is_dir())
        self.assertEqual(stat.S_IMODE(evidence_dir.parent.stat().st_mode), 0o700)
        self.assertNotIn(evidence_dir, self.calls)
        evidence_dir.mkdir(mode=0o700)
        self.calls.append(evidence_dir)
        if args[0] != self.args[0]:
            raise ValueError('archive differs from exact commit')
        if args[4] != self.args[4]:
            raise ValueError('diff differs from selected commits')
        if args[8] != self.args[8]:
            command = ['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', str(args[9]), '-rawin', '-in', str(args[7]), '-sigfile', str(args[8])]
            raise subprocess.CalledProcessError(1, command, output=b'Signature Verification Failure\n')
        if args[2] != self.args[2]:
            raise ValueError('signed build artifact mismatch')
        return {'passed': True}

    def run_probe(self, validator):
        self.probe.run_controls(self.args, 'other-image', self.root / 'evidence', validator)

    def test_controls_have_positive_sentinel_and_retained_private_evidence(self):
        self.run_probe(self.validator)
        self.assertEqual(len(self.calls), 5)
        self.assertTrue(all(path.is_dir() for path in self.calls))
        self.assertTrue(all(path.parent.exists() for path in self.calls))

    def test_unrelated_value_error_cannot_pass(self):
        def invalid(*args, **kwargs):
            if args == tuple(self.args):
                return {'passed': True}
            raise ValueError('durable validation evidence directory required')
        with self.assertRaises(ValueError):
            self.run_probe(invalid)

    def test_bypassed_guard_cannot_pass(self):
        for index in (0, 4, 8, 2):
            with self.subTest(index=index):
                def bypassed(*args, **kwargs):
                    if args[index] != self.args[index]:
                        return {'passed': True}
                    return self.validator(*args, **kwargs)
                with self.assertRaises(AssertionError):
                    self.run_probe(bypassed)
                self.calls.clear()

    def test_failed_positive_control_cannot_pass(self):
        with self.assertRaises(AssertionError):
            self.run_probe(lambda *args, **kwargs: {'passed': False})

    def test_lifecycle_receives_an_unused_child_of_private_case_directory(self):
        def lifecycle_contract(*args, evidence_dir=None):
            self.assertFalse(evidence_dir.exists(), 'candidate lifecycle owns creation of its evidence directory')
            self.assertEqual(stat.S_IMODE(evidence_dir.parent.stat().st_mode), 0o700)
            return self.validator(*args, evidence_dir=evidence_dir)
        self.run_probe(lifecycle_contract)

    def test_unrelated_command_or_signature_config_failure_cannot_pass(self):
        for command, output in [(['docker', 'save'], b'Signature Verification Failure\n'), (None, b'Could not read public key\n')]:
            with self.subTest(command=command):
                def broken(*args, **kwargs):
                    try:
                        return self.validator(*args, **kwargs)
                    except subprocess.CalledProcessError as error:
                        raise subprocess.CalledProcessError(1, command or error.cmd, output=output)
                with self.assertRaises(subprocess.CalledProcessError):
                    self.run_probe(broken)
                self.calls.clear()

    def test_real_openssl_invalid_signature_has_required_provenance(self):
        private = self.root / 'fixture-private.pem'
        public = self.root / 'fixture-public.pem'
        document = self.root / 'manifest'
        signature = self.root / 'signature'
        document.write_bytes(b'operator build fixture')
        commands = [
            ['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(private)],
            ['openssl', 'pkey', '-in', str(private), '-pubout', '-out', str(public)],
            ['openssl', 'pkeyutl', '-sign', '-inkey', str(private), '-rawin',
             '-in', str(document), '-out', str(signature)],
        ]
        for command in commands:
            subprocess.run(command, check=True, capture_output=True, timeout=10)
        values = list(self.args)
        values[7:10] = [document, signature, public]
        command = self.probe.signature_command(values)
        subprocess.run(command, check=True, capture_output=True, timeout=10)
        signature.write_bytes(b'forged')
        with self.assertRaises(subprocess.CalledProcessError) as failure:
            subprocess.run(command, check=True, capture_output=True, timeout=10)
        self.assertTrue(self.probe.signature_rejected(failure.exception, values))
        public.unlink()
        with self.assertRaises(subprocess.CalledProcessError) as config_failure:
            subprocess.run(command, check=True, capture_output=True, timeout=10)
        self.assertFalse(self.probe.signature_rejected(config_failure.exception, values))


if __name__ == '__main__':
    unittest.main()
