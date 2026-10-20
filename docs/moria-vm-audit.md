# Skrivebeskyttet VM-gjennomgang, 20. oktober 2026

Brukeren ba om å vurdere nødvendige VM-er og muligheter for større pool, med
uttrykkelig ønske om å bevare eksisterende arbeid. Ingen VM-er eller tjenester
ble startet, stoppet, slettet, rekonfigurert eller innlemmet i poolen under audit.
Bare lokal dokumentasjon og ignorerte auditrapporter er skrevet.

## Bekreftet inventar og vurdering

Omfang: OpenStack-prosjektet som den eksisterende OpenRC-tilgangen på MORIAVM
autentiserer mot. Andre prosjekter og Pegasus er ikke kartlagt.

| VM | IP | Status ved kontroll | Funn | Vurdering |
| --- | --- | --- | --- | --- |
| MORIAVM | 192.168.1.180 | ACTIVE | SSH/Tailscale, OpenStack CLI og eksisterende autentiseringsoppsett; brukes av live-autoskaleren. Ingen kjørende Docker-containere. | Nødvendig for dagens tilgang og styring. Behold. |
| ruby-acorn-pool-01 | 192.168.1.229 | ACTIVE | Den eksplisitt eide VM-en i aktiv pool. | Nødvendig som skaleringstarget. Behold. |
| prometheus-vm | 192.168.1.179 | ACTIVE | Fem kjørende Swarm-tjenester: Prometheus, Grafana, Alertmanager, node-exporter, cAdvisor. Vedvarende Docker-volumer. | Separat skolemonitorering, ikke avhengighet for lokal Ruby Acorn. Behold data og tjenester. |
| internalvm | 192.168.1.212 | ACTIVE | FastAPI-container på port 10080, ingen container-mounts eller Docker-volumer; Git-repo uten lokale endringer. | Ingen funnet Ruby Acorn-avhengighet. Kandidat til senere gjenbruk hvis øvelsen er ferdig og innhold er sikret. Ikke erklært trygg å slette. |
| moiravm | 192.168.1.196 | SHUTOFF | Eldre VM fra 2. september, egen UUID og nøkkelpar keyname. Ingen tilknyttet Cinder-disk. Rotdiskens innhold er ikke undersøkt. | Uavklart. Behold stoppet til separat godkjent inspeksjon/sikring. |

`MORIAVM` og `moiravm` er forskjellige VM-er, ikke alternative skrivemåter for
samme ressurs. `manager`, `my-first-vm`, `pegasus-base-vm` og `moria-base-vm`
fantes ikke som VM-navn i denne prosjektlisten. `manager` finnes som nøkkelpar
og brukes av pool-VM-en; et nøkkelpar er ikke en VM. Fravær her beviser ikke
at navnene aldri ble brukt eller ikke finnes i et annet prosjekt.

Skole-Prometheus hadde fire aktive scrape-targets: cadvisor, games-exporter,
node-exporter og prometheus, alle UP. Ingen target pekte på internalvm.
At ingen avhengighet ble funnet i disse oppsettene utelukker ikke bruk i andre
kursøvelser eller fra eksterne klienter. Ingen Cinder-volumer ble listet i
prosjektet; alle fem VM-er har likevel 20 GiB rot-disk. Ingen separate volumer
betyr derfor ikke at en VM er uten data. En container uten mounts kan også ha
data i sitt skrivbare lag.

## Kvoter og trygg utvidelse

| Ressurs | Nåværende VM-størrelser samlet | Kvote | Med to nye acit-small |
| --- | ---: | ---: | ---: |
| VM-er | 5 | 5 | 7 |
| vCPU | 6 | 10 | 8 |
| RAM (MiB) | 8192 | 10240 | 10240 |

Tallene summerer registrerte VM-størrelser, inkludert den stoppede VM-en; de
beskriver ikke faktisk CPU-belastning eller tilgjengelig fysisk kapasitet på
Moria. To nye små VM-er passer nominelt innenfor gjeldende CPU/RAM-kvoter,
men trenger instanskvote minst 7 og tilgjengelig kapasitet på vertene.
Instanskvote 6 er tilstrekkelig for én ny VM og poolstørrelse 2.

Anbefalt førstevalg: be fagansvarlig/Moria-administrator om instanskvote 7 for
en dedikert pool på 3 VM-er, med eksisterende maskiner bevart. Ikke endre
kvoter selv eller bestill mer kapasitet uten instruksjon. Hvis kvoten ikke kan
økes, er internalvm en mulig gjenbrukskandidat; moiravm krever innholdsinspeksjon
før vurdering. Gjenbruk krever godkjent plan, bevaring av innhold og avklaring
av eventuelle gjenværende fagkrav. Sletting er ikke nødvendig for å utvide en
fast pool ved godkjent gjenbruk.

Å slå av en VM fjerner den ikke fra OpenStack. Det frigjør derfor ikke i seg
selv en plass til en ny VM i den eksisterende listen på fem instanser.
Se [OpenStack: User-Facing Operations](https://docs.openstack.org/operations-guide/ops-user-facing-operations.html).

## Forbedringer etter avklart pool

1. Pool på 2–3 VM-er gir mulighet til å verifisere flere faktiske identiteter og
   grensen på to endringer per beslutning. Dette krever ny konfigurasjon og test.
2. Tilpass og dokumenter demoens last-/kapasitetsantakelser: dagens globale
   Steam-tall ga allerede rått behov 12. Et tak på 3 vil fortsatt kunne være
   mettet hele tiden. Flere VM-er alene gir da ikke dynamisk opp/nedskalering.
   Bruk egne kontrollerte lasttester for bevis, og hold dem tydelig atskilt fra
   reelle Steam-beslutninger. Ikke presenter antatt spillerkapasitet som målt.
3. Legg til en liten tjeneste og readiness-sjekk; hypervisorstatus ACTIVE er ikke
   bevis på at en applikasjon er klar til å motta last.
4. Gjør vertskjøringen robust med automatisk oppstart, prosesseierskap/lås,
   varsling ved gamle beslutninger og målt oppstartstid. Dette er foreslått arbeid.
5. TimesFM vurderes etter at reaktiv flermaskinstyring og kapasitetsmodellen er
   dokumentert; ingen ny modelltrening eller deployment inngikk i audit.

## Evidens og begrensninger

Ignorerte lokale rapporter: `data/moria/vm-audit-inventory.json`,
`vm-audit-management.json`, `vm-audit-guests.json`,
`vm-audit-dependencies.json` og `vm-audit-internal-git.json`.
Git-status på internalvm ble lest med et kommandospesifikt safe.directory og
GIT_OPTIONAL_LOCKS=0; permanent Git-konfigurasjon ble ikke endret.
Ingen nøkkelinnhold, OpenRC-verdier eller applikasjonshemmeligheter ble lest ut.
Tidligere kryptert backup av skolemonitoreringen finnes; full gjenoppretting
er ennå ikke testet. Audit er ikke en ny backup og autoriserer ingen opprydding.
