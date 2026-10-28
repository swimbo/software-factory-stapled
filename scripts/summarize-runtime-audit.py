#!/usr/bin/env python3
"""Normalize retained Trivy JSON; never suppress findings or grant release approval."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


def summarize(scans, output):
    output.mkdir(parents=True, exist_ok=True)
    inventories, findings, summary = [], [], {}
    for role, path in sorted(scans.items()):
        raw = path.read_bytes()
        scan = json.loads(raw)
        if scan.get('SchemaVersion') != 2 or not isinstance(scan.get('Results'), list):
            raise ValueError('missing or unsupported Trivy scan results')
        results = scan['Results']
        licenses = {}
        for result in results:
            for license in result.get('Licenses', []):
                key = (license.get('PkgName'), license.get('FilePath', ''))
                licenses.setdefault(key, set()).add(license.get('Name', 'UNKNOWN'))
        for result in results:
            for package in result.get('Packages', []):
                inventories.append({'role': role, 'target': result['Target'],
                    'name': package['Name'], 'version': package.get('Version', ''),
                    'path': package.get('FilePath', ''),
                    'licenses': package.get('Licenses') or sorted(licenses.get(
                        (package['Name'], package.get('FilePath', '')), []))})
            for item in result.get('Vulnerabilities', []):
                findings.append({'role': role, 'target': result['Target'],
                    'id': item['VulnerabilityID'], 'severity': item['Severity'],
                    'package': item['PkgName'], 'version': item['InstalledVersion'],
                    'fixed': item.get('FixedVersion', ''), 'path': item.get('PkgPath', ''),
                    'title': item.get('Title', ''), 'url': item.get('PrimaryURL', '')})
        matches = [f for f in findings if f['role'] == role]
        summary[role] = {'scan_sha256': hashlib.sha256(raw).hexdigest(),
            'image_id': scan.get('Metadata', {}).get('ImageID'),
            'packages': sum(len(r.get('Packages', [])) for r in results),
            'license_records': sum(len(r.get('Licenses', [])) for r in results),
            'severity_matches': dict(Counter(f['severity'] for f in matches)),
            'unique_advisories': len({f['id'] for f in matches})}
    (output / 'runtime-package-inventory.json').write_text(json.dumps(inventories, indent=2) + '\n')
    with (output / 'runtime-findings.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(findings[0]) if findings else
            ['role', 'target', 'id', 'severity', 'package', 'version', 'fixed', 'path', 'title', 'url'])
        writer.writeheader()
        writer.writerows(findings)
    (output / 'runtime-scan-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan', action='append', required=True, help='ROLE=JSON_PATH')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    scans = {}
    for value in args.scan:
        role, path = value.split('=', 1)
        if role in scans:
            parser.error('duplicate scan role')
        scans[role] = Path(path)
    print(json.dumps(summarize(scans, args.output), indent=2))
