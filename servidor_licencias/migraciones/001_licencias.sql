-- Servidor de licencias de PhootFlow (Supabase).
-- Solo el rol de servicio (la Edge Function y admin_licencias.py) lee y escribe: RLS activo y sin políticas,
-- así que la clave pública «anon» no puede ver nada.

create table if not exists public.licencias (
    id            uuid primary key default gen_random_uuid(),
    clave         text not null unique,
    titular       text not null default '',
    email         text not null default '',
    notas         text not null default '',
    estado        text not null default 'activa' check (estado in ('activa', 'revocada')),
    max_equipos   integer not null default 1 check (max_equipos >= 1),
    dias_offline  integer not null default 7 check (dias_offline between 1 and 365),
    caduca        timestamptz,                -- null: no caduca
    creada        timestamptz not null default now(),
    cambiada      timestamptz not null default now()
);

create table if not exists public.equipos (
    id           uuid primary key default gen_random_uuid(),
    licencia_id  uuid not null references public.licencias(id) on delete cascade,
    huella       text not null,
    nombre       text not null default '',
    version      text not null default '',
    primera      timestamptz not null default now(),
    ultima       timestamptz not null default now(),
    unique (licencia_id, huella)
);
create index if not exists equipos_licencia_idx on public.equipos (licencia_id);

-- Par de claves de firma de los tokens (lo genera la Edge Function la primera vez; nunca sale de aquí).
create table if not exists public.ajustes (
    k  text primary key,
    v  text not null
);

alter table public.licencias enable row level security;
alter table public.equipos   enable row level security;
alter table public.ajustes   enable row level security;

revoke all on public.licencias, public.equipos, public.ajustes from anon, authenticated;
