"""Exercise independent Python preparation and a version-constrained offline dependency."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim_platform import Platform


def write_probe(root, python):
    source = root / 'probe-source'
    source.mkdir(exist_ok=True)
    (source / 'agent.py').write_text(
        'type Prices = list[float]\nraise RuntimeError("do not execute source")\ndef create_agent(context): return None\n'
    )
    (source / 'agent.json').write_text(json.dumps(dict(python=python)))
    (source / 'requirements.txt').write_text('runtime_probe_dependency==1.0\n')
    wheels = root / 'probe-wheels'
    wheels.mkdir(exist_ok=True)
    dist = 'runtime_probe_dependency-1.0.dist-info'
    files = {
        'runtime_probe_dependency.py': 'raise RuntimeError("do not import dependency")\n',
        dist
        + '/METADATA': 'Metadata-Version: 2.1\nName: runtime_probe_dependency\nVersion: 1.0\nRequires-Python: >=3.12\n',
        dist + '/WHEEL': 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    files[dist + '/RECORD'] = ''.join(name + ',,\n' for name in [*files, dist + '/RECORD'])
    with zipfile.ZipFile(wheels / 'runtime_probe_dependency-1.0-py3-none-any.whl', 'w') as wheel:
        for name, text in files.items():
            wheel.writestr(name, text)
    return source, wheels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--python', default='3.12')
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--archive', type=Path)
    options = parser.parse_args()
    root = options.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    source, wheels = write_probe(root, options.python)
    with Platform(root / 'app') as app:
        profile = app.create_profile('Runtime probe')
        saved = app.import_agent(profile, source)
        job = app.prepare_agent_environment(
            profile,
            saved['agent_id'],
            wheelhouse=wheels,
            allow_python_download=options.download,
            python_archive=options.archive,
        )
        try:
            status = job.wait(300)
            if status['state'] != 'ready':
                raise RuntimeError(str(status))
        finally:
            job.close()
        environment = status['environment']
        executable = (
            root
            / 'app'
            / 'agents'
            / 'environments'
            / environment['environment_ref']
            / 'python'
            / 'bin'
            / 'python'
        )
        actual = subprocess.check_output(
            [str(executable), '-I', '-S', '--version'], text=True
        ).strip()
        second = app.prepare_agent_environment(profile, saved['agent_id'], wheelhouse=wheels)
        try:
            reused = second.wait(30)
            assert reused['state'] == 'ready' and reused['reused'], reused
        finally:
            second.close()
        print(
            json.dumps(
                dict(
                    app_python=sys.version.split()[0],
                    runtime=status['runtime'],
                    actual_environment=actual,
                    reused=reused['reused'],
                    packages=environment['packages'],
                ),
                indent=2,
            )
        )


if __name__ == '__main__':
    main()
