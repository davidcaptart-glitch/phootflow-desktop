"""Administración de licencias de PhootFlow (solo para David). Ver LICENCIAS.md.

Necesita la clave secreta de Supabase (service_role o sb_secret_…) en la variable de entorno
PHOOTFLOW_SERVICE_KEY o en %APPDATA%\\PhootFlow\\admin.env (línea PHOOTFLOW_SERVICE_KEY=…). Nunca en el repo.

    python admin_licencias.py crear "Ana Pérez" --email ana@ejemplo.com
    python admin_licencias.py listar
    python admin_licencias.py ver PF-XXXXX-…        (o parte del nombre del titular)
    python admin_licencias.py revocar PF-XXXXX-…
    python admin_licencias.py reactivar PF-XXXXX-…
    python admin_licencias.py liberar PF-XXXXX-… [equipo]
    python admin_licencias.py crear "Ana" --producto lite        # app móvil Lite (o pro)
    python admin_licencias.py cambiar PF-XXXXX-… --equipos 2 --dias 14 --caduca 2027-12-31|nunca
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

import net

SUPABASE_URL = "https://exlegzodfrydddgcvbrp.supabase.co"
ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford: sin I, L, O, U para que no se confundan


def new_key() -> str:
    """PF- y 4 grupos de 5 caracteres: 100 bits aleatorios."""
    return "PF-" + "-".join("".join(secrets.choice(ALFABETO) for _ in range(5)) for _ in range(4))


def service_key() -> str:
    k = os.environ.get("PHOOTFLOW_SERVICE_KEY", "").strip()
    if k:
        return k
    path = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PhootFlow", "admin.env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                name, _, val = line.strip().partition("=")
                if name.strip() == "PHOOTFLOW_SERVICE_KEY" and val.strip():
                    return val.strip().strip('"')
    except OSError:
        pass
    sys.exit("Falta la clave secreta de Supabase: define PHOOTFLOW_SERVICE_KEY o crea "
             f"{path} (ver LICENCIAS.md).")


class Admin:
    def __init__(self, url: str, key: str, opener=net.urlopen):
        self.base = url.rstrip("/") + "/rest/v1/"
        self.key = key
        self.opener = opener

    def _req(self, method: str, path: str, body=None):
        h = {"apikey": self.key, "Content-Type": "application/json", "Prefer": "return=representation"}
        if self.key.startswith("eyJ"):  # clave service_role antigua (JWT)
            h["Authorization"] = "Bearer " + self.key
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with self.opener(req, timeout=20) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise SystemExit(f"Supabase respondió {e.code}: {e.read().decode(errors='replace')[:300]}")
        return json.loads(raw) if raw else []

    # --- consultas
    def all(self) -> list[dict]:
        return self._req("GET", "licencias?select=*,equipos(*)&order=creada.desc")

    def find(self, q: str) -> dict:
        """Por clave exacta o por parte del nombre del titular (debe ser única)."""
        clave = "".join(q.split()).upper()
        r = self._req("GET", "licencias?select=*,equipos(*)&clave=eq." + urllib.parse.quote(clave))
        if not r:
            r = self._req("GET", "licencias?select=*,equipos(*)&titular=ilike."
                          + urllib.parse.quote(f"*{q}*"))
        if not r:
            raise SystemExit(f"No hay ninguna licencia que coincida con «{q}».")
        if len(r) > 1:
            raise SystemExit("Coinciden varias: " + ", ".join(f"{x['titular']} ({x['clave']})" for x in r))
        return r[0]

    # --- cambios
    def create(self, titular: str, email="", notas="", equipos=1, dias=7, caduca=None,
               producto="escritorio") -> dict:
        row = {"clave": new_key(), "titular": titular, "email": email, "notas": notas,
               "max_equipos": equipos, "dias_offline": dias, "caduca": caduca}
        if producto != "escritorio":  # sin la migración 002 la columna no existe: las de PC no la mandan
            row["producto"] = producto
        return self._req("POST", "licencias", row)[0]

    def update(self, lic: dict, **cambios) -> dict:
        cambios["cambiada"] = dt.datetime.now(dt.timezone.utc).isoformat()
        return self._req("PATCH", "licencias?id=eq." + lic["id"], cambios)[0]

    def release(self, lic: dict, equipo: str = "") -> int:
        eqs = [e for e in lic.get("equipos", [])
               if not equipo or e["huella"].startswith(equipo.lower()) or e["nombre"].lower() == equipo.lower()]
        for e in eqs:
            self._req("DELETE", "equipos?id=eq." + e["id"])
        return len(eqs)


# ------------------------------------------------------------------ salida
def _fecha(s) -> str:
    if not s:
        return "—"
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y %H:%M")


def _caduca(s: str | None):
    if s is None:
        return None
    if s.lower() in ("nunca", "no", ""):
        return ""
    return dt.datetime.strptime(s, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=dt.timezone.utc).isoformat()


PRODUCTOS = {"escritorio": "PC (escritorio)", "lite": "móvil Lite", "pro": "móvil Pro (incluye Lite)"}


def describe(lic: dict) -> str:
    eqs = lic.get("equipos", [])
    lines = [f"{lic['titular'] or '(sin nombre)'}  ·  {lic['clave']}  ·  "
             f"{PRODUCTOS.get(lic.get('producto') or 'escritorio', '?')}",
             f"  estado: {lic['estado']}   equipos: {len(eqs)}/{lic['max_equipos']}   "
             f"sin conexión: {lic['dias_offline']} días   caduca: {_fecha(lic.get('caduca'))}",
             f"  email: {lic.get('email') or '—'}   creada: {_fecha(lic.get('creada'))}"]
    if lic.get("notas"):
        lines.append(f"  notas: {lic['notas']}")
    for e in eqs:
        lines.append(f"  · {e['nombre'] or '?'} [{e['huella'][:8]}]  v{e.get('version') or '?'}  "
                     f"desde {_fecha(e.get('primera'))}  último uso {_fecha(e.get('ultima'))}")
    return "\n".join(lines)


def main(argv=None, admin: Admin | None = None) -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="admin_licencias", description="Licencias de PhootFlow")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crear", help="crea una clave nueva")
    c.add_argument("titular")
    c.add_argument("--email", default="")
    c.add_argument("--notas", default="")
    c.add_argument("--equipos", type=int, default=1)
    c.add_argument("--dias", type=int, default=7, help="días que funciona sin conexión")
    c.add_argument("--caduca", help="AAAA-MM-DD (por defecto, no caduca)")
    c.add_argument("--producto", choices=["escritorio", "lite", "pro"], default="escritorio",
                   help="escritorio = PhootFlow para PC; lite / pro = apps móviles (Pro abre también Lite)")
    sub.add_parser("listar", help="todas las licencias con sus equipos")
    for name, h in (("ver", "detalle de una licencia"), ("revocar", "quita el acceso"),
                    ("reactivar", "devuelve el acceso")):
        sub.add_parser(name, help=h).add_argument("licencia", help="clave o parte del nombre del titular")
    lb = sub.add_parser("liberar", help="desvincula equipos (para que la clave se active en otro PC)")
    lb.add_argument("licencia")
    lb.add_argument("equipo", nargs="?", default="", help="nombre o inicio de la huella (por defecto, todos)")
    ch = sub.add_parser("cambiar", help="cambia límites o datos")
    ch.add_argument("licencia")
    for a in ("--titular", "--email", "--notas", "--caduca"):
        ch.add_argument(a)
    ch.add_argument("--equipos", type=int)
    ch.add_argument("--dias", type=int)
    a = p.parse_args(argv)
    adm = admin or Admin(os.environ.get("PHOOTFLOW_SUPABASE_URL") or SUPABASE_URL, service_key())

    if a.cmd == "crear":
        lic = adm.create(a.titular, a.email, a.notas, a.equipos, a.dias, _caduca(a.caduca) or None,
                         a.producto)
        print("Licencia creada. Envía esta clave al titular:\n\n    " + lic["clave"] + "\n")
        print(describe({**lic, "equipos": []}))
    elif a.cmd == "listar":
        rows = adm.all()
        print("\n\n".join(describe(x) for x in rows) if rows else "No hay licencias.")
    elif a.cmd == "ver":
        print(describe(adm.find(a.licencia)))
    elif a.cmd in ("revocar", "reactivar"):
        lic = adm.update(adm.find(a.licencia), estado="revocada" if a.cmd == "revocar" else "activa")
        print(f"{lic['titular']} ({lic['clave']}): {lic['estado']}. La app lo notará en la próxima comprobación "
              f"(al abrirla o cada 6 h) o, sin conexión, en {lic['dias_offline']} días como mucho.")
    elif a.cmd == "liberar":
        n = adm.release(adm.find(a.licencia), a.equipo)
        print(f"{n} equipo(s) liberado(s).")
    elif a.cmd == "cambiar":
        lic = adm.find(a.licencia)
        cambios = {k: v for k, v in (("titular", a.titular), ("email", a.email), ("notas", a.notas),
                                     ("max_equipos", a.equipos), ("dias_offline", a.dias)) if v is not None}
        if a.caduca is not None:
            cambios["caduca"] = _caduca(a.caduca) or None
        if not cambios:
            raise SystemExit("No has indicado ningún cambio.")
        print(describe({**adm.update(lic, **cambios), "equipos": lic.get("equipos", [])}))


if __name__ == "__main__":
    main()
