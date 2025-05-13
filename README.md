# OPC-UA Gateway

En gateway for å koble OPC-UA servere til Unity-applikasjoner via HTTP API.

## Funksjonalitet

- Kobler til OPC-UA servere med støtte for sikkerhetspolicyer
- Abonnerer på OPC-UA noder definert i konfigurasjonsfilen
- Eksponerer data via et HTTP REST API for Unity
- Støtter CORS for webbaserte klienter

## Installasjon

```bash
# Klon repositoriet
git clone https://github.com/lonesomeridr/OPC-UA_Gateway.git
cd OPC-UA_Gateway

# Opprett virtuelt miljø
python3 -m venv venv
source venv/bin/activate

# Installer avhengigheter
pip install -r requirements.txt
```

## Konfigurasjon

Rediger `config.ini` filen:

```ini
[OPCUA]
server_url = opc.tcp://192.168.1.100:4840
application_uri = urn:example:client
security_policy = None
security_mode = None

[HTTP]
host = 0.0.0.0
port = 5000
cors_enabled = true

[MONITORING]
node1_id = ns=2;s=Device1.Tag1
node1_name = FlowTransmitter
node1_unit = l/min
```

## Kjøring

```bash
# Manuell kjøring
python main.py

# Med tilpasset konfigurasjonsfil
python main.py --config /sti/til/config.ini

# Med spesifikt loggnivå
python main.py --log-level DEBUG
```

## API-endepunkter

- `GET /api/values` - Hent alle verdier
- `GET /api/value/{name}` - Hent en spesifikk verdi basert på navn
- `GET /api/status` - Sjekk server status

## Oppsett som systemtjeneste

Se dokumentasjonen for å sette opp som systemtjeneste for autostart.