# Pegasus-vedlikehold 24. oktober 2026

Forberedt 23. oktober 2026, omtrent kl. 21:30 Europe/Oslo. Varslet nedstenging:
24. oktober kl. 09:30 norsk tid. Ingen automatisk gjenoppretting er planlagt.

## Verifisert tilstand

- `ruby-acorn.service` og `ruby-acorn-ml-shadow.service` var begge
  `enabled/active`. Begge er nå `disabled/inactive`, `MainPID=0`, uten
  gjenværende Python-prosesser, systemd-cgroup, drop-ins eller Ruby Acorn-timere.
  ML-stopp ble særskilt godkjent av brukeren. Ingen uncertain-create-kvittering
  ble funnet. Studieenhetene er statiske og ble ikke endret.
- Seks VM-er var `ACTIVE` uten task. Ingen VM ble startet, stoppet, slettet
  eller opprettet. Ingen nettverk, sikkerhetsgrupper eller credentials ble endret.
- Management: `ruby-acorn-management-02`, SSH `ruby-manager`, UUID
  `53e79431-bd91-43d4-8686-dffee4f38063`.
- Monitoring: `ruby-acorn-monitoring-01`, SSH `ruby-monitoring`, UUID
  `0f539284-acf1-439b-accd-8b51dddeb329`. En-node Swarm var Ready/Leader;
  alle fem `prom_*`-tjenester hadde 1/1 replika og uendrede container-ID-er.
- Course-workers: 730 = `65063f7c-5e6f-4c50-9e34-c96c484ee4e1`,
  570 = `97d89bab-df3e-48ab-bf4d-b5f9f4dd7946`. Øvrige to VM-er er
  eldre management-01 og vm-test; de skal bevares uten adopsjon til course.
- Spillestrategier i installert, versjonskontrollert konfigurasjon:
  730 `aggressive_capacity`, 570 `cost_optimized`.
- Prometheus og Grafana bruker henholdsvis `prom_prometheus_data` og
  `prom_grafana_data`. Konfigurasjon/provisioning er bind-mountet fra
  `/home/ubuntu/prometheus`. Grafana databasehelse var `ok`.
- Etter stopp: fire ordinære scrape-targets UP; 8001 og 8003 DOWN som forventet.

## Backup og begrensninger

Privat mappe utenfor Pegasus, Git og OneDrive:
`C:\Users\jsand\RubyAcornBackups\pegasus\maintenance-20261024`.

| Arkiv | Kontrollert innhold |
| --- | --- |
| `ruby-acorn.bundle` | Alle lokale Git-referanser; `git bundle verify` bestått |
| `manager.tar.gz.dpapi` | 519 filer / 107 422 432 ukomprimerte byte: fire installerte kildetrær, fire state-/studiekataloger, units, originale tjenestestatuser og journaler |
| `monitoring.tar.gz.dpapi` | 700 filer / 254 672 945 ukomprimerte byte: TSDB-snapshot inkludert head, Grafana online SQLite-backup og assets, konfigurasjon, Alertmanager-state, fem dashboard-API-eksporter og Swarm-spec/image-referanser |
| `local-experiments.tar.gz.dpapi` | 113 lokale forsøks-/evidensfiler, inkludert tidligere lokale Pegasus-resultater |

Alle krypterte arkiver ble dekryptert og lest tilbake. Alle regulære filer i de
to fjernbackupene ble kontrollert mot SHA-256-manifest; SQLite `integrity_check`
var `ok`. Kvitteringer med arkivhash ligger ved siden av arkivene.
GitHub hadde commit `ed7888786ebd8f18f7b8e82f88e681edd3e18bf4` på feature-branchen.
393 installerte kilde-/unit-versjoner ble funnet i Git-historikken. Ett avvik,
`verify-ml-runtime.py` fra ML-installasjonen, er arkivert som
`maintenance-20261024/verify-ml-runtime-deployed.py`; ingen nye strengliteraler
eller secret-literal-funn sammenlignet med den sporede utgaven. Installerte filer
er ikke oppgradert eller erstattet.

**DPAPI krever samme Windows-brukerprofil på denne PC-en.** Dette er ikke en
portabel passordbackup. Ikke slett/reset profilen før data er gjenopprettet eller
en separat portabel backup er verifisert. Ingen full applikasjonsrestore er øvd.
Backupene er fra forberedelsestidspunktet; senere monitoreringsprøver før selve
nedstengingen er bare på Pegasus. Ingen worker-diskbackup er tatt; denne
prototypen har ikke verifisert installert spillserver eller spillerdata der.

SSH-nøkler og OpenRC ble ikke kopiert til Git eller backupene. Eksisterende
credential-oppsett må fortsatt være tilgjengelig ved recovery. Venv/modelvekter
ble utelatt; kildekode og forsøksresultater er bevart. Sensitiv monitoring-konfig
ligger bare i privat remote staging og det krypterte arkivet. Remote staging og
TSDB-snapshot er beholdt; ingen originaldata eller backup er slettet.

Engangshjelperen og dekrypteringskommandoen ligger i backupmappen. Dekryptering
skriver til en ny fil og må utføres i en privat mappe, aldri i Git/OneDrive:

```powershell
py -3.11 C:\Users\jsand\RubyAcornBackups\pegasus\maintenance-20261024\backup.py decrypt ARKIV.tar.gz.dpapi PRIVAT-NY-FIL.tar.gz
```

## Kjørbar sjekkliste etter vedlikehold

Vent på bekreftet ferdig vedlikehold og fungerende OsloMet VPN. Kjør fra repoet:

```powershell
powershell -NoProfile -File docs/maintenance-20261024/check-recovery.ps1
```

Skriptet kontrollerer SSH, stoppet/deaktivert autoscaler/ML, OpenStack API og
inventar, manglende uncertain-create, Swarm, volum-mounts, Prometheus readiness,
de fire ordinære scrape-targetene, ferske positive kildedata for begge spill,
Grafana og alle fem dashboards. Det starter eller aktiverer ingenting.
Stopp ved feil. Ikke slett kvitteringer, lag erstatnings-VM-er eller endre pool.
Sammenlign UUID-er med backupens `vm-inventory-before.json`. SHUTOFF-workers
skal ikke startes manuelt uten godkjenning; ACTIVE er ikke spillserverberedskap.

**Bare etter ny godkjenning fra brukeren**, og når sjekklisten består, kan
autoscaleren startes manuelt. Aktivering ved boot er en separat godkjent handling.
Opprinnelig ønsket tjenestetilstand var enabled/active for begge tjenester.

```powershell
# KUN etter godkjent gjenoppretting, ikke under vedlikehold:
ssh ruby-manager 'sudo systemctl start ruby-acorn.service'
# Vent minst to beslutningssykluser (omtrent 60 s). Krev enabled-state fortsatt
# disabled inntil eventuell separat godkjenning om oppstartsaktivering.
ssh ruby-manager 'systemctl is-active ruby-acorn.service; curl -fsS http://192.168.45.220:8001/health'
ssh ruby-monitoring 'sudo python3 /home/ubuntu/ruby-acorn-monitoring/verify_monitoring.py'
# Krev bestått rapport, ferske beslutninger, begge inputs tilgjengelige,
# mål 8001 UP og uendret/bundet VM-inventar. Ved feil: stopp ruby-acorn.service.
# Start ML bare etter godkjenning og frisk autoscaler/kilde; kontroller deretter
# 8003 og sudo python3 /home/ubuntu/ruby-acorn-ml-monitoring/verify-ml-monitoring.py.
# Til slutt, bare hvis godkjent:
# ssh ruby-manager 'sudo systemctl enable ruby-acorn.service ruby-acorn-ml-shadow.service'
```

ML-verifikatoren trenger ny gyldig kontekst og faktiske fremtidige utfall;
vedlikeholdsgap skal ikke fylles med syntetiske observasjoner. Ikke krev gamle
live-metoderesultater som bevis på ny drift. Manuell dashboardvisning kan gjøres
gjennom eksisterende SSH-tunnel; ingen offentlige porter må åpnes.
