# Pegasus: resultat av godkjent én-VM-test

2026-10-20: testen er **bestått** etter brukerens eksplisitte godkjenning av
opprettelse, stopp og start av én VM. Ingen automatisk skalering ble startet.
VM-en er beholdt **ACTIVE**, slik den godkjente planen beskrev.

| Felt | Bekreftet verdi |
| --- | --- |
| Prosjekt | ACIT4410_H26_jesan5100 |
| VM-navn | ruby-acorn-pegasus-vm-test-730-01 |
| UUID | 1517a4dc-ad07-45da-8fd2-c87307a9f8fb |
| Intern IPv4 | 192.168.44.33 |
| Flavor | aem.1c2r.50g |
| Image | Ubuntu-24.04-LTS (Noble Numbat) |
| Nettverk / security group / keypair | ACIT4410 / default / ruby-acorn-key |
| Siste uavhengige API-verifikasjon | 16:05:46 Europe/Oslo (14:05:46 UTC) |

## Reelle tilstandsoverganger

Alle mutasjoner brukte `DecisionEngine.apply_desired_instances()` og
`PegasusController(dry_run=False)`, med `ScalingLimits(0, 1, 1)` og den
forhåndsvaliderte konfigurasjonen `data/pegasus/one-vm-test.json`.
Dette var eksplisitte testmål 1 → 0 → 1, ikke en kontinuerlig spillerstyrt loop.

| Trinn | Resultat lest direkte fra Pegasus | Samme UUID | Task |
| --- | --- | --- | --- |
| Opprettelse | ACTIVE | 1517a4dc-ad07-45da-8fd2-c87307a9f8fb | Ingen |
| Stopp | SHUTOFF, bekreftet 16:02:30 Europe/Oslo | Samme | Ingen |
| Start | ACTIVE, bekreftet 16:04:56 Europe/Oslo | Samme | Ingen |

Opprettelse til bekreftet sluttilstand tok omtrent 30,6 sekunder i controlleren.
Stopptrinnet tok omtrent 15,7 sekunder og starttrinnet 24,9 sekunder inkludert
tilhørende lesekontroller. Den konfigurerte pausen på minst 120 sekunder ble
respektert mellom handlingene. Ved gjenstart ble samme VM og IP gjenbrukt;
ingen ytterligere VM ble opprettet.

## Feil og verifikasjon

Ingen av de tre skyoperasjonene feilet. To lokale kontroller i det engangs
testskriptet var for strenge og ble korrigert uten ytterligere opprettelse:

1. En utvidet VM-respons fra SDK/Nova representerte flavorens `id` som navn,
   ikke den konfigurerte UUID-en. Kontrolløren hadde allerede bekreftet ACTIVE.
   Uavhengig lesing av flavor ved UUID bekreftet riktig navn og 1 vCPU / 2048
   MiB / 50 GiB. Fortsettelsen forbød `create` og tillot kun stop/start på UUID-en
   ovenfor. Den opprinnelige opprettelsens cooldown ble beholdt over omstarten.
2. API-et returnerte sikkerhetsregler i forskjellig rekkefølge. En
   rekkefølgeavhengig sammenligning feilet. Uavhengig sluttkontroll normaliserte
   regelrekkefølgen og bekreftet identiske regler mot bevis fra før opprettelse.

Opprinnelige verifikasjonsfeil er bevart som egne rapporter; de ble ikke skjult
som vellykkede VM-handlinger. Sluttrapporten bekrefter alle tre tilstandene,
samme UUID, riktig image/keypair/flavor og ingen pågående task. Dette verifiserer
infrastrukturtilstand, ikke SSH, cloud-init, spillserverhelse eller 100 spillere/VM.

## Faktisk allokering etter testen

Direkte API-kontroll viste følgende prosjektbruk både etter start og mens
VM-en var SHUTOFF:

| Ressurs | Brukt | Kvote | Ledig |
| --- | ---: | ---: | ---: |
| VM-er | 1 | 10 | 9 |
| vCPU | 1 | 12 | 11 |
| RAM | 2048 MiB (2 GiB) | 24576 MiB (24 GiB) | 22528 MiB (22 GiB) |
| Nettverksporter | 1 | 10 | 9 |

Rotdisk: 50 GiB allokert fra flavoren, uten separat tilkoblet volume.
Ingen floating IP ble opprettet. Dette er faktisk bekreftet ressursallokering;
CPU-belastning, gjestens RAM-/diskforbruk og fakturert kostnad er ikke målt.
SHUTOFF frigjorde ikke VM-/CPU-/RAM-/portkvoten i denne testen.

## Ressurser som er bevart

Prosjektets VM-inventar var tomt ved teststart og inneholder nå kun test-VM-en.
Det var dermed ingen andre VM-er i dette prosjektet å påvirke. Sluttkontrollen
bekreftet også uendrede nettverk, uendrede sikkerhetsgrupper/regler og at det
registrerte keypairet fortsatt matcher lokal offentlig nøkkel. Ingen nøkkel,
privatnøkkel, nettverk, eksisterende sikkerhetsregel eller Moria-ressurs ble endret
av testen. Kun én VM og dens tilknyttede port ble opprettet, som godkjent.

Testprosessene er avsluttet. Ingen Pegasus-manager, planlagt jobb, autostart,
Compose-endring eller Prometheus/Grafana-utrulling ble startet. Ingen sletting
ble utført. Senere stopp, sletting eller automatisk styring må følge brukerens
videre instruksjoner; denne godkjenningen gjaldt kun denne testsekvensen.

Ufarlig evidens ligger under ignorert `data/pegasus/`:

- `one-vm-test-run.json`: endelig resultat, tilstander, UUID og kvotebruk.
- `one-vm-test-initial-verification-error.json`: første lokale flavor-kontrollfeil.
- `one-vm-test-final-verifier-error.json`: lokal rekkefølgeavhengig regelkontroll.
- `approved-one-vm-test.py` og `approved-one-vm-resume-test.py`: engangsskript;
  de er ikke driftstjenester og skal ikke kjøres på nytt som automatisk skalering.
