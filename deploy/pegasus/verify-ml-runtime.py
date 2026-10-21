"""Read-only ML runtime receipt. Run as root on the manager, never as ML code."""
import argparse
import hashlib
import json
import math
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
    directory = Path('/var/lib/ruby-acorn-ml-shadow')
    events = [json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
    forecasts = [e for e in events if e['event'] == 'forecast']
    key = lambda e: (e['origin_timestamp'], e['model'], e['horizon_minutes'])
    proposals = {key(e): e for e in forecasts}
    metadata = json.loads((directory/'metadata.json').read_text())['config']
    history = json.loads((directory/'source-history.json').read_text())
    actuals = {row['timestamp']: row['count'] for row in history['data']}
    errors = {}
    verified_outcomes = True
    for event in events:
        if event['event'] != 'outcome':
            continue
        forecast = proposals[key(event)]
        actual = actuals[event['target_timestamp']]
        error = forecast['prediction']-actual
        verified_outcomes &= (event['actual'] == actual and actual > 0
            and math.isclose(event['absolute_error'], abs(error))
            and event['target_timestamp'] == event['origin_timestamp']+event['horizon_minutes']*60)
        errors.setdefault(event['model']+':'+str(event['horizon_minutes']), []).append(error)
    for name, values in errors.items():
        score = state['scores'][name]
        verified_outcomes &= (score['count'] == len(values)
            and math.isclose(score['mae'], sum(abs(x) for x in values)/len(values))
            and math.isclose(score['rmse'], math.sqrt(sum(x*x for x in values)/len(values))))
    report['journal_audit'] = {'forecast_count': len(forecasts),
        'unique_forecast_count': len(proposals), 'scored_outcomes': {k: len(v) for k, v in errors.items()},
        'outcomes_match_source_and_scores': bool(verified_outcomes),
        'origins_published_before_issue': all(e['origin_timestamp'] <= history['data_epoch']
            +math.floor((e['issued_at']-metadata['replay_epoch'])/300)*300 for e in forecasts)}
    report['passed'] = (ml['ActiveState'] == autoscaler['ActiveState'] == 'active'
        and autoscaler['MainPID'] == args.expected_autoscaler_pid
        and autoscaler['NRestarts'] == '0' and no_credentials and not any(access.values())
        and report['autoscaler_unit_matches_original']
        and len(forecasts) == len(proposals) and verified_outcomes
        and report['journal_audit']['origins_published_before_issue']
        and state.get('status') == 'forecast' and time.time()-state['checked_at'] < 120
        and len(state.get('latest', [])) == 6)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
