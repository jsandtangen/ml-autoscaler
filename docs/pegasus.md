# Pegasus / OpenStack

## Status og avgrensning

2026-10-20: Pegasus svarer med OpenStack Horizon på
`https://pegasus.sky.oslomet.no/project/` (videresending til `/auth/login/`).
En uautentisert GET mot `https://pegasus.sky.oslomet.no:5000/v3/` ga HTTP 200
og Identity-versjonsinformasjon. Dette bekrefter Horizon og et standard
Identity API, men **ikke** prosjektets Compute-tilgang, tillatelser eller kvoter.
Oppgitt kvote på ti VM-er er ennå ikke bekreftet gjennom autentisert API.

Implementasjonen er lokalt testet med mockede skyresponser og OpenStack SDK
4.21.0. Ingen Pegasus-VM er opprettet, startet, stoppet eller slettet.
Eksisterende Moria-prosess, VM-er og Compose er ikke endret eller startet på nytt.
Branchen `feature/pegasus-integration` var allerede aktiv og arbeidskopien ren
ved oppstart; eksisterende branch ble beholdt. Ingenting er pushet eller flettet.

## Autentisering og nettverk: manuelle steg

1. Velg riktig prosjekt i Pegasus. Noter prosjekt-ID fra prosjektinformasjon
   eller den nedlastbare konfigurasjonen. Bruk aldri Moria-konfigurasjon som
   erstatning for Pegasus-konfigurasjon.
2. Se etter **Identity → Application Credentials → Create Application Credential**
   i Horizon. Lag en egen credential for Ruby Acorn med nødvendig prosjektrolle
   for lesing og server create/start/stop, gjerne med utløpsdato. La
   `unrestricted` være avslått. Dette oppretter en autentiseringsressurs;
   gjør steget selv. Ikke send ID/secret eller innloggingspassord til chatten.
3. Last ned eller lag `clouds.yaml` og eventuelt `secure.yaml` **utenfor repoet**.
   Bruk Horizon sin API Access/konfigurasjonsnedlasting for korrekt auth URL,
   region og interface. Eksempel nedenfor inneholder bare plassholdere.
4. Hvis menyen er skjult, SSO-brukeren ikke kan lage application credentials,
   eller API svarer 401/403: be faglærer/Pegasus-administrator aktivere dette
   eller gi en offisielt støttet prosjektbundet tjenesteidentitet. Web-SSO og
   nettlesercookies gir ikke automatisk SDK-autentisering. Denne prototypen
   støtter kun application credentials; et annet alternativ må avklares først.
5. Kjør fra OsloMet-nettverket eller godkjent VPN dersom katalogens tjenester
   ikke er tilgjengelige hjemmefra. Horizon og Identity var tilgjengelige fra
   arbeidsmaskinen ved kontrollen; det er ennå ukjent om Compute, Image og
   Network krever VPN eller et internt kjøremiljø. En intern maskin må nå alle
   endepunktene og ha egen beskyttet konfigurasjon. Ikke slå av TLS-verifisering.
   Ved intern CA: oppgi institusjonens godkjente CA-fil via `cacert`.

Eksempel på ekstern `clouds.yaml`:

```yaml
clouds:
  pegasus:
    auth_type: v3applicationcredential
    auth:
      auth_url: https://REPLACE_WITH_OFFICIAL_IDENTITY_ENDPOINT/v3
      application_credential_id: REPLACE_LOCALLY
      application_credential_secret: REPLACE_LOCALLY
    region_name: REPLACE_WITH_OFFICIAL_REGION
    interface: public
    identity_api_version: 3
    verify: true
```

Beskytt filen med brukerens filrettigheter. SDK kan også bruke `secure.yaml`
for secret-delen. Ikke legg hemmeligheter i kommandolinjen, JSON-poolfilen,
logger eller Git. Ikke aktiver SDK HTTP-debuglogging.
Application credentials er OpenStacks mekanisme for apper uten brukerpassord:
[Keystone-dokumentasjon](https://docs.openstack.org/keystone/latest/user/application_credentials.html).
Se også [SDK-konfigurasjon](https://docs.openstack.org/openstacksdk/latest/user/config/configuration.html).

## Konfigurasjon og lesende kontroll

Installer i et fungerende Python 3.11+-miljø. Kommandoene under bruker det
isolerte testmiljøet som ble opprettet under arbeidet; `.venv` kunne ikke kjøre
pytest fordi `unicodedata` manglet. Det eksisterende miljøet ble ikke reparert
eller erstattet.

```powershell
$python = 'data/pegasus-test-venv/Scripts/python.exe'
# Ved nytt miljø: python -m pip install -e '.[dev,openstack]'
$env:OS_CLIENT_CONFIG_FILE = 'C:\PRIVATE\openstack\clouds.yaml'
Copy-Item pegasus.example.json data/pegasus.json
```

Rediger `data/pegasus.json` med faktiske ID-er for prosjekt, aktivt image,
flavor og eksisterende nettverk, samt eksisterende keypair og security groups.
Finn dem i Horizon. Keypair-navnet er ikke en privat nøkkel; backend trenger
ikke SSH-nøkkelen. Ikke lag nettverk eller åpne security rules automatisk.
`maximum_instances` er som standard 6 og kan bare settes til 1–6.

```powershell
& $python scripts/inspect_pegasus.py --config data/pegasus.json
```

Dette gjør bare GET/lesing: autentisering og prosjektkontroll, VM-inventar,
eierskapskontroll, image/nettverk/keypair/security group og Compute-kvote for
én ekstra VM. Ingen rå SDK-responser, tokens eller servermetadata skrives ut.
Ved feil: kontroller lokalt auth URL, region, prosjekt, credential-utløp,
API-roller, VPN og ressurs-ID-er. Ikke send rå HTTP-feillogger med hemmeligheter.
Null ledig opprettelseskapasitet gjør forhåndskontrollen mislykket selv om
lesetilgang og start/stopp av en eksisterende pool kan virke.

Deretter en tørr beslutningssyklus fra eksisterende Prometheus:

```powershell
& $python main.py --controller pegasus --pegasus-config data/pegasus.json --label appid=730 --max-instances 6 --once
```

Tørrkjøring er standard hver gang. `--pegasus-apply` må gis eksplisitt på CLI;
ingen miljøvariabel kan slå det på. Planen logges, skytilstanden forblir virkelig,
og ingen vellykket VM-handling telles i tørrkjøring. Manglende API-tilgang
erstattes aldri med simulerte VM-er.

## Beslutninger, sikkerhet og begrensninger

`DecisionEngine` og eksisterende strategier beholdes. Kapasitetsregelen bruker
fortsatt 100 spillere/VM, global spillerandel 0.001, headroom 0.10 og minimum 1;
threshold/aggressive kan fortsatt velges eksplisitt. Pegasus har maksimum 6
og høyst 2 endringer per syklus som standard. Global Steam-telling er ikke
det samme som kunder på denne serverpoolen.

Navn må ha formen `ruby-acorn-pegasus-POOL-APPID-NN`, tilhøre konfigurert prosjekt
og ha alle metadatafeltene `ruby_acorn_controller=pegasus`,
`ruby_acorn_pool=POOL`, `ruby_acorn_game=APPID`. Navn alene gir aldri eierskap.
Navnekollisjon eller duplikat stanser kontrollen. Andre VM-er telles kun i
prosjektkvoten og blir aldri endret.

Backend starter stoppede eide VM-er før nye opprettes. Total eiet pool,
inkludert stoppede VM-er, begrenses til seks. Opprettelse bruker image-basert
boot uten separate volumes, floating IP eller cloud-init-spillinstallasjon.
Compute-grensene for instances, vCPU og RAM kontrolleres før hver opprettelse,
inkludert prosjektets øvrige og stoppede VM-er. Nova/Neutron håndhever sine
kvoter på serveren ved samtidige endringer; nettverks-/portkvote eller fysisk
kapasitetsmangel kan fortsatt gi feil. Ingen kvoter endres.

Operasjoner venter på ACTIVE/SHUTOFF uten task state, med 300 sekunders
ventefrist og 15 sekunders HTTP-timeout. Den samlede ventetiden kan overstige
fristen med varigheten til siste API-kall. Automatiske HTTP-retries er avslått.
BUILD, ERROR eller pågående task sperrer nye endringer. En 120 sekunders pause
etter endringer reduserer stop/start-pendling. Pausen er lokal for prosessen og
nullstilles ved omstart; ingen vedvarende hysterese er implementert.

Før create skrives en varig `uncertain-create.json` under state-katalogen.
Ved tapt svar eller tidsavbrudd beholdes denne og sperrer videre endringer,
også etter omstart. Kontroller aktuell VM, navn, metadata og task i Horizon.
Fjern kvitteringen manuelt **først etter** at det er avklart om opprettelsen
lyktes eller feilet. ERROR-VM-er må håndteres manuelt. Backend sletter aldri.

Én manager må eie prosjekt/pool/appid. En lokal OS-lås sperrer to prosesser med
samme state-katalog, også tørrkjøring. Ikke bytt state-katalog eller kjør på
flere verter for å omgå den. Det finnes ingen distribuert lås. Normal avslutning
lar VM-ene beholde tilstanden; ingen opprydding eller autostart installeres.

SHUTOFF er ikke sletting. Stoppede instanser beholder disk og normalt allokert
instance/vCPU/RAM-kvote; eventuelle volumes og andre ressurser eksisterer videre.
Pegasus sine faktiske faktureringsregler må avklares med administrator.
De eksisterende EUR/time-metrikkene er modellestimater og beregner ikke
Pegasus-faktura eller kostnaden for stoppede disker.

## Trinnvise tester med eksplisitt godkjenning

Trinn 1–2 er lesende: kjør forhåndskontrollen og tørrkjøringen ovenfor. Før
trinn 3 må prosjekt, image, flavor, nettverk, eierskap og kvote være verifisert.
Legg frem konkret plan for én test-VM og få brukerens eksplisitte godkjenning.
Tidligere Moria-godkjenning gjelder ikke Pegasus.

Følgende er **kun etter godkjenning**, ikke en automatisk testsekvens. Bruk en
egen tom demo-pool og stopp eventuell kontinuerlig Pegasus-manager først.
Prometheus må levere en fersk gyldig sample; `player-share 0` gjør at minimum
styrer testmålet uten syntetisk endring i spillerkilden.

```powershell
# Trinn 3: én dedikert VM. Bekreft inventory i Horizon etterpå.
& $python main.py --controller pegasus --pegasus-config data/pegasus.json --label appid=730 --player-share 0 --minimum-instances 1 --max-instances 1 --once --pegasus-apply
# Trinn 4, separat godkjent stopp (ingen sletting):
& $python main.py --controller pegasus --pegasus-config data/pegasus.json --label appid=730 --player-share 0 --minimum-instances 0 --max-instances 1 --once --pegasus-apply
# Start samme VM igjen med trinn 3-kommandoen.
# Trinn 5, separat godkjent utvidelse fra 1 til 3:
& $python main.py --controller pegasus --pegasus-config data/pegasus.json --label appid=730 --player-share 0 --minimum-instances 3 --max-instances 3 --once --pegasus-apply
# Tilbake til 1 med trinn 3-kommandoen; de to andre beholdes SHUTOFF.
```

Se at de samme UUID-ene gjenbrukes og at alle øvrige VM-er er urørt.
Godkjenning for denne testen innebærer ikke kontinuerlig drift eller sletting.
ACTIVE bekrefter kun at OpenStack kjører VM-en, ikke SSH, cloud-init,
spillserverhelse eller at VM-en tar imot spillere.

## Prometheus og Grafana

Bruk en egen host-prosess på eksempelvis `127.0.0.1:8003` for Pegasus, med
`--metrics-host 127.0.0.1 --metrics-port 8003` og uten `--once` for kontinuerlig
tørrkjøring. Eksisterende prosesser bruker andre porter. Ikke bruk
`--pegasus-apply` kontinuerlig før dette er særskilt godkjent.
Ingen scrape-jobb eller dashboard er automatisk endret eller lastet om.

`/metrics` har eksisterende beslutningsmetrikker og egne infrastrukturmetrikker:

| Rå metrikk | Betydning |
| --- | --- |
| `pegasus_desired_instances` | Siste begrensede mål fra gyldig spillerinput, også ved feil |
| `pegasus_running_instances` | ACTIVE uten task, sist bekreftet gjennom API |
| `pegasus_stopped_instances` / `pegasus_error_instances` | Bekreftet SHUTOFF / ERROR |
| `pegasus_pending_operations` | VM-er med overgangstilstand/task, eller pågående forespørsel |
| `pegasus_actions_succeeded_total` / `pegasus_actions_failed_total` | Individuelle reelle VM-operasjoner siden prosessoppstart |
| `pegasus_action_latency_seconds_sum` / `pegasus_action_latency_seconds_count` | Tid til bekreftet sluttilstand for vellykkede handlinger |
| `pegasus_state_available` / `pegasus_state_timestamp_seconds` | API-lesing lyktes / siste vellykkede lesetidspunkt |
| `pegasus_dry_run` | 1 når mutasjoner er avslått |

Tilstand oppdateres ved beslutningssykluser og polling under operasjoner,
ikke av Prometheus-scrapes. Ved manglende spillerinput gjøres ikke en ny
infrastrukturkontroll. Bruk tidsstempel og tilgjengelighet for å vise ukjent/
gammel tilstand, ikke anta at tidligere verdier fortsatt er bekreftet.
Pending betyr VM-operasjoner, ikke bekreftet spillkapasitet. En feil før en
mutasjon (f.eks. quota/ownership) teller ikke som mislykket API-handling.

Forslag til **senere manuelt aktivert** separat scrape-jobb, med prefiks på
alle navn slik at eksisterende fake/Moria-spørringer ikke blandes med Pegasus:

```yaml
- job_name: pegasus_vm_autoscaler
  static_configs:
    - targets: ['host.docker.internal:8003']
      labels:
        appid: '730'
        pool: 'pegasus-demo'
  metric_relabel_configs:
    - source_labels: [__name__]
      regex: '(.+)'
      target_label: __name__
      replacement: 'pegasus_demo_${1}'
```

Etter relabeling heter for eksempel faktisk antall
`pegasus_demo_pegasus_running_instances`, mens beslutningsmålet heter
`pegasus_demo_desired_instances`. Bruk et eget Grafana-dashboard med disse
spørringene. Kontroller target UP, `pegasus_demo_pegasus_state_available == 1`,
ferskt state-tidsstempel og samsvar med Horizon før VM-tilstand presenteres som
verifisert. Gjennomsnittlig vellykket handlingslatens kan beregnes som
`rate(pegasus_demo_pegasus_action_latency_seconds_sum[5m]) / rate(pegasus_demo_pegasus_action_latency_seconds_count[5m])`.
Dette gir ingen spillserverberedskap eller måling av virkelig spillerkapasitet.

## Lokal verifikasjon

```powershell
& $python -m pytest -q
git diff --check
```

Mocktester dekker tørrkjøring, 1–3–1, gjenbruk, kvoter, eier-/prosjektvern,
pauser, pending, tidsavbrudd, feiltelling, usikker create på tvers av omstart,
prosesslås, CLI og reelle SDK-ressursfelter. De bekrefter ikke Pegasus-roller,
VM-oppstart, spillserverberedskap eller en virkelig Prometheus-scrape.

Resultat 2026-10-20: 334 tester bestått; fire deprecation-advarsler fra SDKs
ressursinitialisering. `git diff --check` bestått. Ingen autentiserte Pegasus-
eller reelle VM-tester er kjørt.
