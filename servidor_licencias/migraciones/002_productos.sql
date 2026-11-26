-- Licencias por producto: «escritorio» (PhootFlow para Windows), «lite» y «pro» (apps móviles).
-- Las claves que ya existen son de escritorio. Una clave Pro abre también Lite.
alter table public.licencias
    add column if not exists producto text not null default 'escritorio'
        check (producto in ('escritorio', 'lite', 'pro'));
create index if not exists licencias_producto_idx on public.licencias (producto);

-- Preparado para las apps nativas (Android / iOS): la licencia saldrá de una compra en la tienda.
-- origen: 'manual' (clave creada por David), 'google_play', 'app_store' o 'web' (pago en la web).
-- compra_ref: identificador de la compra en la tienda (purchaseToken de Google Play, originalTransactionId de
-- Apple). Único: la misma compra no crea dos licencias; al «restaurar compras» se recupera la misma.
alter table public.licencias
    add column if not exists origen text not null default 'manual'
        check (origen in ('manual', 'google_play', 'app_store', 'web')),
    add column if not exists compra_ref text;
create unique index if not exists licencias_compra_ref_idx on public.licencias (origen, compra_ref)
    where compra_ref is not null;
