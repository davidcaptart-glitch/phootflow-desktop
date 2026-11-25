"""Personas de una sesión de Events (sin Qt): reconocer caras, agruparlas por persona y exportar sus fotos.

- Reconocimiento: SFace de OpenCV Zoo (`face_recognition_sface_2021dec.onnx`, Apache-2.0, 38,7 MB) con
  `cv2.FaceRecognizerSF`; la cara se alinea con los 5 puntos que ya da YuNet. Se descarga la primera vez
  (`ensure_model`, SHA-256 como `scene_ai`). `describe()` saca de cada foto las cajas normalizadas, la calidad
  y el vector de 128 valores (float16 en base64: `Photo.people`, guardado en `analisis.json`).
- Agrupado (`People.build`): sin número fijo de grupos. Cada cara usable (ni diminuta ni borrosa ni dudosa) se
  une al grupo cuyo centro se le parece más (coseno ≥ `JOIN`); si no, abre grupo; luego se unen los grupos cuyos
  centros se parecen (≥ `MERGE`) y se reasigna. Lo decidido a mano (nombres, uniones, caras sacadas) se guarda
  en `<salida>/.phootflow/personas.json` y manda sobre el agrupado automático: las caras nuevas van a la persona
  más parecida y el resto forma grupos nuevos.
- Personas conocidas (`Known`, `%APPDATA%\\PhootFlow\\personas\\conocidas.json`, solo en este equipo): al nombrar
  (o aceptar) a alguien se guardan sus mejores caras de esa sesión; en otra sesión, un grupo sin nombre que se le
  parece (`KNOWN`, con margen) recibe ese nombre como «reconocida» hasta que lo aceptes o digas «No es X».
- Exportar: `copy_to_folders` copia el JPG de entrega ya revelado a `Personas/<nombre>/` (sin revelar dos veces).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import urllib.request

import cv2
import numpy as np

MODEL_URL = ("https://huggingface.co/opencv/face_recognition_sface/resolve/"
             "3d7082438a6e4551e840c9b2bb60b71e8da4b524/face_recognition_sface_2021dec.onnx")
MODEL_FILE = "face_recognition_sface_2021dec.onnx"
MODEL_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
MODEL_SIZE = 38_696_353

VERSION = 1
MAX_FACES = 12        # caras por foto que se describen (las más grandes): una foto de grupo no se come el tiempo
MIN_PX = 36           # lado mínimo de la cara en la imagen de análisis (1600 px): más pequeña no se reconoce
MIN_SCORE = 0.75      # confianza mínima de YuNet
BLUR_REL = 0.25       # nitidez mínima respecto a la mediana de las caras de la sesión
JOIN = 0.40           # parecido medio mínimo cara ↔ caras del grupo para entrar (SFace: 0,363 = misma persona en LFW)
MERGE = 0.40          # parecido medio mínimo entre las caras de dos grupos para unirlos
MIN_PHOTOS = 2        # grupos sin nombre con menos fotos no se enseñan (caras sueltas del público)
FILE = "personas.json"
FOLDER = "Personas"
KNOWN = 0.45          # parecido medio mínimo (con sus 3 caras más parecidas) con una persona conocida
KNOWN_MARGIN = 0.05   # … y ventaja mínima sobre la segunda más parecida (si no, se deja sin nombre)
KNOWN_PER_SESSION = 12  # caras que se recuerdan de cada persona por sesión


# ---------------------------------------------------------------- modelo

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(dest_dir: str, progress=None) -> str:
    """Ruta del modelo SFace; lo descarga y verifica (SHA-256) si falta. Lanza RuntimeError si no coincide."""
    import net
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, MODEL_FILE)
    if os.path.exists(path) and os.path.getsize(path) == MODEL_SIZE:
        return path
    tmp = path + ".part"
    h = hashlib.sha256()
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "PhootFlow"})
    with net.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
        total = int(r.headers.get("Content-Length") or MODEL_SIZE)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if h.hexdigest() != MODEL_SHA256:
        os.remove(tmp)
        raise RuntimeError("El modelo de reconocimiento de caras no supera la verificación SHA-256")
    os.replace(tmp, path)
    return path


def make_recognizer(path: str):
    """`cv2.FaceRecognizerSF` (uno por hilo: no es seguro entre hilos), o False si no se puede."""
    if not path or not os.path.exists(path) or not hasattr(cv2, "FaceRecognizerSF"):
        return False
    try:
        return cv2.FaceRecognizerSF.create(path, "")
    except Exception:
        return False


# ---------------------------------------------------------------- vectores

def encode(emb: np.ndarray) -> str:
    return base64.b64encode(np.asarray(emb, np.float16).tobytes()).decode("ascii")


def decode(s: str, n: int) -> np.ndarray:
    if not s or not n:
        return np.zeros((0, 128), np.float32)
    a = np.frombuffer(base64.b64decode(s), np.float16).astype(np.float32).reshape(n, -1)
    return _unit(a)


def _unit(a: np.ndarray) -> np.ndarray:
    return a / np.maximum(np.linalg.norm(a, axis=-1, keepdims=True), 1e-6)


def describe(rec, rgb, raw: list) -> dict:
    """Caras de una foto para reconocerlas. `rgb`: imagen de análisis (RGB, 0..1 float o uint8) sobre la que
    trabajó YuNet; `raw`: sus filas (x, y, w, h, 5 puntos, confianza). Devuelve {"v", "b", "e"}:
    b = [[x0, y0, x1, y1, lado_px, confianza, nitidez], …] normalizadas, e = vectores en base64."""
    out = {"v": VERSION, "b": [], "e": ""}
    if rec is False or rec is None or not len(raw):
        return out
    img = np.asarray(rgb)
    if img.dtype != np.uint8:
        img = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    bgr = np.ascontiguousarray(img[..., ::-1])
    h, w = bgr.shape[:2]
    rows = sorted((np.asarray(r, np.float32) for r in raw), key=lambda r: -float(r[2] * r[3]))[:MAX_FACES]
    boxes, embs = [], []
    for r in rows:
        try:
            crop = rec.alignCrop(bgr, r.reshape(1, -1))
            f = rec.feature(crop).reshape(-1)
        except Exception:
            continue
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(g, cv2.CV_32F).var())
        x, y, fw, fh = (float(v) for v in r[:4])
        boxes.append([round(max(0.0, x) / w, 4), round(max(0.0, y) / h, 4), round(min(w, x + fw) / w, 4),
                      round(min(h, y + fh) / h, 4), round(min(fw, fh), 1), round(float(r[14]), 3),
                      round(blur, 1)])
        embs.append(f)
    if embs:
        out["b"], out["e"] = boxes, encode(_unit(np.array(embs, np.float32)))
    return out


# ---------------------------------------------------------------- agrupado

def face_key(photo_key: str, i: int) -> str:
    return f"{photo_key}#{i}"


def photo_of(key: str) -> str:
    return key.rsplit("#", 1)[0]


def _cluster(keys: list, emb: np.ndarray, qual: np.ndarray) -> list[list[int]]:
    """Grupos (índices) sin número fijo, por ENLACE MEDIO (parecido medio con las caras del grupo, no con su
    centro: el centro de caras con ruido se parece de más a cualquiera). Por calidad, cada cara entra en el grupo
    más parecido (≥ `JOIN`) o abre uno; luego se unen los grupos parecidos (≥ `MERGE`) y se reasigna una vez.
    Con vectores unitarios, la media de los productos entre dos grupos es Σa·Σb / (na·nb): basta con las sumas."""
    n = len(keys)
    if not n:
        return []
    lab = np.full(n, -1)
    sums: list = []
    cnt: list = []
    for i in np.argsort(-qual, kind="stable"):
        e = emb[i]
        if sums:
            sims = (np.array(sums) @ e) / np.array(cnt)
            j = int(np.argmax(sims))
            if sims[j] >= JOIN:
                sums[j] = sums[j] + e
                cnt[j] += 1
                lab[i] = j
                continue
        sums.append(e.copy())
        cnt.append(1)
        lab[i] = len(sums) - 1
    S, N = np.array(sums), np.array(cnt, np.float64)
    alive = list(range(len(S)))
    while len(alive) > 1:
        A = S[alive]
        M = (A @ A.T) / np.outer(N[alive], N[alive])
        np.fill_diagonal(M, -1)
        x, y = np.unravel_index(int(np.argmax(M)), M.shape)
        if M[x, y] < MERGE:
            break
        ga, gb = alive[min(x, y)], alive[max(x, y)]
        S[ga] += S[gb]
        N[ga] += N[gb]
        lab[lab == gb] = ga
        alive.remove(gb)
    # reasignar: cada cara al grupo con el que más se parece (sin contarse a sí misma)
    A, NA = S[alive], N[alive]
    pos = {g: k for k, g in enumerate(alive)}
    sims = emb @ A.T
    own = np.array([pos[g] for g in lab])
    rows = np.arange(n)
    sims[rows, own] -= 1.0
    denom = np.tile(NA, (n, 1))
    denom[rows, own] -= 1
    sims = np.where(denom > 0, sims / np.maximum(denom, 1), -1.0)
    sims[rows, own] = np.where(denom[rows, own] > 0, sims[rows, own], 1.0)  # sola en su grupo: se queda
    best = np.argmax(sims, axis=1)
    groups: dict = {}
    for i in range(n):
        j = int(best[i])
        groups.setdefault(j if sims[i, j] >= JOIN else -1 - i, []).append(i)
    return list(groups.values())


class People:
    """Personas de una sesión. `faces`: clave → (vector, caja, calidad); `assign`: clave → persona
    ("" = sacada a mano de su grupo); `names`: persona → nombre."""

    def __init__(self):
        self.faces: dict[str, dict] = {}
        self.assign: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.auto: set[str] = set()            # personas con el nombre puesto por las conocidas (sin confirmar)
        self.rejected: dict[str, list] = {}    # persona -> nombres que el usuario dijo que no son
        self.next = 1

    # ---- datos
    @staticmethod
    def photo_key(ph) -> str:
        return os.path.basename(ph.path)

    def _usable(self, photos) -> None:
        """Caras de las fotos analizadas; se marcan las que valen para reconocer (`ok`)."""
        self.faces = {}
        for ph in photos:
            d = getattr(ph, "people", None) or {}
            b = d.get("b") or []
            if not b or getattr(ph, "error", ""):
                continue
            emb = decode(d.get("e", ""), len(b))
            if len(emb) != len(b):
                continue
            for i, box in enumerate(b):
                self.faces[face_key(self.photo_key(ph), i)] = {"emb": emb[i], "box": box[:4], "px": box[4],
                                                               "score": box[5], "blur": box[6]}
        blurs = [f["blur"] for f in self.faces.values() if f["px"] >= MIN_PX]
        bmin = BLUR_REL * float(np.median(blurs)) if blurs else 0.0
        for f in self.faces.values():
            f["ok"] = f["px"] >= MIN_PX and f["score"] >= MIN_SCORE and f["blur"] >= bmin
            # calidad: grande, nítida y segura (para elegir la cara de muestra y el orden del agrupado)
            f["q"] = min(f["px"], 160) / 160 + min(f["blur"] / max(bmin * 4, 1e-6), 1.0) * 0.5 + f["score"] * 0.5

    def _new_id(self) -> str:
        pid = f"p{self.next}"
        self.next += 1
        return pid

    def build(self, photos, saved: dict | None = None, regroup: bool = False, library=None) -> "People":
        """Agrupa las caras de `photos`. `saved` (personas.json) manda: las caras ya asignadas se quedan donde
        estaban y las nuevas van a la persona más parecida. `regroup`: rehacer todo salvo las personas con nombre
        puesto a mano. `library` (`Known`): las personas sin nombre que se parecen a una conocida de otras
        sesiones reciben su nombre («reconocidas», `auto`)."""
        self._usable(photos)
        saved = saved or {}
        self.names = {k: v for k, v in (saved.get("names") or {}).items() if v}
        self.auto = {p for p in (saved.get("auto") or []) if p in self.names}
        self.rejected = {k: list(v) for k, v in (saved.get("rejected") or {}).items()}
        self.next = max(int(saved.get("next") or 1), 1)
        old = saved.get("assign") or {}
        if regroup:
            for p in self.auto:  # lo reconocido se vuelve a reconocer después de reagrupar
                self.names.pop(p, None)
            self.auto = set()
            old = {k: v for k, v in old.items() if v == "" or v in self.names}
        self.assign = {k: v for k, v in old.items() if k in self.faces}
        new = [k for k, f in self.faces.items() if f["ok"] and k not in self.assign]
        members: dict[str, list[str]] = {}
        for k, pid in self.assign.items():
            if pid:
                members.setdefault(pid, []).append(k)
        if members and new:  # las caras nuevas, a la persona que ya existe más parecida
            pids = list(members)
            C = np.array([np.mean([self.faces[k]["emb"] for k in members[p]], axis=0) for p in pids])
            E = np.array([self.faces[k]["emb"] for k in new])
            sims = E @ C.T  # media de los productos con sus caras (enlace medio)
            rest = []
            for i, k in enumerate(new):
                j = int(np.argmax(sims[i]))
                if sims[i, j] >= JOIN:
                    self.assign[k] = pids[j]
                else:
                    rest.append(k)
            new = rest
        if new:
            E = np.array([self.faces[k]["emb"] for k in new])
            Q = np.array([self.faces[k]["q"] for k in new])
            for g in sorted(_cluster(new, E, Q), key=lambda g: (-len(g), new[g[0]])):
                pid = self._new_id()
                for i in g:
                    self.assign[new[i]] = pid
        alive = set(self.assign.values())
        self.names = {p: n for p, n in self.names.items() if p in alive}
        self.auto &= set(self.names)
        if library is not None:
            self.recognize(library)
        return self

    def centroid(self, pid: str) -> np.ndarray:
        """Media de las caras SIN normalizar: su producto con una cara es el parecido medio con todas ellas."""
        m = [k for k, p in self.assign.items() if p == pid]
        return np.mean([self.faces[k]["emb"] for k in m], axis=0) if m else np.zeros(128, np.float32)

    def recognize(self, library) -> int:
        """Nombre de las personas conocidas a los grupos sin nombre que se les parecen. Si dos grupos son la
        misma persona conocida, se unen. Devuelve cuántos grupos se reconocieron."""
        n = 0
        for pid in self.groups(1):
            if pid in self.names:
                continue
            name, _ = library.match(self.centroid(pid), exclude=self.rejected.get(pid, ()))
            if not name:
                continue
            same = next((p for p, nm in self.names.items() if nm.lower() == name.lower()), None)
            if same:
                self.merge(same, pid)
            else:
                self.names[pid] = name
                self.auto.add(pid)
            n += 1
        return n

    def confirmed(self) -> dict:
        """Personas con nombre puesto (o aceptado) a mano: las únicas que se aprenden para otras sesiones."""
        return {p: n for p, n in self.names.items() if p not in self.auto}

    # ---- consultas
    def members(self, pid: str) -> list[str]:
        return sorted((k for k, p in self.assign.items() if p == pid), key=lambda k: -self.faces[k]["q"])

    def photos_of(self, pid: str) -> set[str]:
        return {photo_of(k) for k, p in self.assign.items() if p == pid}

    def sample(self, pid: str) -> str:
        m = self.members(pid)
        return m[0] if m else ""

    def name(self, pid: str, n: int | None = None) -> str:
        return self.names.get(pid) or f"Persona {pid[1:] if n is None else n}"

    def groups(self, min_photos: int = MIN_PHOTOS) -> list[str]:
        """Personas a enseñar: las que tienen nombre y las que salen en `min_photos` fotos o más; primero
        las que tienen nombre y luego por número de fotos."""
        count: dict[str, set] = {}
        for k, p in self.assign.items():
            if p:
                count.setdefault(p, set()).add(photo_of(k))
        out = [p for p, s in count.items() if p in self.names or len(s) >= min_photos]
        return sorted(out, key=lambda p: (p not in self.names, -len(count[p]), p))

    def match(self, photo_key: str, pids, together: bool = False) -> bool:
        """¿La foto sale con esas personas? (alguna de ellas o, con `together`, todas a la vez)."""
        if not pids:
            return True
        have = {p for k, p in self.assign.items() if p and photo_of(k) == photo_key}
        return set(pids) <= have if together else bool(have & set(pids))

    def index(self) -> dict[str, set]:
        """Foto → personas (para filtrar miles de fotos sin recorrer `assign` cada vez)."""
        out: dict[str, set] = {}
        for k, p in self.assign.items():
            if p:
                out.setdefault(photo_of(k), set()).add(p)
        return out

    # ---- cambios a mano
    def merge(self, keep: str, other: str) -> None:
        if keep == other:
            return
        for k, p in list(self.assign.items()):
            if p == other:
                self.assign[k] = keep
        if other in self.names and (keep not in self.names or (keep in self.auto and other not in self.auto)):
            self.names[keep] = self.names[other]
            if other in self.auto:
                self.auto.add(keep)
            else:
                self.auto.discard(keep)
        self.names.pop(other, None)
        self.auto.discard(other)

    def remove_face(self, key: str) -> None:
        """Saca una cara de su grupo (no vuelve a entrar en ninguno al reagrupar)."""
        if key in self.assign:
            self.assign[key] = ""

    def move_face(self, key: str, pid: str) -> None:
        if key in self.faces:
            self.assign[key] = pid

    def rename(self, pid: str, name: str) -> None:
        name = (name or "").strip()
        self.auto.discard(pid)
        if name:
            self.names[pid] = name
        else:
            self.names.pop(pid, None)

    def accept(self, pid: str) -> None:
        """Dar por bueno el nombre reconocido (desde ahora también se aprende de esta sesión)."""
        self.auto.discard(pid)

    def reject(self, pid: str) -> None:
        """«No es X»: quita el nombre reconocido y no vuelve a ponérselo a este grupo."""
        name = self.names.pop(pid, "")
        self.auto.discard(pid)
        if name:
            self.rejected.setdefault(pid, []).append(name)

    # ---- disco
    def to_json(self) -> dict:
        return {"v": VERSION, "next": self.next, "names": dict(self.names), "assign": dict(self.assign),
                "auto": sorted(self.auto), "rejected": dict(self.rejected)}

    def save(self, work_dir: str) -> None:
        os.makedirs(work_dir, exist_ok=True)
        p = os.path.join(work_dir, FILE)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.to_json(), f, ensure_ascii=False)
        os.replace(p + ".tmp", p)


def load(work_dir: str) -> dict:
    try:
        with open(os.path.join(work_dir, FILE), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- personas conocidas (todas las sesiones)

def library_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return os.path.join(base, "PhootFlow", "personas", "conocidas.json")


def session_key(out_dir: str) -> str:
    return hashlib.sha1(os.path.normcase(os.path.abspath(out_dir)).encode("utf-8")).hexdigest()[:12]


def _decode_all(b: str) -> np.ndarray:
    return decode(b, len(base64.b64decode(b)) // 256) if b else np.zeros((0, 128), np.float32)


class Known:
    """Personas con nombre de sesiones anteriores, solo en este equipo: nombre -> {sesión: caras (base64)}.
    Solo se aprenden las que tú nombraste o aceptaste; al sacar una cara o cambiar el nombre en una sesión,
    lo aprendido de esa sesión se rehace (`sync`)."""

    def __init__(self, path: str = ""):
        self.path = path or library_path()
        self.people: dict = {}
        self._embs: dict = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.people = {n: dict(v) for n, v in (d.get("people") or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            self.people = {}
        self._index()

    def _index(self):
        self._embs = {}
        for name, by in self.people.items():
            parts = [a for a in (_decode_all(b) for b in by.values()) if len(a)]
            if parts:
                self._embs[name] = np.concatenate(parts)

    def names(self) -> list:
        return sorted(self._embs, key=str.lower)

    def match(self, c: np.ndarray, exclude=()) -> tuple:
        """La persona conocida a la que se parece un grupo ("" si ninguna con claridad). `c` es la media de sus
        caras (`People.centroid`), así que cada producto es el parecido medio del grupo con esa cara conocida."""
        ex = {e.lower() for e in exclude}
        scores = []
        for name, E in self._embs.items():
            if name.lower() in ex:
                continue
            s = np.sort(E @ c)[::-1][:3]
            scores.append((float(np.mean(s)), name))
        if not scores:
            return "", 0.0
        scores.sort(reverse=True)
        best, name = scores[0]
        second = scores[1][0] if len(scores) > 1 else -1.0
        if best >= KNOWN and best - second >= KNOWN_MARGIN:
            return name, best
        return "", best

    def sync(self, session: str, people: "People") -> bool:
        """Aprende de esta sesión las personas confirmadas (sus mejores caras) y olvida de esta sesión lo que ya
        no está (caras sacadas, nombres cambiados). Devuelve True si cambió algo."""
        now: dict = {}
        for pid, name in people.confirmed().items():
            keys = people.members(pid)[:KNOWN_PER_SESSION]
            if not keys:
                continue
            key = next((n for n in list(self.people) + list(now) if n.lower() == name.lower()), name)
            embs = np.array([people.faces[k]["emb"] for k in keys])
            if key in now:
                embs = np.concatenate([_decode_all(now[key]), embs])[:KNOWN_PER_SESSION * 2]
            now[key] = encode(embs)
        changed = False
        for name in list(self.people):
            if session in self.people[name] and name not in now:
                del self.people[name][session]
                changed = True
            if not self.people[name]:
                del self.people[name]
        for name, b in now.items():
            if self.people.get(name, {}).get(session) != b:
                self.people.setdefault(name, {})[session] = b
                changed = True
        if changed:
            self._index()
        return changed

    def forget(self, name: str) -> None:
        """Olvidar a una persona en todas las sesiones."""
        for n in [n for n in self.people if n.lower() == name.lower()]:
            del self.people[n]
        self._index()

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"v": VERSION, "people": self.people}, f)
        os.replace(self.path + ".tmp", self.path)


# ---------------------------------------------------------------- exportar

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def folder_name(name: str) -> str:
    """Nombre válido de carpeta de Windows a partir del nombre de la persona."""
    s = _BAD.sub("_", (name or "").strip()).rstrip(". ")
    if s.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?i)(COM|LPT)\d", s):
        s += "_"
    return s or "Persona"


def copy_to_folders(out_dir: str, folders: dict, sizes, delivery_path, delivery_size, progress=None) -> int:
    """Copia el JPG de entrega ya revelado (color y, si lo hay, B/N) de cada foto a `Personas/<nombre>/`.
    `folders`: nombre → nombres de foto (sin extensión). No revela nada ni pisa una copia igual.
    Devuelve cuántos archivos copió."""
    size = delivery_size(sizes)
    jobs = []
    for name, stems in folders.items():
        base = os.path.join(out_dir, FOLDER, folder_name(name))
        for stem in stems:
            for bw in (False, True):
                src = delivery_path(out_dir, stem, size, bw, sizes)
                if os.path.exists(src):
                    dst = os.path.join(base, "Blanco y negro", stem + ".jpg") if bw else \
                        os.path.join(base, stem + ".jpg")
                    jobs.append((src, dst))
    n = 0
    for i, (src, dst) in enumerate(jobs):
        try:
            s = os.stat(src)
            d = os.stat(dst) if os.path.exists(dst) else None
            if d is None or d.st_size != s.st_size or d.st_mtime_ns < s.st_mtime_ns:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                n += 1
        except OSError:
            pass
        if progress:
            progress(i + 1, len(jobs))
    return n
