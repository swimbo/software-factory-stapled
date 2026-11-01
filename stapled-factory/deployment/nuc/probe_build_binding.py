"""Actual NUC source/signature/artifact negative controls; no model calls."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def signature_command(values):
    return ['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', str(values[9]),
            '-rawin', '-in', str(values[7]), '-sigfile', str(values[8])]


def signature_rejected(error, values):
    output = error.stdout or b''
    if isinstance(output, str):
        output = output.encode()
    return (error.returncode == 1 and error.cmd == signature_command(values)
            and output.strip() == b'Signature Verification Failure')


def run_controls(args, other_image, evidence_root, validate):
    """Keep each call's inputs and private evidence, including failed controls."""
    evidence_root = Path(evidence_root)
    evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='binding-probe-', dir=evidence_root))
    print('binding probe evidence: ' + str(run))

    def check(values, name):
        case = Path(tempfile.mkdtemp(prefix=name + '-', dir=run))
        evidence = case / 'target'
        return validate(*values, evidence_dir=evidence)

    result = check(args, 'positive')
    if not isinstance(result, dict) or result.get('passed') is not True:
        raise AssertionError('positive binding control failed')
    archive = run / 'source.tar'
    archive.write_bytes(b'fake archive')
    diff = run / 'diff'
    diff.write_text('fake diff')
    signature = run / 'build.sig'
    signature.write_bytes(b'forged')
    controls = [
        ('archive', 0, archive, 'archive differs from exact commit'),
        ('diff', 4, diff, 'diff differs from selected commits'),
        ('signature', 8, signature, None),
        ('artifact', 2, other_image, 'signed build artifact mismatch'),
    ]
    for name, index, value, reason in controls:
        values = list(args)
        values[index] = value
        try:
            check(values, name)
        except ValueError as error:
            if reason is None or str(error) != reason:
                raise
        except subprocess.CalledProcessError as error:
            if name != 'signature' or not signature_rejected(error, values):
                raise
        else:
            raise AssertionError(name + ' accepted')
        print(name + ' substitution rejected')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    options = parser.parse_args(argv)
    sys.path.insert(0, str(options.project / 'spine'))
    from prebuilt_validation import validate_prebuilt
    root = options.root
    case = root / 'good'
    att = case / 'attested'
    metadata = json.loads((case / 'metadata.json').read_text())
    image = subprocess.check_output(['docker', 'image', 'inspect', metadata['tag'], '--format', '{{.Id}}'], text=True).strip()
    args = [att / 'source.tar', metadata['revision'], image,
            options.project / 'wall/holdout/scenarios.json', att / 'diff',
            root / 'repo', metadata['baseline'], att / 'build.json',
            att / 'build.sig', root.parent / 'operator-build-public.pem']
    broken = json.loads((root / 'broken/metadata.json').read_text())
    other = subprocess.check_output(['docker', 'image', 'inspect', broken['tag'], '--format', '{{.Id}}'], text=True).strip()
    run_controls(args, other, root / 'binding-probe-evidence', validate_prebuilt)


if __name__ == '__main__':
    main()
