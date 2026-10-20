# Lokal VirtualBox-vert for Ruby Acorn

Oppsettsveiledning før VM-implementering, undersøkt 20. oktober 2026.
Ingen hypervisor er installert, ingen Windows-funksjoner er endret og ingen
selvstendige VM-er er opprettet av Codex.

## Maskinen og kompatibiliteten

PC-en kjører Windows 11 Home, Intel i7-9750H (6 kjerner/12 tråder) og omtrent
16 GB RAM. Rundt 3,2 GB RAM og 634 GiB diskplass var ledig ved måling.
Docker Desktop kjører Linux-containere med WSL2. Windows rapporterer en aktiv
hypervisor. Ingen Hyper-V-modul/Get-VM, vmms-tjeneste eller VirtualBox-, VMware-
eller QEMU-verktøy ble funnet.

[Hyper-V-rollen kan ikke installeres på Windows 11 Home](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v).
[WSL2 bruker en del av Hyper-V-arkitekturen](https://learn.microsoft.com/en-us/windows/wsl/faq),
men dette gjør ikke PC-en til en vanlig administrerbar Hyper-V-vert.

[Oracle beskriver VirtualBox over Hyper-V via Windows Hypervisor Platform](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/AdvancedTopics.html),
med automatisk oppdagelse av hypervisoren, men potensielt betydelig ytelsestap.
[Oracle dokumenterer også konflikter og ytelsesproblemer](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/Troubleshooting.html).
Sambruk må derfor testes på denne PC-en før vi lager flere VM-er.
Behold WSL2, Virtual Machine Platform og sikkerhetsinnstillingene under testen.
Generell feilsøking som slår av Windows-hypervisoren vil påvirke Docker/WSL2
og observabiliteten vi trenger samtidig.

CPU-feltene for direkte virtualiseringsstøtte rapporterte false samtidig som
HypervisorPresent var true. Dette alene begrunner ikke en påstand om at
BIOS-virtualisering er av når Windows-hypervisoren allerede kjører.

## Trinn 1: Installer og kontroller VirtualBox

1. Last ned gjeldende stabil Windows x86-64-utgave fra
   [VirtualBox sine offisielle nedlastinger](https://www.virtualbox.org/wiki/Downloads).
   Bruk vanlig pakke; prototypen trenger ikke Extension Pack.
   Følg [Oracles installasjonsveiledning](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html).
2. Installer under din vanlige Windows-bruker, med administratorgodkjenning
   når Windows ber om det. Python SDK/bindings trengs ikke for VBoxManage.
   Følg eventuelle runtime-forutsetninger fra installasjonsprogrammet og bruk
   offisielle nedlastinger. Start PC-en på nytt hvis installasjonen krever det.
3. Kontroller **Windows Hypervisor Platform / Windows-hypervisorplattform** i
   «Aktiver eller deaktiver Windows-funksjoner». Oracle krever denne komponenten
   for Hyper-V-integrasjonen. Aktiver den hvis den mangler, og start Windows
   på nytt. Dette er en annen komponent enn Hyper-V-rollen.
   Behold Windows Subsystem for Linux og Virtual Machine Platform aktivert.
4. Åpne PowerShell fra prosjektmappen:

```powershell
& 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe' --version
& 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe' list vms
docker compose ps
```

Tom VM-liste er forventet før første VM opprettes. Kontroller at Docker Desktop
og Compose-tjenestene fortsatt fungerer etter eventuell omstart.
**Dette er neste felles kontrollpunkt. Autoscaleren bruker fortsatt fake.**

## Trinn 2: Lag og test én liten Linux-mal

Etter at trinn 1 er bekreftet:

1. Last ned [Debian amd64 netinst](https://www.debian.org/distrib/netinst).
   Bruk Debian 13 og tekstinstallasjon uten skrivebord. Debian anbefaler
   [1 GB RAM og 4 GB disk uten skrivebord](https://www.debian.org/releases/trixie/amd64/ch03s04.en.html).
2. Opprett `ruby-acorn-template`: én virtuell CPU, 1024 MiB RAM, dynamisk
   VDI-disk på 8 GiB og NAT-nettverk. Bruk manuell installasjon. Ingen delte
   mapper, vertsdisktillgang eller 3D-akselerasjon trengs.
   Plasser VM-filene utenfor OneDrive/Git, for eksempel
   `C:\Users\jsand\VirtualBox VMs\RubyAcorn`.
3. Installer på VM-ens virtuelle disk med standard systemverktøy, eventuelt
   SSH-server, og en lokal bruker. Ingen passord lagres i repoet.
4. Test Linux-oppstart og normal avslutning mens Docker/Prometheus/Grafana
   fortsatt kjører. Noter tid og minnebruk. Ved problemer inspiseres VM-ens
   `Logs/VBox.log`, særlig meldinger om Hyper-V/NEM.
5. Test ACPI-avslutning og bekreft faktisk avslått tilstand. En vellykket
   stoppeforespørsel alene beviser ikke at VM-en har stoppet. Hard `poweroff`
   skal ikke være vanlig skaleringsoperasjon.

Tre gjester trenger 3 GiB RAM pluss overhead; rundt 3,2 GB ledig gir for liten
margin. Begynn med én, lukk unødvendige programmer og mål igjen før flere
startes. WSL-minnegrenser og sikkerhetsfunksjoner endres ikke automatisk.

## Trinn 3: Klon en fast gruppe

Når malen fungerer stabilt, slå den helt av. Lag to fulle kloner,
`ruby-acorn-01` og `ruby-acorn-02`, og eventuelt `ruby-acorn-03` etter ny
minne-/ytelsestest. Bruk nye MAC-adresser. Gjør Linux-vertsnavn, maskin-ID og
eventuelle SSH-vertsnøkler unike før klonene brukes som separate nettverksverter.
Malen forblir avslått og inngår ikke i autoscalerens gruppe.

Registrer klonenes eksakte UUID-er fra `VBoxManage list vms`. Kontroller at
hver klone starter og avslutter normalt. Ingen kloning eller sletting skjer
under skaleringssykluser.

## Deretter implementeres kontrolleren

Etter manuell verifisering utvider vi eksisterende
`running_instances`, `scale_up`, `scale_down`-grensesnitt med eksplisitt valgt
VirtualBox-backend. Fake beholdes til testing og som standard. Kontrolleren
skal styre bare konfigurerte UUID-er, starte avslåtte VM-er uten GUI og be om
normal ACPI-avslutning. Den må avvente og verifisere faktisk tilstand, håndtere
feil og unngå nye operasjoner på VM-er som allerede starter eller avsluttes.
VM-oppstart betyr ikke at en spillserver er klar.

Aktivt tak blir gruppens størrelse, altså 2 eller 3. Rått kapasitetsbehov
bevares selv om det overstiger taket, med høyst to endringer per syklus.
TimesFM observerer bare. Kostnadene er fortsatt illustrerende EUR/time.
Grensene 7/10/15 gjelder offline-simulering og krever ikke tilsvarende antall
lokale VM-er.
