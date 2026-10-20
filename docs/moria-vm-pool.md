# Ekte VM-styring i Moria

Gjeldende prioritet fra 20. oktober 2026: en fast pool med ekte OpenStack-VM-er
som autoskaleren starter og stopper. TimesFM-arbeid venter; eksisterende
shadow-observatør er separat. Skolemonitoreringen skal bevares.

## Aktiv live-styring og Grafana

Brukeren ba om neste steg med live spillertall og Grafana. Den separate
vertsprosessen er nå aktiv og VM-en er **ACTIVE**. Startet 20. oktober kl. 14:51
norsk tid med samme poolfil, reaktiv kapasitet, appid 730, minimum 1, maksimum 1
og høyst 2 endringer per beslutning. Den leser live Steam-data via lokal
Prometheus på port 9090. Første vellykkede beslutning kl. 14:52 brukte 1015537
globale spillere og startet den ene VM-en fra 0 til 1. En påfølgende beslutning
bekreftet 1 -> 1 uten ny skalering. Med minimum lik maksimum vil normale
variasjoner i spillertall ikke stoppe VM-en. Rått modellbehov var 12, mens
pooltaket tillater 1; dette vises som kapasitetsbegrensning.

Metrics bindes til `127.0.0.1:8002`; Docker Desktop når dette via
`host.docker.internal:8002`. Jobben `moria_vm_autoscaler` prefikser alle innsamlede
metrics med `moria_` og legger til `appid=730` / `pool=ruby-acorn`.
Eksisterende dashboards fortsetter dermed å bruke fake-målingene.
Lokal Prometheus ble reloaded med SIGHUP etter bestått promtool-konfigurasjonssjekk.

[Ruby Acorn Moria VMs](http://127.0.0.1:3000/d/ruby-acorn-moria-vms) har 17 paneler.
Grafana-API-et bekreftet provisioning; alle 19 panelspørringer ga gyldige,
ikke-tomme resultater. Alle fire Prometheus-mål var UP. Visuell nettleserkontroll
var ikke tilgjengelig. Ingen spillserver/readiness-sjekk er installert.

`data/moria/live-process.json` registrerer launcher/worker PID og loggfiler
(`live-*.stderr.log` / `stdout.log`). Python-worker var 5252 ved oppstart;
verifiser prosessidentitet og ferske beslutninger hver sesjon. Kvitteringen er
ikke en heartbeat. Verten starter ikke prosessen automatisk etter omstart.
Live observasjoner skrives separat til `data/moria/live-player-history.csv`.
`data/moria/live-monitoring-verification.json` bevarer verifiserte panelspørringer.
TimesFM-prognoser leses ikke av denne kjøringen og skolemonitoreringen endres ikke.

Ved nødvendig stopp: verifiser worker-PID/kommando, stopp bare denne
autoskalerprosessen, og les deretter VM-status. Prosess-stopp stopper ikke VM-en.
Start ikke en ny manager før den gamle er bekreftet avsluttet. Ikke gjenkjør
demonstrasjonen mot en pool som eies av den aktive live-prosessen.

## Tidligere kontrollert driftstest 20. oktober 2026

Brukeren godkjente forslaget nedenfor, og opprettelse og test er gjennomført.
`ruby-acorn-pool-01` fikk UUID `d41249d4-8b13-49d8-956c-0d95b12e4bc7` og
IP `192.168.1.229`. Konsollen bekreftet ferdig cloud-init kl. 14:40 norsk tid.
Etter første stopp kjørte `demo_openstack_scaling.py` samme DecisionEngine og
reaktive kapasitetsstrategi som hovedløkken med syntetiske spillertall 100000 og 0.
Demoen brukte minimum 0, maksimum 1 og endringsgrense 2; vanlig live minimum er 1.

Resultat: **SHUTOFF → ACTIVE → SHUTOFF**, med bekreftede sluttilstander og samme
VM-ID i begge beslutninger. Testen var ferdig kl. 14:44. Den nye VM-en står stoppet.
Sluttkontroll kl. 14:45 bekreftet at alle fire opprinnelige VM-er beholdt tilstanden.
Dette beviser ekte VM-start/stopp, men ikke spillserverberedskap eller automatisk
skalering fra reelle spillertall. På dette tidspunktet var kontinuerlig styring ikke aktivert.

Lokale bevis (ignorert av Git):

- `data/moria/pool.json`: poolkonfigurasjon uten hemmeligheter.
- `data/moria/creation.json`, `first-boot.json`, `initial-stop.json` og
  `final-verification.json`: opprettelse, boot og sluttkontroll.
- `demo-results/moria-vm-start-stop-2026-10-20.json`: full beslutningsrapport.

Alle fem instansplasser er nå brukt, inkludert stoppede VM-er. En større pool
krever økt kvote eller særskilt godkjent gjenbruk. Ingen eksisterende VM-er,
nettverksregler eller skolemonitorering er endret. Forslaget under beholdes som
dokumentasjon av det som ble godkjent; ikke opprett samme VM på nytt.

## Oppdaget infrastruktur

SSH til `ubuntu@moriavm` med eksisterende `~/.ssh/acit4410_key` fungerer.
OpenStack 9.0.0 og `/home/ubuntu/moria-openrc.sh` finnes på verten; den eksisterende
application credential brukes der uten å kopieres til Windows eller repoet.
API-listing og start/stopp av den nye eide VM-en er verifisert i drift.

Compute-kvote: 5 VM-er, 10 vCPU og 10240 MiB RAM. Fire VM-er finnes:
`MORIAVM`, `internalvm`, `prometheus-vm` og stoppet `moiravm`.
Stoppede VM-er finnes fortsatt i prosjektet; det er bare én ledig instansplass.
Ingen av disse fire er godkjent som skaleringstarget.

## Konkret forslag som krever godkjenning

Opprett én dedikert VM `ruby-acorn-pool-01`, `acit-small` (1 vCPU, 1024 MiB RAM,
20 GiB disk), image `ubuntu-26.04-moria`, eksisterende nett `ACIT4410`, nøkkelpar
`manager`, eksisterende sikkerhetsgruppe `default`, ingen floating IP.
Eksisterende nettverk, sikkerhetsregler, VM-er og skolemonitorering endres ikke.
Kommandoen skal kjøres i et autentisert skall på Moria etter godkjenning:

```bash
openstack server create --wait \
  --image 610ccd74-d019-4d25-b1d1-1ac870ee0af0 \
  --flavor 138d36ed-9d53-4845-94a7-1f4857418b9b \
  --network 0dd7a091-6e66-408b-93f3-3524f9a09558 \
  --key-name manager --security-group 46f06a1c-40d4-4adc-be0a-6c4a70210248 \
  --property ruby_acorn_pool=ruby-acorn --property ruby_acorn_game=730 \
  ruby-acorn-pool-01
```

Registrer den returnerte UUID-en i en lokal `data/moria/pool.json`, basert på
`openstack-pool.example.json`. Vent på fullført første oppstart/cloud-init og
stopp bare den nye VM-en. Demonstrer så `0 -> 1 -> 0` gjennom DecisionEngine,
med ekte VM-ID-er og verifiserte sluttilstander i JSON. VM-en beholdes stoppet.
Ingen sletting er del av denne testen. En pool på 2–3 krever økt instanskvote
eller separat godkjenning av gjenbruk av eksisterende VM-er.

## Lokal kode og kjøring

`OpenStackController` bruker kun UUID-ene i poolfilen og sjekker begge
eierskapsfeltene før operasjoner. Den bruker `server start` / `server stop`,
venter på ACTIVE/SHUTOFF og feiler ved tidsavbrudd, ERROR eller feil eierskap.
Overgangstilstander blokkerer nye sykluser. ACTIVE er hypervisorstatus, ikke
bevis på at et spill eller en tjeneste er klar. Spillserverinstallasjon og
readiness-sjekker er ikke implementert.

Les status uten å endre VM-er:

```powershell
.\.venv-ml\Scripts\python.exe scripts/demo_openstack_scaling.py --pool data/moria/pool.json
```

Etter godkjenning av start/stopp, kjør demonstrasjonen:

```powershell
.\.venv-ml\Scripts\python.exe scripts/demo_openstack_scaling.py --pool data/moria/pool.json --apply --report demo-results/moria-vm-start-stop.json
```

Kjør først enkeltbeslutninger mot lokal live Steam/Prometheus etter godkjent test:

```powershell
.\.venv-ml\Scripts\python.exe main.py --controller openstack --openstack-pool data/moria/pool.json --label appid=730 --max-instances 1 --max-scaling-change 2 --once
```

Kontinuerlig vertsdrift kan bruke samme kommando uten `--once`, med
`--metrics-host 127.0.0.1 --metrics-port 8002` for å unngå Compose-porten.
Dette aktiveres separat etter den kontrollerte testen; lokal Compose beholder
fake-kontrolleren. Bare én prosess skal eie en pool, også på tvers av maskiner.
Avslutning av løkken lar VM-ene stå i sin siste tilstand. En delvis feil kan
etterlate noen VM-er startet/stoppet; kontroller faktisk status før videre arbeid.
VM-pool støtter foreløpig bare ett eksplisitt appid per prosess.
`AUTOSCALER_OPENSTACK_POOL` er miljøalternativ til CLI-flagget.
Kostnadstall er fortsatt demoanslag, ikke faktisk Moria-fakturering.

Offisiell kommandoreferanse:
[OpenStack server](https://docs.openstack.org/python-openstackclient/latest/cli/command-objects/server.html).
