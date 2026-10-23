# Read-only checks. Never starts/enables services or changes cloud resources.
$ErrorActionPreference = 'Stop'
function Invoke-RemoteCheck($AliasName, $Command, $InputScript) {
    if ($InputScript) {
        $InputScript | & ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 $AliasName $Command
    } else {
        & ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 $AliasName $Command
    }
    if ($LASTEXITCODE -ne 0) { throw "Recovery check failed: $AliasName. Keep autoscaler disabled." }
}
Invoke-RemoteCheck ruby-manager 'hostname; systemctl is-active ssh' $null
Invoke-RemoteCheck ruby-manager 'sudo -n python3 -' @'
import subprocess,pathlib
for unit in ['ruby-acorn.service','ruby-acorn-ml-shadow.service']:
    values=dict(x.split('=',1) for x in subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','-p','UnitFileState','-p','MainPID'],text=True).splitlines())
    assert values=={'MainPID':'0','ActiveState':'inactive','UnitFileState':'disabled'}, unit
assert not list(pathlib.Path('/var/lib/ruby-acorn').rglob('uncertain-create.json')), 'Reconcile uncertain creation manually'
print('Both services remain stopped/disabled; no uncertain-create receipt')
'@
Invoke-RemoteCheck ruby-manager "bash -c 'source /home/ubuntu/pegasus-openrc.sh; cd /opt/ruby-acorn; .venv/bin/python -m scripts.inspect_pegasus --config deploy/pegasus/workers.json'" $null
Invoke-RemoteCheck ruby-manager "bash -c 'source /home/ubuntu/pegasus-openrc.sh; cd /opt/ruby-acorn; .venv/bin/python -'" @'
from types import SimpleNamespace
from scaler.infrastructure.pegasus_controller import connect
conn=None
try:
    conn=connect(SimpleNamespace(cloud='env'))
    assert conn.current_project_id=='234f15222c5e4396851d2713697893b1'
    rows={s.id:s for s in conn.compute.servers(details=True)}
    expected={'97d89bab-df3e-48ab-bf4d-b5f9f4dd7946','65063f7c-5e6f-4c50-9e34-c96c484ee4e1','0f539284-acf1-439b-accd-8b51dddeb329','53e79431-bd91-43d4-8686-dffee4f38063','e92fea6d-7f8b-405a-ab12-fd00ea40c232','1517a4dc-ad07-45da-8fd2-c87307a9f8fb'}
    assert set(rows)==expected, 'Inventory differs from pre-maintenance baseline'
    for s in rows.values():
        assert s.status in ('ACTIVE','SHUTOFF') and not s.task_state, 'Unhealthy or transitioning VM'
    for uid,game in [('65063f7c-5e6f-4c50-9e34-c96c484ee4e1','730'),('97d89bab-df3e-48ab-bf4d-b5f9f4dd7946','570')]:
        s=rows[uid]
        assert s.metadata.get('ruby_acorn_pool')=='course' and s.metadata.get('ruby_acorn_game')==game
        print('Preserved worker',game,uid,s.status,'no task')
except Exception:
    raise SystemExit('OpenStack inventory/state check failed; inspect privately; keep autoscaler disabled')
finally:
    if conn:conn.close()
'@
Invoke-RemoteCheck ruby-monitoring 'sudo -n python3 -' @'
import importlib.util,json,subprocess,urllib.request,urllib.parse
spec=importlib.util.spec_from_file_location('verify','/home/ubuntu/ruby-acorn-monitoring/verify_monitoring.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
info=json.loads(subprocess.check_output(['docker','info','--format','{{json .Swarm}}']))
assert info['LocalNodeState']=='active' and info['ControlAvailable'], 'Swarm manager unhealthy'
services=subprocess.check_output(['docker','service','ls','--format','{{.Name}} {{.Replicas}}'],text=True).splitlines()
assert len(services)==5 and all(x.split()[1]=='1/1' for x in services), 'Swarm replicas unhealthy'
for service,dest,volume in [('prom_prometheus','/prometheus','prom_prometheus_data'),('prom_grafana','/var/lib/grafana','prom_grafana_data')]:
    cid=subprocess.check_output(['docker','ps','--filter','label=com.docker.swarm.service.name='+service,'--format','{{.ID}}'],text=True).strip()
    mounts=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .Mounts}}',cid]))
    assert any(m['Destination']==dest and m.get('Name')==volume for m in mounts), 'Persistent volume missing'
with urllib.request.urlopen('http://127.0.0.1:9090/-/ready',timeout=10) as r:assert r.status==200
up=v.query('up')
for job in ['games','prometheus','node-exporter','cadvisor']:
    rows=[r for r in up if r['metric'].get('job')==job]
    assert rows and all(r['value'][1]=='1' for r in rows), 'Scrape failed: '+job
for appid in ['730','570']:
    selector='steam_player_count{job="games",appid="'+appid+'"}'
    rows=v.query(selector);ages=v.query('time()-timestamp('+selector+')')
    assert len(rows)==len(ages)==1 and float(rows[0]['value'][1])>0 and 0<=float(ages[0]['value'][1])<=120, 'Source stale/invalid: '+appid
headers=v.grafana_headers()
assert v.grafana('/api/health',headers)['database']=='ok'
for uid in ['64nrElFmk','ruby-acorn-ml-shadow','ruby-acorn-pegasus-scaling','ruby-acorn-pegasus-costs','ruby-acorn-pegasus-demand']:
    assert v.grafana('/api/dashboards/uid/'+uid,headers)['dashboard']['uid']==uid
sources=[s for s in v.grafana('/api/datasources',headers) if s['name']=='Prometheus' and s['type']=='prometheus']
assert len(sources)==1
path='/api/datasources/proxy/uid/'+urllib.parse.quote(sources[0]['uid'])+'/api/v1/query?'+urllib.parse.urlencode({'query':'up{job="games"}'})
response=v.grafana(path,headers)
assert response['status']=='success' and response['data']['result'] and all(r['value'][1]=='1' for r in response['data']['result'])
print('Swarm, persistent volumes, source, ordinary targets, five dashboards and Grafana datasource healthy')
print('Autoscaler/ML scrape targets are expected DOWN until separately approved restoration')
'@
Write-Host 'Read-only checks passed. Compare VM UUIDs/states with baseline. Obtain user approval before any restart or enable.'
