# Drifts-VM: opprettelse og forbindelseskontroll

2026-10-20: én drifts-VM er opprettet etter brukerens eksplisitte godkjenning.
**VM-en er ACTIVE, men videre utrulling er blokkert av spillkildetilgang og
SSH-tilgang fra arbeidsmaskinen.** Prometheus, Grafana og beslutningsmotoren er
ikke installert/startet på VM-en. Automatisk skalering er ikke aktivert.

| Felt | Bekreftet verdi |
| --- | --- |
| Navn | ruby-acorn-management-01 |
| UUID | e92fea6d-7f8b-405a-ab12-fd00ea40c232 |
| Intern IPv4 | 192.168.44.243 |
| Tilstand | ACTIVE uten pågående task |
| Flavor | aem.2c4r.50g: 2 vCPU, 4096 MiB RAM, 50 GiB disk |
| Image | Ubuntu-24.04-LTS (Noble Numbat) |
| Nettverk | Eksisterende ACIT4410 |
| Keypair / security group | ruby-acorn-key / uendret default |
| Metadata | ruby_acorn_role=management, ruby_acorn_project=ruby-acorn |
| Skaleringspool | Ingen: har ikke pool-/game-/controller-metadata eller poolens navnemønster |
| Siste sluttkontroll | 2026-10-20 18:17:32 Europe/Oslo / 16:17:32 UTC |

VM-en ble opprettet med en liten cloud-init-probe, uten pakkeinstallasjon,
credentials, privatnøkler eller Ruby Acorn-utrulling. Rapporten ble hentet fra
Nova-konsollen med autentisert API; rå konsollutdata ble ikke publisert eller
lagret. Cloud-init er bekreftet ferdig uten observerte skriptfeil.

## Resultater fra selve VM-en

| Test | Resultat |
| --- | --- |
| ACIT4410 `http://172.31.255.1:1080/health` | Forbindelsesfeil, URLError; ingen HTTP-respons |
| ACIT4410 `http://172.31.255.1:1080/metrics` | Forbindelsesfeil, URLError; ingen HTTP-respons |
| Identity `https://pegasus.sky.oslomet.no:5000` | HTTP 300 |
| Identity discovery `:5000/v3/` | HTTP 200 |
| Compute `:8774/v2.1` | HTTP 200 |
| Image `:9292` | HTTP 300 |
| Network `:9696` | HTTP 200 |
| Volume v3 `:8776/v3/PROJECT_ID` | HTTP 401 |
| Lokal TCP-port 22 på VM-en | Lytter / tilkobling lyktes |

API-prøvene bekrefter DNS-/nettverks-/TLS-/HTTP-tilgang fra gjesten til de
katalogførte endepunktene. HTTP 401 på Volume betyr nådd tjeneste uten
autentisering, ikke et nettverksbrudd. **Application-credential-autentisering
inne i VM-en er ikke testet**: ingen credentials er kopiert dit. Det gjenstår
før en beslutningsmotor faktisk kan administrere VM-er fra drifts-VM-en.

Spilldatakildens feilrapport inneholder bare feilklasse, ikke underliggende
socket-feil. Det er derfor ikke fastslått om årsaken er ruting, filtrering eller
at denne private adressen ikke støttes fra Pegasus. Ingen ruter, DNS,
brannmurer, VPN, Tailscale eller andre nettverksinnstillinger ble endret.

## SSH fra arbeidsmaskinen

TCP-test og ekte `ssh` med eksisterende privatnøkkel, BatchMode og streng
vertsnøkkelkontroll fikk tidsavbrudd mot `192.168.44.243:22` før håndtrykk.
Ingen autentisert SSH-sesjon ble etablert. VM-ens offentlige host key ble
hentet fra den autentiserte konsollrapporten og lagret separat utenfor Git;
et faktisk SSH-håndtrykk kunne ikke verifiseres fordi forbindelsen aldri kom frem.

Security group `default` er uendret: utgående IPv4/IPv6 og inngående trafikk
fra samme sikkerhetsgruppe, uten generell SSH-åpning for arbeidsmaskinen.
Dette forklarer en mulig sperre, men en TCP-timeout alene fastslår ikke alle
rute-/filterledd. Ingen inngående port ble åpnet for å få testen til å lykkes.

## Faktisk ressursallokering og plass

Boot-proben inne i drifts-VM-en målte:

- 2 synlige CPU-er.
- MemTotal 4009888 KiB, omtrent 3,82 GiB synlig RAM etter systemreservasjoner.
- MemAvailable 3557628 KiB, omtrent 3,39 GiB, ved måletidspunktet.
- Rotfilsystem: 50884108288 bytes totalt (47,39 GiB), 1730125824 bytes brukt
  (1,61 GiB), 49137205248 bytes ledig (45,76 GiB).

50 GiB er flavorens allokerte virtuelle disk; gjestens rotfilsystem er mindre
etter partisjonering og filsystemoverhead. Tallene over er oppstartsrapportens
måling, ikke senere applikasjonsbelastning.

Prosjektets sluttbruk fra direkte API:

| Ressurs | Brukt | Kvote | Ledig |
| --- | ---: | ---: | ---: |
| VM-er | 2 | 10 | 8 |
| vCPU | 3 | 12 | 9 |
| RAM | 6144 MiB (6 GiB) | 24576 MiB (24 GiB) | 18432 MiB (18 GiB) |
| Nettverksporter | 2 | 10 | 8 |
| Cinder-volumer / GiB | 0 / 0 | 10 / 300 | 10 / 300 |

Begge VM-er har 50 GiB flavor-rotdisk, totalt 100 GiB allokert; ingen separat
Cinder-volume eller floating IP ble opprettet. Ingen timepris/fakturert kostnad
er verifisert.

## Feil, vern og gjenstående steg

Et lokalt API-kall ved lesing av oppstartsrapporten fikk ConnectTimeout etter
at ACTIVE allerede var bekreftet. En senere **lesende** kontroll hentet den
ferdige rapporten. Create ble ikke gjentatt, og kun én ny VM er opprettet.

Sluttinventaret og sikkerhetsreglene ble sammenlignet med tidligere bevis:
eksisterende test-VM `1517a4dc-ad07-45da-8fd2-c87307a9f8fb` er fortsatt ACTIVE
og uendret. Nettverk, default-regler og øvrige eksisterende VM-er er ikke endret.
Ingen Moria-kontakt eller Moria-endring var del av dette arbeidet.

Videre utrulling er ikke klar. Nødvendige neste avklaringer er:

1. Kurs-/Pegasus-administrator må bekrefte en støttet adresse/rute fra ACIT4410-
   subnettet i Pegasus til spilldatakilden, eventuelt en alternativ offisiell
   kildeadresse. Ikke erstatt med Moria-tunnel eller syntetiske data.
2. SSH-administrasjon må avklares. En eventuell separat management-security-
   group med bare TCP 22 fra en konkret godkjent kilde-IP/CIDR, knyttet kun til
   drifts-VM-en, krever **ny godkjenning** før den opprettes/tilknyttes. Ikke
   utvid eksisterende default-gruppe eller åpne dashboard-/Prometheus-porter.
3. Når forbindelsene er i orden: avtal sikker credential-konfigurasjon inne i
   gjesten og verifiser autentisert API-lesing. Deretter kan utrulling vurderes.
   Ingen installasjon eller automatisk skalering er autorisert i denne runden.

Ufarlig evidens: ignorert `data/pegasus/management-create-receipt.json`, samt
engangs bootstrap/probe under samme mappe. Ikke kjør opprettelsesskriptet igjen;
kvittering/navnekollisjon sperrer vanlig gjentakelse. Privatnøkler og credentials
forblir utenfor repoet og er ikke inkludert i cloud-init eller konsollrapporten.
