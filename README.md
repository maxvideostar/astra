# astra_web

Interfață web pentru binarul **Astra Cesbo** (versiunea free). Nu modifică binarul — doar
generează config-ul Lua, pornește/oprește procesul și citește statisticile din log.

- **Fără dependențe** — doar Python 3 din standard library. Merge pe **Windows** și **Ubuntu**.
- Control proces: Start / Stop / Restart + status (pid, uptime).
- Editor de canale: input (UDP/HTTP) → output (HTTP/UDP), mai multe pe canal.
- Monitorizare live: bitrate, CC errors, PES errors, scrambled/OK.
- Log viewer live + vizualizare config Lua generat.
- Login cu user/parolă, acces local sau extern.

Testat cu **Astra 4.4.182** (Windows și Linux).

---

## Ubuntu / Debian

```bash
git clone https://github.com/maxvideostar/astra.git
cd astra
sudo ./install.sh
# port personalizat:  PORT=9000 sudo -E ./install.sh
```

Se instalează în `/opt/astra-web` și pornește ca serviciu systemd.

```bash
systemctl status astra-web      # status
journalctl -u astra-web -f      # loguri
systemctl restart astra-web     # repornire
sudo ./uninstall.sh             # scoate serviciul
```

Interfața: `http://IP-SERVER:8080` — user `admin`, parolă `admin` (schimb-o din **Setări**).

---

## Windows

1. Pune `astra_web.py`, `start.bat` și `astra.exe` în același folder.
2. Instalează [Python 3](https://www.python.org/downloads/) (bifează *Add to PATH*).
3. Editează în `start.bat` linia `set "ASTRA=C:\astra\astra.exe"` cu calea ta.
4. Dublu-click pe `start.bat`. În fereastra neagră scrie adresa exactă (ex. `http://127.0.0.1:8080`).

Dacă portul e refuzat de Windows (`WinError 10013`), scriptul sare automat la următorul liber.

---

## Utilizare

**Canale** → `+ Canal`:
- **Input**: sursa, un link pe linie — ex. `udp://@239.1.1.1:1234` sau `http://host/stream`.
- **Output**: unde iese — ex. `http://0.0.0.0:8000/hd365`.
  Folosește **același port** la toate canalele și **path diferit** (`/hd365`, `/astv`…); Astra
  face un singur server HTTP și le multiplexează.

**Salvează** → **Restart**. Testezi în VLC: `http://IP:port/path`.

### Note
- Nu folosi `localhost` la output pe Windows (se traduce în IPv6 `::1` și Astra dă `10049`);
  pune `127.0.0.1`, `0.0.0.0` sau IP-ul din LAN.
- `10013` la output = port ocupat/rezervat sau blocat de firewall/antivirus — schimbă portul
  sau permite `astra` în firewall.
- Parsarea statisticilor presupune formatul standard de log Astra (`[nume] Bitrate:… CC error:…`).

---

## Securitate

Schimbă parola din **Setări** imediat. Pentru acces din internet, pune-l în spatele unui
reverse proxy cu HTTPS (nginx) și nu expune portul direct.

---

## Fișiere

| fișier | rol |
|---|---|
| `astra_web.py` | aplicația (server web + control proces), cross-platform |
| `start.bat` | launcher Windows |
| `install.sh` / `uninstall.sh` | instalare/dezinstalare systemd pe Ubuntu |
| `astra` / `astra.exe` | binarele Astra Cesbo free (Linux / Windows) |

La rulare se creează `settings.json`, `channels.json`, `astra_config.lua`, `astra_web.log`.
