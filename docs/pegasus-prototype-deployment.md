# Pegasus-prototypen: drift og verifikasjon

Dette er teknisk driftsdokumentasjon, ikke tekst til den akademiske rapporten.

## Infrastruktur og avgrensning

Management er `ruby-acorn-management-02` (192.168.45.220, SSH `ruby-manager`).
Monitoring er `ruby-acorn-monitoring-01` (192.168.47.129, SSH `ruby-monitoring`
via management). Eksisterende Prometheus-volum, Grafana-volum, credentials og
SSH-nøkler beholdes. Ingen Moria-avhengighet inngår i denne deployen.

Poolen `course` bruker kun egne nyopprettede VM-er med prosjektsamsvar,
navneformat `ruby-acorn-pegasus-course-APPID-NN` og samtlige metadata:
`ruby_acorn_controller=pegasus`, `ruby_acorn_pool=course`,
`ruby_acorn_game=APPID`. Management-/monitoring-roller avvises også dersom
worker-metadata feilaktig legges til. Ingen eksisterende VM adopteres eller slettes.

Konfigurasjonen bruker Ubuntu 24.04 og minste observerte flavor,
`aem.1c2r.50g` (1 vCPU, 2 GiB RAM, 50 GiB flavor-rootdisk).
Workers får ingen floating IP. SG og nettverk endres ikke.

Før deploy ble fire eksisterende VM-er verifisert ACTIVE: management-02,
monitoring-01, management-01 og pegasus-vm-test-730-01. De brukte 7/12 vCPU,
14336/24576 MiB RAM og 4/10 instanser. To nye workers passer innenfor disse
observerte kvotene. Sju nye workers passer ikke med disse eksisterende ressursene.
Runtime-grensen er derfor **to allokerte workers totalt, én per spill**.
Stoppede workers teller fortsatt mot allokerings- og skykvoter.

## Strategier og kapasitet

`aggressive_capacity` legger som standard til 30 % buffer og én reserveinstans
ved positiv etterspørsel. Ved null etterspørsel gjelder konfigurert minimum.
`cost_optimized` bruker mindre buffer (standard 0), umiddelbar oppskalering
over 110 % modellert utnyttelse og nedskalering først når et lavere mål er
tilstrekkelig under 80 % utnyttelse i 180 sammenhengende sekunder.
Etter databrudd eller restart må nedskaleringsperioden observeres på nytt.

CLI-parametere: `--aggressive-buffer`, `--aggressive-spare`, `--cost-buffer`,
`--scale-up-threshold`, `--scale-down-threshold`, `--downscale-seconds`.
Felles kapasitet styres med `--player-share`, `--players-per-instance`,
`--minimum-instances`, `--max-instances`, `--max-scaling-change` og `--interval`.
API-cooldown og operasjonstimeout ligger i workers.json. Spill velger strategi
via `deploy/pegasus/games.toml`. Gamle `aggressive` og `threshold` bevares.

Normaloppsettet leser `steam_player_count{job="games",appid="..."}` fra
eksisterende Prometheus. Kundens spillerandel er satt til 0.0001 og kapasitet
til 100 spillere per VM; begge er **simulerte antakelser**. Kildens `/health`
bekreftet historisk replay 2026-10-21 (`replay_epoch`, `current_interval`,
`max_interval`, 362 spill). Fersk scrape er ikke et mål på dagens Steam-tall.
Ingen spillserver installeres på workers. ACTIVE beviser VM-status, ikke
spillberedskap eller målt spillerkapasitet.

De lave testgrensene kan få ulike strategier til å ende på samme VM-antall.
Strategiforskjeller dokumenteres med identiske innganger i automatiske tester
og hypotetiske sammenligningsserier, ikke som et kontrollert live kostnadseksperiment.

## Installasjon og autonom drift

`python scripts/package_pegasus.py` lager et kildearkiv under ignorert
`data/pegasus/`. Kjør fra repo-roten. Kopier til management, pakk ut under
`/opt/ruby-acorn`, opprett venv og installer `.[openstack,dev]`. Ubuntu trenger
`python3.12-venv`. Arkivet inneholder ikke data, credentials eller private nøkler.

```bash
cd /opt/ruby-acorn
python3 -m venv .venv
.venv/bin/python -m pip install '.[openstack,dev]'
.venv/bin/python -m pytest -q
source /home/ubuntu/pegasus-openrc.sh
.venv/bin/python scripts/inspect_pegasus.py --config deploy/pegasus/workers.json
```

Eksisterende guest-OpenRC lastes bare av prosessen; verdier kopieres ikke inn
i repo eller servicefil. `cloud=env` velger eksisterende OS-miljøkonfigurasjon.
TLS-verifikasjon, application credentials og riktig prosjekt er påkrevd.
Credentialens token viste `reader` og `member`. Keystone-oppslag av alle
rolletildelinger ga 403 og er ikke nødvendig for vanlig Compute-rekonsiliering.
En vellykket lifecycle-test må i tillegg bekrefte faktisk API-tilgang.

Før kontinuerlig livekontroll, kjør kontrollert demo nedenfor. Installer deretter:

`bash deploy/pegasus/install-management.sh --prepare` forbereder miljøet og
kjører tester/preflight. `--start-live` krever en verifisert demo-live.json
før unit installeres og aktiveres. Kommandoene nedenfor viser de samme driftstrinnene.

```bash
sudo install -d -o ubuntu -g ubuntu -m 700 /var/lib/ruby-acorn
sudo install -m 644 deploy/pegasus/ruby-acorn.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/ruby-acorn.service
sudo systemctl daemon-reload
sudo systemctl enable --now ruby-acorn
systemctl is-enabled ruby-acorn
systemctl is-active ruby-acorn
journalctl -u ruby-acorn --since '-5 minutes' --no-pager
curl http://192.168.45.220:8001/health
```

Systemd starter etter reboot, restarter ved feil og sender SIGTERM ved stopp.
SIGTERM avslutter ved neste pause etter pågående avgrensede operasjoner.
Logger er JSON. Tilstand og historikk lagres under `/var/lib/ruby-acorn`.
En OS-fillås hindrer en ekstra manager med samme pool/tilstandskatalog;
samme prosess kan administrere ulike spill under felles poolgrense.
Kjør aldri flere manager-verter eller alternative state-kataloger mot samme pool.
`systemctl stop ruby-acorn` stopper prosessen, men stopper ikke automatisk workers.

Ved manglende, ugyldige eller for gamle spillerdata beholdes eksisterende
kapasitet; `input_available=0` og alder på siste vellykkede beslutning viser feilen.
Course-deployen bruker også `--reject-zero-player-counts`, fordi kildeauditten
fant ugyldige nullperioder i replay-dataene. Null beholder da kapasiteten og
nullstiller nedskaleringstimeren. Standardklienten og eksplisitte demo-innganger
aksepterer fortsatt reell/testet null. Slå ikke på dette valget for andre kilder
uten samme begrunnelse.
Ingen syntetisk nullverdi brukes som erstatning. API-feil gir feilmetrikker og
ny avlesning. En usikker create låses med `uncertain-create.json`, skrevet og
fsync-et **før** API-kallet. Ikke fjern kvitteringen før manuell kontroll av
faktisk VM-inventar. Kvitteringen blokkerer også andre spill i samme delte pool,
slik at en ennå usynlig VM ikke omgås i kapasitetsbudsjettet.
Prosessrestart etter tidligere handling utløser full cooldown.

Dry-run er standard i Python CLI: utelat `--pegasus-apply`. Live-unitens run.sh
inneholder flagget eksplisitt. Kjør kun dry-run når live-tjenesten er stoppet,
og bruk den samme state-katalogen for å beholde singleton-vernet.

## Deterministisk demonstrasjon

Kjør før normal tjeneste, med null allokerte workers for valgt spill og fersk
rapportsti. Demoskriptet nekter å adoptere en eksisterende worker.

```bash
source /home/ubuntu/pegasus-openrc.sh
cd /opt/ruby-acorn
.venv/bin/python -m scripts.demo_pegasus_scaling \
  --config deploy/pegasus/workers.json \
  --report /var/lib/ruby-acorn/demo-dry-run.json --scrape-wait 0
.venv/bin/python -m scripts.demo_pegasus_scaling \
  --config deploy/pegasus/workers.json \
  --report /var/lib/ruby-acorn/demo-live.json --apply \
  --metrics-host 192.168.45.220
```

Demoen bruker syntetisk etterspørsel 100 → 0 og `DecisionEngine` med grense én
worker. Den verifiserer API-resultater, venter cooldown, lar Prometheus scrape
hver fase, sammenligner alle uadministrerte VM-er før/etter og lagrer UUID,
status, handlinger, varighet og metrikker. Siste ønskede tilstand er SHUTOFF;
ingen VM slettes. Etterpå kan normal drift gjenbruke denne worker-en.

Hvis SSH brytes: les rapport, skyinventar og eventuell uncertain-create-kvittering
før videre handling. **Ikke gjenta create eller bruk ny pool for å omgå usikkerhet.**
Bruk gjerne en avgrenset transient systemd-unit for demoen dersom SSH er ustabil.
`bash deploy/pegasus/inspect-interrupted-demo.sh` samler kun lesende opplysninger
etter gjenopprettet VPN. Skriptet oppretter eller starter ingen VM.

## Eksisterende monitorering

Kopier `deploy/pegasus` til `~/ruby-acorn-monitoring` på monitoring-VM-en.
`bash ~/ruby-acorn-monitoring/install-monitoring.sh` validerer en additiv
Prometheus-kandidat med promtool, bevarer forrige konfigurasjonsfil, legger til
én intern jobb `ruby_acorn_pegasus` mot 192.168.45.220:8001 og sender SIGHUP.
TSDB og containere erstattes ikke. Tre JSON-dashboards kopieres inn i
eksisterende Grafana-provider; eksisterende dashboards/data beholdes.

Dashboard-UID-er: `ruby-acorn-pegasus-demand`, `ruby-acorn-pegasus-scaling`,
`ruby-acorn-pegasus-costs`. `scripts/build_pegasus_dashboards.py` regenererer dem.
De viser kildedata, faktiske VM-statuser, modellert kapasitet, driftsfeil og
estimerte kostnader. EUR 0.10 per kjørende VM-time er en antakelse, ikke en
Pegasus-faktura. Lagring og management/monitoring inngår ikke i kostnadsmodellen;
stoppede disker forblir allokert. Delte fleet-metrikker gjentas per spill og skal
ikke summeres. Strategisammenligning er hypotetisk, ikke alternative live-fleeter.

```bash
sudo python3 ~/ruby-acorn-monitoring/verify_monitoring.py
```

Dette bruker Grafanas dashboard-API og kjører alle paneluttrykk for appid 730
og 570 både direkte mot Prometheus og gjennom Grafanas datasource-proxy.
Eksisterende Grafana-servicecredentials brukes kun i minnet på monitoring-VM-en.
Grafana 13 bruker unified storage; en tom gammel SQLite-dashboardtabell er ikke
bevis på manglende dashboards. Skriptet sjekker også ferske beslutninger,
tilgjengelige innganger, Grafana-databasehelse og scrape-status.
Det erstatter ikke visuell nettleserkontroll.
Bruk eksisterende SSH-rute, eksempelvis fra Windows:

```powershell
ssh -N -L 13000:127.0.0.1:3000 -L 19090:127.0.0.1:9090 ruby-monitoring
```

Grafana blir tilgjengelig på http://127.0.0.1:13000. Ingen nye offentlige
Prometheus-porter eller security group-regler opprettes av deployen.

## Verifikasjonsstatus

Verifisert 2026-10-21 etter at VPN kom tilbake:

- 361 lokale tester besto. På management-VM-en besto 360, med én Windows-spesifikk
  test hoppet over. Fire SDK-deprecation-varsler i begge miljøer.
- Pegasus-preflight, promtool, shell-syntaks og Python-kompilering besto.
- Den opprinnelige live-demoen fullførte 2026-10-20 til tross for SSH-bruddet:
  `65063f7c-5e6f-4c50-9e34-c96c484ee4e1` ble opprettet, nådde ACTIVE og ble
  stoppet til SHUTOFF. Kvitteringen viste to vellykkede handlinger, null API-feil,
  og identiske statuser for de fire beskyttede VM-ene før/etter.
- Normaldrift gjenbrukte denne VM-en for 730 og opprettet bare én ny worker for
  570: `97d89bab-df3e-48ab-bf4d-b5f9f4dd7946`. Begge ble bekreftet ACTIVE.
- `ruby-acorn.service` er enabled og active. En ekstra manager ble avvist av
  OS-låsen. Graceful restart og automatisk recovery etter kontrollert SIGKILL
  ble verifisert; systemd startet prosessen etter 30 sekunder, med uendret
  VM-inventar. Hele management-VM-en ble ikke rebootet.
- Tre dashboards ble bekreftet via Grafanas API. Alle 50 paneluttrykk for to
  spill ga data både direkte og gjennom Grafanas datasource-proxy.
- Windows-verktøyet stoppet visuell kontroll fordi det ikke kunne fastslå
  nettleser-URL. Visuell rendering er derfor ikke sluttkontrollert.

Bevis ligger lokalt under ignorert `data/pegasus/completion-20261021/`, og på
management under `/var/lib/ruby-acorn/`. `demo-live-original.json` er original
demo-kvittering; normaldrift og recovery skal vurderes fra egne kvitteringer.
`final-verification.json` viser uavhengig kontroll 2026-10-21 kl. 10:38:12
Europe/Oslo. `recovery-test.json` dokumenterer feiltesten og `monitoring-final.json`
viser de 50 panelkontrollene og alle fem Prometheus-jobber UP. Sluttforbruk var
6/10 VM-er, 9/12 vCPU og 18432/24576 MiB RAM. Cinder var uendret: to volumer,
100 GiB; ingen av de nye workers har Cinder-vedlegg eller floating IP.
Ikke kjør demoen på nytt som om poolen var tom. Ingen ny create er nødvendig
for å gjenta en verifisert hendelse.

Kravstatus: VM-lifecycle, to algoritmer, kontinuerlig kildelesning, singleton,
grensekontroll, restart, metrikker og dashboarddata er verifisert. Reboot-oppstart
er konfigurert, men en faktisk reboot er ikke testet. Dagens Steam-tall, installert
spillserver, målt spillerkapasitet og faktiske økonomiske besparelser inngår ikke
i det som er bevist. Med én worker per spill kan begge strategier ende på samme
antall; dette er en bevisst liten prototypegrense, ikke en kostnadsevaluering.

Ingen manuell aktivering gjenstår for normal drift. For visuell egenkontroll:
åpne Grafana gjennom SSH-tunnelen og bruk eksisterende innlogging. Etter at den
lokale tunnelen eller VPN avsluttes, må tunnelen åpnes igjen for nettlesertilgang;
autoscaleren fortsetter som systemd-tjeneste på Pegasus uavhengig av lokal SSH.
