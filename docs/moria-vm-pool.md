# Ekte VM-styring i Moria

Gjeldende prioritet fra 20. oktober 2026: en fast pool med ekte OpenStack-VM-er
som autoskaleren starter og stopper. TimesFM-arbeid venter; eksisterende
shadow-observatør er separat. Skolemonitoreringen skal bevares.

## Oppdaget infrastruktur

SSH til `ubuntu@moriavm` med eksisterende `~/.ssh/acit4410_key` fungerer.
OpenStack 9.0.0 og `/home/ubuntu/moria-openrc.sh` finnes på verten; den eksisterende
application credential brukes der uten å kopieres til Windows eller repoet.
API-listing fungerer. Start/stopp-rettigheter må fortsatt verifiseres i drift.

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
