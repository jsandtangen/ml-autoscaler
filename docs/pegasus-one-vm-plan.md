# Plan for godkjenning: én Pegasus-test-VM

Status 2026-10-20: klargjort, **VM-opprettelse avventer eksplisitt godkjenning**.
Arbeidet fortsetter på `feature/pegasus-integration`; eksisterende lokale
endringer er bevart. Ingen push, merge eller kontinuerlig manager er startet.

## Klargjøring som er utført

Lesende kontroll fant ingen eksisterende keypairs eller VM-er. Brukeren ba
uttrykkelig om å opprette `ruby-acorn-key` dersom et egnet keypair manglet.
En dedikert Ed25519-nøkkel ble derfor generert lokalt, og **kun den offentlige
nøkkelen** ble registrert i Pegasus. Registrert offentlig nøkkel ble lest tilbake
og sammenlignet med den lokale.

Privatnøkkel: `%LOCALAPPDATA%\RubyAcorn\openstack\keys\ruby-acorn-key`.
Offentlig nøkkel ligger ved siden av med `.pub`. NTFS-kontroll viste kun
innlogget Windows-bruker med FullControl på begge filer. De ligger utenfor Git
og OneDrive. Ingen privatnøkkel eller credential-verdi er skrevet ut.
Ingen eksisterende skyressurser eller sikkerhetsregler er endret.

## Konkret VM

| Felt | Planlagt verdi |
| --- | --- |
| Prosjekt | ACIT4410_H26_jesan5100 |
| Prosjekt-ID | 234f15222c5e4396851d2713697893b1 |
| SDK cloud / region | openstack / Pilestredet |
| Antall / hard grense i testfil | 1 / 1 |
| VM-navn | ruby-acorn-pegasus-vm-test-730-01 |
| Flavor | aem.1c2r.50g |
| Flavor-ID | 2db2d811-a566-4438-b7ad-64735413c2db |
| CPU / RAM / rotdisk | 1 vCPU / 2048 MiB / 50 GiB |
| Image | Ubuntu-24.04-LTS (Noble Numbat), ACTIVE |
| Image-ID | 414d4efa-e67d-43cc-b484-3d88817bcec1 |
| Nettverk | ACIT4410, eksisterende shared/internal IPv4-nett med DHCP |
| Nettverks-ID | 374240bf-b8c4-45cd-b377-e32a247beb43 |
| SSH-keypair | ruby-acorn-key |
| Security group | default, bcfdaf33-d05e-4d17-9a0b-efdaaf6c7c87 |
| Floating IP / separat boot-volume | Ingen / ingen |

Eierskapsmetadata som opprettes sammen med VM-en:

```json
{
  "ruby_acorn_controller": "pegasus",
  "ruby_acorn_pool": "vm-test",
  "ruby_acorn_game": "730"
}
```

`default` har to utgående regler (IPv4/IPv6) og to inngående regler fra samme
sikkerhetsgruppe. Den åpner ikke generell SSH-tilgang fra klientmaskinen.
API-testen krever ingen nye regler. Ingen SSH-/spillporter, floating IP,
nettverk, router eller sikkerhetsgrupper skal opprettes eller endres i testen.

## Ressursbudsjett, kontrollert før opprettelse

| Ressurs | Nå | Etter én VM | Kvote | Gjenstår |
| --- | ---: | ---: | ---: | ---: |
| VM-er | 0 | 1 | 10 | 9 |
| vCPU | 0 | 1 | 12 | 11 |
| RAM MiB | 0 | 2048 | 24576 | 22528 |
| Nettverksporter | 0 | 1 | 10 | 9 |

I tillegg allokeres en 50 GiB rotdisk. Ingen separat Cinder-volume er planlagt.
Prosjektets Compute-/portkvoter passer; faktisk ledig hypervisor-/diskkapasitet
kan først bekreftes ved godkjent opprettelse. Kvoter og inventar sjekkes på nytt
umiddelbart før mutasjon. SHUTOFF frigjør ikke VM-en eller disken og skal ikke
tolkes som sletting eller null kostnad. Eksakte Pegasus-priser er ikke verifisert.

## Testsekvens etter godkjenning

1. Kontroller på nytt riktig prosjekt, keypair, kvoter, eierskap/navnekollisjoner
   og at kun én manager håndterer testpoolen. Behold grensen på én VM.
2. Bruk Python `DecisionEngine` og `PegasusController(dry_run=False)` med den
   ferdige konfigurasjonen. Sett ønsket antall til 1. Opprett én VM og vent på
   ACTIVE uten pågående task. Noter UUID og IP fra direkte API-lesing.
3. Etter den konfigurerte pausen på minst 120 sekunder: sett ønsket antall til
   0, stopp **samme UUID**, og bekreft SHUTOFF uten task gjennom Pegasus API.
4. Etter ny pause: sett ønsket antall til 1. Start den stoppede VM-en igjen;
   bekreft samme UUID og ACTIVE uten task. Ingen ny VM skal opprettes her.
5. Les sluttinventar direkte fra Pegasus og kontroller at øvrige ressurser er
   uendret. Lagre en rapport uten hemmeligheter under ignorerte `data/pegasus/`.

Planlagt sluttilstand er **én ACTIVE VM**, beholdt etter testen. Ingen sletting
og ingen kontinuerlig skalering inngår. ACTIVE er kun bekreftet VM-tilstand;
SSH, cloud-init, spillserver og 100 spillere/VM regnes ikke som verifisert.
Ved API-feil eller usikker opprettelse: stopp testen, behold nøkkel/VM/kvittering
og rapporter faktisk kjent tilstand. Ingen automatisk sletting eller ny create
skal brukes som feilretting.

## Lokal konfigurasjon og allerede utført verifikasjon

Ferdig konfigurasjon: `data/pegasus/one-vm-test.json` (ignorert av Git), med
pool `vm-test`, appid `730`, maximum_instances `1`, timeout `300` sekunder og
cooldown `120` sekunder. Privatnøkkelbanen inngår ikke i poolfilen.

Den eksisterende `scripts/inspect_pegasus.py --config` bestod mot dette oppsettet.
En `DecisionEngine`-tørrkjøring med `ScalingLimits(0, 1, 1)` og ønsket antall 1
ga kun planen `create ruby-acorn-pegasus-vm-test-730-01`. Faktisk antall forble
0, og tellere for vellykkede/mislykkede VM-operasjoner forble 0.

Ufarlige kvitteringer: `data/pegasus/keypair-registration.json`,
`one-vm-plan-preflight.json` og `one-vm-dry-run.json`.
Disse inneholder ingen privatnøkkel eller application credentials.
