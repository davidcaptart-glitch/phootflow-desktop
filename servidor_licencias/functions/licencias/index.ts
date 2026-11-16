// Edge Function «licencias» de PhootFlow.
// POST {clave, huella, nombre, version} -> {token, estado}
// POST {accion: "clave_publica"}       -> {clave_publica}
// El token es base64url(JSON) + "." + base64url(firma Ed25519 del primer trozo). La app lo verifica con la
// clave pública incrustada y lo usa sin conexión hasta «hasta».
// Se despliega con verify_jwt = false: la app no lleva ninguna clave de Supabase.
import { createClient } from "jsr:@supabase/supabase-js@2";
import * as ed from "npm:@noble/ed25519@2.1.0";

const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false },
});

const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
const unhex = (s: string) => Uint8Array.from(s.match(/../g)!.map((h) => parseInt(h, 16)));
const b64u = (b: Uint8Array) =>
  btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const json = (code: number, obj: unknown) =>
  new Response(JSON.stringify(obj), { status: code, headers: { "Content-Type": "application/json" } });

let seed: Uint8Array | null = null;

async function signingKey(): Promise<Uint8Array> {
  if (seed) return seed;
  const { data } = await db.from("ajustes").select("v").eq("k", "firma_privada").maybeSingle();
  if (data) return (seed = unhex(data.v));
  const nuevo = ed.utils.randomPrivateKey();
  const pub = await ed.getPublicKeyAsync(nuevo);
  const { error } = await db.from("ajustes").insert([
    { k: "firma_privada", v: hex(nuevo) },
    { k: "firma_publica", v: hex(pub) },
  ]);
  if (error) { // otra instancia la creó a la vez
    const r = await db.from("ajustes").select("v").eq("k", "firma_privada").single();
    return (seed = unhex(r.data!.v));
  }
  return (seed = nuevo);
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return json(405, { error: "metodo" });
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return json(400, { error: "peticion" });
  }

  if (body.accion === "clave_publica") {
    await signingKey();
    const r = await db.from("ajustes").select("v").eq("k", "firma_publica").single();
    return json(200, { clave_publica: r.data?.v });
  }

  const clave = String(body.clave ?? "").trim().toUpperCase();
  const huella = String(body.huella ?? "").trim().toLowerCase();
  if (!clave || clave.length > 64 || !/^[0-9a-f]{16,64}$/.test(huella)) return json(400, { error: "peticion" });
  const nombre = String(body.nombre ?? "").slice(0, 80);
  const version = String(body.version ?? "").slice(0, 20);

  const { data: lic, error: e1 } = await db.from("licencias").select("*").eq("clave", clave).maybeSingle();
  if (e1) return json(500, { error: "servidor" });
  if (!lic) return json(404, { error: "clave_no_valida" });

  const ahora = new Date();
  const caduca = lic.caduca ? new Date(lic.caduca) : null;
  const estado = lic.estado === "revocada" ? "revocada" : caduca && caduca <= ahora ? "caducada" : "activa";

  const { data: eqs } = await db.from("equipos").select("id,huella,nombre").eq("licencia_id", lic.id);
  const lista = eqs ?? [];
  const mio = lista.find((e) => e.huella === huella);
  if (mio) {
    await db.from("equipos").update({ ultima: ahora.toISOString(), nombre, version }).eq("id", mio.id);
  } else if (estado === "activa") {
    if (lista.length >= lic.max_equipos) {
      return json(409, { error: "limite_equipos", equipos: lista.map((e) => e.nombre) });
    }
    await db.from("equipos").insert({ licencia_id: lic.id, huella, nombre, version });
  }

  const emitido = Math.floor(ahora.getTime() / 1000);
  let hasta = emitido + lic.dias_offline * 86400;
  if (caduca) hasta = Math.min(hasta, Math.floor(caduca.getTime() / 1000));
  const datos = { v: 1, clave, huella, estado, titular: lic.titular, emitido, hasta, caduca: lic.caduca };
  const p = b64u(new TextEncoder().encode(JSON.stringify(datos)));
  const firma = await ed.signAsync(new TextEncoder().encode(p), await signingKey());
  return json(200, { token: p + "." + b64u(firma), estado });
});
