"""Read-only ML runtime receipt. Run as root on the manager, never as ML code."""
import argparse
import hashlib
import json
from pathlib import Path
import pwd
import subprocess
import time
import urllib.request


def service(name):
    fields = ['ActiveState', 'MainPID', 'NRestarts', 'User', 'MemoryCurrent', 'MemoryPeak',
              'MemoryMax', 'CPUUsageNSec', 'CPUQuotaPerSecUSec', 'ProtectHome',
              'IPAddressDeny', 'IPAddressAllow', 'InaccessiblePaths']
    output = subprocess.check_output(['systemctl', 'show', name,
                                     *['--property='+field for field in fields]], text=True)
    return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-autoscaler-pid', required=True)
    args = parser.parse_args()
    ml, autoscaler = service('ruby-acorn-ml-shadow'), service('ruby-acorn')
    pid = int(ml['MainPID'])
    env = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
    no_credentials = not any(item.split(b'=', 1)[0].startswith(b'OS_') for item in env)
    state = json.loads(Path('/var/lib/ruby-acorn-ml-shadow/status.json').read_text())
    account = pwd.getpwnam('ruby-acorn-ml')
    access = {}
    for path in ['/home/ubuntu/pegasus-openrc.sh', '/opt/ruby-acorn', '/var/lib/ruby-acorn']:
        result = subprocess.run(['nsenter', '--target='+str(pid), '--mount',
                                 '--setuid='+str(account.pw_uid), '--setgid='+str(account.pw_gid),
                                 '--', '/usr/bin/test', '-r', path], capture_output=True)
        if result.returncode not in (0, 1):
            raise RuntimeError('Could not verify ML namespace read restrictions')
        access[path] = result.returncode == 0
    unit = Path('/etc/systemd/system/ruby-acorn.service')
    original = Path('/opt/ruby-acorn/deploy/pegasus/ruby-acorn.service')
    hash_file = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report = {'verified_at': time.time(), 'ml': ml, 'autoscaler': autoscaler,
              'no_openstack_environment': no_credentials, 'ml_read_access': access,
              'autoscaler_unit_matches_original': hash_file(unit) == hash_file(original),
              'autoscaler_unit_sha256': hash_file(unit),
              'status': state, 'meminfo': [line for line in Path('/proc/meminfo').read_text().splitlines()
                                         if line.startswith(('MemTotal:', 'MemAvailable:', 'SwapTotal:'))],
              'model_revision': Path('/var/cache/ruby-acorn-ml-shadow/models--google--timesfm-2.5-200m-pytorch/refs/main').read_text().strip()}
    report['passed'] = (ml['ActiveState'] == autoscaler['ActiveState'] == 'active'
        and autoscaler['MainPID'] == args.expected_autoscaler_pid
        and autoscaler['NRestarts'] == '0' and no_credentials and not any(access.values())
        and report['autoscaler_unit_matches_original']
        and state.get('status') == 'forecast' and time.time()-state['checked_at'] < 120
        and len(state.get('latest', [])) == 6)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
