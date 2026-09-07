---
title: Inventario vivo y contrato API
type: architecture-contract
status: design-baseline
last_updated: 2026-09-07
owner: P2-inventory-api
---

# Inventario vivo y contrato API

## 1. Propósito, alcance y lenguaje normativo

Este contrato define cómo Cardeex reconcilia presencia, publica inventario profesional por dealer y punto de venta, conserva historia y entrega snapshots, eventos y webhooks sin huecos. No implementa producto, no concede acceso a fuentes y no convierte una observación en una verdad comercial que la evidencia no sostenga.

Las palabras **DEBE**, **NO DEBE**, **DEBERÍA** y **PUEDE** son normativas. El contrato HTTP ejecutable es [`contracts/openapi.yaml`](../../contracts/openapi.yaml), fijado a OpenAPI 3.1.2. Si el YAML contradice este documento, el área afectada queda bloqueada hasta corregir ambos; no se elige silenciosamente una versión.

P2 recibe de P0 `SourceRegistration`, `Surface`, `Stream`, `scope_revision`, `listing_namespace_id` y admisión; de P1 recibe `ListingIdentity`, `Offer`, `Vehicle`, observaciones, afirmaciones temporales y evidencia; de P3 recibe ejecuciones, particiones, barreras, salud y retención. P2 posee la reconciliación, las generaciones publicadas, las membresías efectivas, las secuencias de inventario y la interfaz de consumidores.

Fuera de alcance: estrategia de descubrimiento, recetas de scraping, infraestructura física, facturación, búsqueda textual, analítica y gestión pública de suscripciones webhook.

## 2. Referencias técnicas primarias

- [OpenAPI Specification 3.1.2](https://spec.openapis.org/oas/v3.1.2.html), versión exacta del documento estático. OAS 3.1 usa el dialecto de JSON Schema 2020-12 y permite describir webhooks iniciados fuera de una operación API.
- [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html), `application/problem+json` y extensiones de error específicas del dominio.
- [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html), semántica HTTP: `404` puede ocultar un recurso prohibido, `410` expresa indisponibilidad permanente del recurso de continuación y `Retry-After` comunica espera temporal.
- [RFC 9562](https://www.rfc-editor.org/rfc/rfc9562.html), UUIDv7 para IDs de entidades y eventos creados por Cardeex. El tiempo embebido ayuda al orden físico, pero **no** sustituye `observed_at`, `recorded_at` ni `seq` y nunca se usa para atribuir causalidad.
- [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259.html), que solo garantiza interoperabilidad exacta general de enteros JSON hasta `2^53-1`; por ello una secuencia uint64 se representa como cadena decimal.
- [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html), JSON Canonicalization Scheme (JCS) usado para fijar el digest reproducible del núcleo de manifiestos bulk.

Las decisiones sobre snapshots, reconciliación, cursores y membresía son decisiones nuevas de Cardeex, no afirmaciones de esos estándares.

## 3. Vocabulario y fronteras

### 3.1 Identidades

| Término | Identidad y responsabilidad | No significa |
|---|---|---|
| `tenant_id` | Cliente de la API y frontera de autorización. | Dealer, vendedor, fuente ni propietario del dato. |
| `source_id` | Sistema publicador reconocido. | Dominio web, dealer o credencial. |
| `surface_id` | Vía concreta de acceso de una fuente. | Universo reconciliable por sí sola. |
| `stream_id` | Universo lógico monitorizable con alcance versionado. | Partición, ejecución o dealer resuelto. |
| `seller_id` | Vendedor de P1. | Entidad legal o POS necesariamente. |
| `dealer_id` | Nombre API del mismo UUID de un `Seller` cuya clasificación profesional efectiva permite inventario Cardeex. Es una proyección, no otra entidad. | Solo concesionario oficial; también puede ser compraventa, taller, desguace de vehículos completos u otro profesional in-scope. |
| `point_of_sale_id` | POS físico o virtual de P1 respaldado por evidencia. | Dirección textual, publisher account o ubicación del anuncio. |
| `listing_id` | Incarnación estable de un anuncio dentro de un namespace de origen. | Vehículo ni stock profesional deduplicado. |
| `offer_id` | Oferta comercial publicada por un listing. Aquí viven precio, moneda y condiciones. | Atributo del vehículo. |
| `vehicle_id` | Hipótesis reversible del activo canónico. | Prueba de propiedad, venta o presencia. |
| `inventory_id` | Proyección estable y global del inventario de un dealer, independiente de tenants. | Una copia por cliente, clase o POS. |
| `inventory_item_id` | Posición lógica de stock del dealer, respaldada por una o más ofertas/listings. | Sinónimo obligatorio de vehicle: puede no estar resuelto. |

Todos los IDs de entidad generados por Cardeex son UUIDv7 en minúsculas. `scope_revision`, `incarnation` y cada `seq` son cadenas decimales sin signo. `seq` cubre todo uint64 (`0` a `18446744073709551615`) y se compara como entero, nunca lexicográficamente. Todas las fechas son RFC 3339 UTC con sufijo `Z`.

### 3.2 DealerInventory

Existe como máximo un `DealerInventory` activo por `dealer_id`. Su identidad no depende de tenant, fuente, POS, clase, país, receta ni dominio. Los consumidores filtran la misma proyección; no se materializa una base distinta para cada cliente.

Un `InventoryItem` representa una posición de stock profesional durante una tenencia comercial. Puede agrupar varios listings del mismo dealer y puede apuntar a cero o un `vehicle_id` efectivo. Un split, merge o cambio de dealer crea decisiones y eventos; nunca reescribe la evidencia ni el historial publicado.

La clasificación de seller profesional y las relaciones `offer offered_by seller` y, opcionalmente, `offer fulfilled_at point_of_sale` proceden de afirmaciones temporales de P1. P2 almacena en la proyección el dealer/POS efectivo junto con `relationship_assertion_ids`, `resolution_decision_id`, versión y confianza. No inserta `seller_id` mutable en el listing u offer canónicos.

En paralelo, un `StreamInventory` es la proyección estable de listings observados dentro de un `stream_id` y su `scope_revision`. Incluye vendedores profesionales, particulares y no resueltos. No deduplica stock entre dealers ni crea un dealer/POS «unknown»: permite consultar esa cobertura mediante snapshots y eventos source-scoped con las mismas reglas de corte, autorización y cursor que `DealerInventory`.

### 3.3 Membresía M:N sin doble conteo

El grafo de candidatos es M:N: un listing puede tener varias asignaciones candidatas y un item puede estar respaldado por varios listings. Cada `InventoryMembership` usa uno de estos estados:

- `candidate`: existe evidencia, pero no afecta a la vista ni a totales;
- `effective`: asignación publicada;
- `rejected`: la evidencia se evaluó y no sostiene la asignación;
- `superseded`: una decisión posterior sustituyó la asignación, preservando historia.

En una generación, un `listing_id` puede tener **como máximo una** membresía `effective`; un item puede tener muchas. Un listing multiproducto que no se pueda separar queda en reconciliación y no se fuerza a varios items. Reasignar una membresía es una nueva decisión que emite eventos en el inventario anterior y el nuevo.

El POS efectivo es nullable y lleva `assignment_state = assigned | unassigned | ambiguous | withheld`. Solo `assigned` tiene `point_of_sale_id`. Una dirección observada o el nombre de una sucursal **no** crean un POS. Si hay varias candidatas sin ganadora, el stock permanece en el dealer con POS `ambiguous`.

Los totales se calculan así, dentro del mismo snapshot:

- `inventory_item_count`: `inventory_item_id` distintos y presentes;
- `listing_count`: listings presentes con membresía efectiva;
- `resolved_vehicle_count`: `vehicle_id` distintos, solo donde está resuelto;
- `unresolved_vehicle_item_count`: items sin vehículo efectivo;
- `unassigned_point_of_sale_item_count`: items sin POS `assigned`.

Los candidatos no cuentan. Un item con tres listings cuenta como un item y tres listings. El total del dealer no se obtiene sumando POS cuando hay stock no asignado; el contrato publica ese remanente. Los contadores solo son cero si la vista correspondiente existe y contiene cero. Una vista publicada conocida puede devolver el último contador conocido junto con frescura `late` y adquisición `unavailable`; `null` se reserva para cuando no existe baseline o subset autorizado suficiente para definirlo. Todo contador se recalcula sobre el subconjunto autorizado dentro del mismo snapshot: nunca se filtran filas conservando un total global que revele datos.

### 3.4 Precio y atributos comerciales

El precio pertenece a `Offer`. Un `InventoryItem` puede exponer varias ofertas activas y una `display_offer_id` elegida por una regla versionada, pero no posee un precio canónico. Un cambio de precio:

1. agrega una observación de atributos de oferta;
2. emite `offer.price_changed` si cambia el valor efectivo;
3. conserva valores divergentes simultáneos entre fuentes;
4. no crea otro listing, item ni vehicle.

`unknown`, `null` y ausencia de campo no son intercambiables. Los campos cuya resolución importe llevan estado (`known`, `unknown`, `not_applicable` o `withheld`) o un objeto de asignación explícito. Un consumidor nunca debe inferir que un precio o POS nulo significa gratis, inexistente o cerrado.

## 4. Ciclos independientes

### 4.1 Presencia del listing

`presence_state` solo admite:

```text
observed_present -> absence_suspected -> not_observed
        |                    |
        +---- positive ------+
        +---- explicit terminal evidence ----> withdrawn
```

- `observed_present`: existe una observación positiva admisible de la misma identidad e incarnación.
- `absence_suspected`: falta en el primer censo completo comparable posterior. Sigue incluido en current y en totales; la sospecha es visible.
- `not_observed`: falta en dos censos completos comparables consecutivos, sin positivo intermedio, y los cierres de ambos censos están separados al menos por la cadencia objetivo aplicable. Sale de current; no se borra.
- `withdrawn`: la fuente produjo evidencia terminal explícita cuya semántica estaba verificada en el manifiesto de capacidades.

Un positivo posterior a `absence_suspected` o `not_observed` vuelve a `observed_present` y emite el cambio correspondiente. La misma clave externa con una nueva `incarnation` no resucita silenciosamente la anterior.

### 4.2 Salud de fuente

`SourceHealth` es una proyección operativa independiente. `healthy`, `degraded`, `rate_limited`, `unavailable`, `suspended` y `unknown` describen capacidad de observación, no presencia de listings. `SLO_UNMET` es un estado de cumplimiento, no una causa de baja.

Un 429, timeout, credencial caducada, fallo de parser, bloqueo, 5xx, TTL vencido o fuente degradada puede cambiar salud y frescura; no mueve `presence_state`. Un inventario viejo durante una incidencia permanece visible como viejo, con alcance y salud, no se transforma en cero.

### 4.3 Disposición del vehículo

`VehicleDisposition` es independiente: `unknown | offered_observed | sold_inferred | sold_confirmed | otherwise_disposed`. Un texto de fuente «vendido» puede retirar el listing con `reason=source_claimed_sold`, pero no confirma la venta del activo canónico. `sold_confirmed` exige evidencia y regla propias; otros listings activos del mismo vehicle son evidencia contradictoria que debe quedar visible.

## 5. Barridos, particiones y certificado

### 5.1 Unidad de cierre

Un «barrido» es un `AcquisitionRun` de clase censo. `run_id` y cada `attempt_id` son UUIDv7 opacos; `run_key` e `idempotency_key` son valores separados para deduplicar o reanudar. El run fija `source_id`, `surface_id`, `stream_id`, `listing_namespace_id`, `scope_revision`, `plan_revision`, ventana y `barrier_id`.

El `scope_revision` y `plan_revision` son cadenas decimales positivas e inmutables; el plan lleva además un digest de contenido verificable. `partition_id` identifica una partición determinista del plan, no un listing. Un checkpoint pertenece a partición + revisiones de scope/plan y no forma identidad.

Cada partición termina en `complete | failed | rate_limited | truncated | cancelled | scope_changed`. Solo `complete` aporta cierre negativo. El barrier se cierra cuando todas las particiones esperadas han terminado, se han persistido manifiestos, se ha deduplicado el conjunto visto y se han ejecutado las comprobaciones de consistencia.

### 5.2 Certificado de completitud

El validador, nunca el conector, emite un `CompletenessCertificate` inmutable con estado:

- `complete`: permite comparar negativos exclusivamente dentro de su `comparison_key`;
- `positive_only`: los positivos son admisibles, pero el run no prueba ausencias;
- `invalid`: evidencia incompatible, manipulada o insuficiente;
- `superseded`: otro certificado corrige su conclusión sin borrarlo.

`comparison_key` es un hash canónico de `source_id + surface_id + stream_id + listing_namespace_id + scope_revision + predicado-union + semántica de enumeración + revisión de capacidad/autorización`. Dos certificados son comparables solo si su clave coincide. Un `plan_revision` distinto puede producir la misma clave únicamente si un verificador demuestra que cubre la misma unión; no basta declararlo.

Un certificado `complete` exige:

1. todas las particiones esperadas `complete` bajo un barrier único;
2. ningún 429, fallo, timeout no resuelto, truncación, cursor inválido ni cambio de scope/autorización;
3. namespace y semántica de terminalidad verificados;
4. recuentos, IDs y manifiestos consistentes con el plan y sus límites;
5. enumeración con snapshot nativo o cierre de una ventana de cambios que demuestre que mutaciones concurrentes no dejaron huecos;
6. cierre de la unión completa cuando la ruta depende de atributos mutables.

Consumir «todas» las páginas de una paginación live no demuestra un censo snapshot. Si altas, bajas o reordenaciones pueden desplazar resultados sin un mecanismo de cierre, el certificado es `positive_only`.

### 5.3 Regla exacta de desaparición

P2 solo puede retirar un listing de current por una de estas dos vías:

**A. Evidencia terminal explícita.** Feed de tombstones, estado terminal tipado o respuesta equivalente para la misma `ListingIdentity` e `incarnation`, siempre que el manifiesto admitido documente que esa señal significa retirada. Un 404/410 desnudo solo cuenta si endpoint, autenticación, salud y semántica permanente estaban verificados; en otro caso es fallo/unknown.

**B. Dos censos comparables.** Tras el último positivo, el listing está ausente de dos certificados `complete` consecutivos con el mismo `comparison_key`; no aparece un positivo entre ellos; y sus cierres están separados al menos por `cadence_target_seconds`. El primero crea `absence_suspected`; el segundo crea `not_observed`.

Una política de fuente puede exigir más censos, pero nunca menos. `positive_only`, `invalid`, certificado caducado, barrido parcial, 429, TTL, salud mala o cambio de alcance no avanzan el contador de ausencia. Además, un run incompleto o no comparable programado entre dos censos rompe su consecutividad: conserva `absence_suspected`, pero reinicia la pareja y exige dos certificados completos posteriores. Un positivo parcial sí restaura `observed_present`.

La pareja se evalúa por la ventana efectiva y el orden de evidencia del source, no por orden de llegada. Cada certificado contiene `window_opened_at`, `window_closed_at` y `evidence_recorded_through`; P2 mantiene un ledger ordenado y recompone la decisión cuando llega evidencia tardía. Un positivo cuyo `observed_at` es posterior al negativo que pretende aplicarse gana aunque se registre tarde. Un censo cerrado antes de ese positivo no puede borrarlo. Si un run parcial tardío cae entre dos censos usados previamente para `not_observed`, rompe la pareja, P2 emite corrección y vuelve a `absence_suspected` salvo que exista otra pareja válida. Empates o relojes de source sin orden fiable abren `presence_conflict`; no retiran. Una corrección de certificado también recompone y compensa mediante nuevos eventos.

### 5.4 Clase, precio y particiones móviles

- **Cambio de clase:** listing e identidad no cambian. Se emite `listing.reclassified`; el censo siguiente debe cerrar la unión de las particiones de clase anterior y candidata. Si el scope no puede observar el destino, no hay evidencia negativa.
- **Partición por precio:** se evita. Si un tope obliga a usarla, los intervalos se solapan, la unión se deduplica por `ListingIdentity` y se cierra como conjunto. Un precio cambiado nunca hace desaparecer el listing ni cambia vehicle.
- **Truncación:** al alcanzar un límite real o sospechado, la partición queda `truncated`, se conserva lo positivo y se crea otro `plan_revision`. No se publica ausencia.
- **Reclasificación durante el run:** un positivo en cualquier partición resetea sospechas. La ausencia solo se prueba contra el seen-set deduplicado tras barrier, nunca por partición aislada.

## 6. Publicación sin contradicciones

### 6.1 Un log aplicable y generaciones atómicas

Cardeex elige una semántica explícita: **toda publicación visible es una generación atómica y todo snapshot es un prefijo exacto del mismo log de proyección**.

1. Las observaciones admitidas viven primero en el ledger de evidencia. No reciben una secuencia de consumidor hasta convertirse en una decisión aplicable a la proyección.
2. Un run abierto puede producir una `positive_batch` generation. Publica atómicamente sus after-images, cambios de atributos o reclasificaciones, pero no una retirada basada en ausencia.
3. Un censo elegible puede producir una `reconciliation` generation con transiciones de presencia. Una tombstone explícita, merge/split, cambio de membresía, corrección o supresión también puede producir su generación sin esperar a otro censo.
4. Los eventos aplicables de una generación reciben un intervalo contiguo de `seq`; `inventory.generation_committed` cierra el intervalo. El puntero visible cambia solo cuando estado, totales, outbox e intervalo son durables.
5. Un snapshot en `snapshot_seq=S` es exactamente el fold de todos los eventos de ese inventario con `seq <= S`; su cursor de eventos empieza después de S. Ningún efecto omitido puede tener una secuencia anterior al corte.
6. Los scopes no afectados se arrastran con su generación, frescura y salud identificables; nunca se reinterpretan como completos.

«Positiva» limita la **evidencia de ausencia**, no promete que cada contador filtrado sea monótono. Un precio o clase observado puede sacar un item de un filtro; una decisión probada de deduplicación o membresía puede reducir items. Cada generación publica before/after coherentes y eventos de entrada/salida de vista. Lo prohibido es retirar presencia porque un run parcial no vio algo. Un run fallido conserva sus positivos admisibles, pero no publica negativos.

No existe un estado visible donde detalle, membresía y total de un mismo snapshot discrepen. Tampoco se salta de una base antigua a observaciones nuevas por fuera del log que usará el handshake.

Una reserva de secuencias abandonada por crash no se reutiliza ni se rellena con hechos ficticios. Antes del siguiente intervalo publicable se persiste un control `inventory.sequence_gap` que declara el rango abandonado; no cambia estado, pero permite que el scanner avance `scanned_through_seq`. Si una restauración implica una ventana real de pérdida, no se fabrica ese control: aumenta `restore_epoch`, todo cursor del epoch anterior devuelve `410`, y el consumidor debe hacer `snapshot_then_stream`.

### 6.2 Versiones y reconstrucción

Cada generación declara `generation_kind`, `projection_version`, `normalization_version`, `identity_version`, `membership_resolution_version`, certificados aplicados cuando correspondan, `through_seq`, scopes cambiados y momento de commit. Las observaciones y decisiones son la fuente reconstruible; caché, buscador y snapshots son derivados.

Un rebuild crea otra generación con `rebuild_of_generation_id` y versiones explícitas. No reescribe snapshots históricos. Una corrección o reasignación emite eventos compensatorios. Supresión por derechos sigue la política transversal: el evento puede conservar una huella no sensible de que hubo supresión, no el contenido prohibido.

### 6.3 Reasignación entre inventarios

La unicidad de membresía efectiva vive en un registro de ownership versionado, no en dos shards que compiten. Una transferencia prepara la generación de salida y la de entrada, ambas ligadas a `transfer_id`; un manifiesto de publicación avanza `ownership_epoch` solo cuando las dos son durables. Antes del gate se ve el owner anterior; después se ve el nuevo. Un fallo intermedio no publica media transferencia. Tanto el write gate como el serving gate validan la tupla owner + listing + scope autorizado, además del epoch: un writer del dealer anterior no puede reinsertar el listing solo por presentar un epoch vigente.

Cada inventario recibe después su evento local con el mismo `correlation_id` y secuencia propia. Los snapshots exponen `ownership_epoch`. Para agregar varios inventarios sin duplicar, el consumidor usa snapshots del mismo epoch o una exportación multi-inventario que lo fije; mezclar snapshots históricos de epochs distintos no tiene garantía de suma global.

### 6.4 Frescura

La frescura usa `observed_at`, no solo `recorded_at`. Cada vista informa por separado `presence_last_confirmed_at`, `delta_verified_through`, `last_full_census_at`, `projection_lag_seconds` y, por campo relevante, `field_last_observed_at`; además publica `measured_at`, edad p50/p95/máxima, objetivo, tier y estado `within_target | late | unknown | unavailable`. Un delta completo sin cambios puede avanzar `delta_verified_through`, pero no finge otra observación de cada atributo. Un 304 o una señal de presencia actualiza presencia únicamente si enlaza un raw previo admisible; no refresca automáticamente precio, kilometraje ni otros campos.

Tiers iniciales de diseño, sujetos a pruebas P3:

| Tier | Delta objetivo | Reconciliación objetivo |
|---|---:|---:|
| T0 | 15 min | 24 h |
| T1 | 6 h | 72 h |
| T2 | 24 h | 7 d |

Son objetivos, no benchmarks logrados. Si una superficie admitida no puede cumplirlos, publica `SLO_UNMET`; no falsifica timestamps ni baja listings.

## 7. Contrato de API

### 7.1 Recursos

El contrato v1 expone:

- dealers profesionales y POS evidenciados;
- `DealerInventory` e items deduplicados;
- `StreamInventory` de listings profesionales, particulares y seller no resuelto;
- snapshots MVCC paginados o materializados en bulk;
- historia de listing;
- celdas de cobertura y certificados de completitud;
- salud de fuente separada;
- eventos por inventario;
- casos y decisiones de reconciliación;
- un webhook saliente estático para eventos. La creación/rotación de suscripciones queda fuera de v1.

Los endpoints y ejemplos completos viven en OpenAPI. Los IDs no autorizados y los inexistentes producen la misma respuesta 404. Las listas filtran antes de paginar. Un 403 solo indica que al token le falta una capacidad general; nunca confirma que un ID privado exista.

### 7.2 Snapshot MVCC y paginación

El bootstrap exacto es:

1. `POST /v1/dealer-inventories/{inventory_id}/snapshots` con filtros, orden y consistencia. Devuelve `snapshot_id`, `generation_id`, `snapshot_seq`, `resume_after_seq`, `event_cursor`, scope normalizado y `expires_at`. `resume_after_seq` es un marcador diagnóstico igual al corte; **no** permite continuar sin el contexto firmado del `event_cursor`.
2. `GET /v1/inventory-snapshots/{snapshot_id}/items` obtiene la primera página; cada respuesta trae un cursor opaco para la siguiente.
3. Todas las páginas leen el mismo snapshot MVCC. El total, si se devuelve, pertenece a ese snapshot.
4. Tras consumirlo, `GET /v1/dealer-inventories/{inventory_id}/events?cursor={event_cursor}` entrega todos los cambios posteriores para exactamente la misma vista.

El cursor está firmado o referenciado server-side y ligado a `snapshot_id`, tenant/principal y revisión de autorización, hash de filtros/scope, orden y expiración. No contiene datos confiables suministrados por el cliente. Reutilizarlo en otro scope devuelve `400 cursor_scope_mismatch`; intentar acceder a un snapshot ajeno devuelve el mismo 404 que uno inexistente.

«MVCC» describe la estabilidad lógica del corte, no una transacción de base de datos abierta durante horas: la implementación materializa o versiona la vista de forma durable y puede reconstruirla por sus versiones.

Snapshot y cursores caducados devuelven `410` con `code=snapshot_expired` o `cursor_expired`. Para no perder filas, el cliente **reinicia desde la primera página de un snapshot nuevo y deduplica por ID**; nunca continúa la posición vieja sobre datos nuevos. El nuevo `event_cursor` sustituye al anterior. Puede haber duplicados durante recuperación, no huecos.

`snapshot_ttl_seconds` vale 1.800 por defecto y como máximo 86.400. `delivery_mode=auto` enruta a materialización asíncrona y bulk si `estimated_item_count > 100000` **o** `estimated_traversal_seconds > 0.5 × snapshot_ttl_seconds`; no es un límite duro de inventario. El POST devuelve `201` si ya está listo o `202` + `Location` si queda `building`, manteniendo fijados corte, filtros y autorización. El GET de estado produce `ready | building | failed`; un snapshot expirado responde `410`.

Bulk publica un núcleo inmutable del mismo `generation_id`, `snapshot_seq`, `resume_after_seq`, `view_id` y subset autorizado. `manifest_sha256` es SHA-256 del JCS del núcleo excluyendo el propio `manifest_sha256` y, en cada parte, `download_url` y `url_expires_at`; renovar el envelope conserva digest, `part_id`, orden, bytes y rangos. El SHA-256 de cada parte cubre exactamente sus bytes gzip. Cada parte es NDJSON gzip e incluye además bytes, cantidad y rango de claves. Un bulk vacío usa `total_items="0"` y `parts=[]`; no fabrica una fila. Uno no vacío exige al menos una parte.

Manifiesto y partes expiran con el snapshot, nunca después de 24 horas. Cada `download_url` dura como máximo 15 minutos y apunta al gateway Cardeex: en **cada** petición, incluido `Range`, el gateway valida OAuth, entitlement vigente y nonce revocable antes de servir bytes; no existe bypass directo al object store. Revocación o erasure impide inmediatamente nuevas lecturas y renovaciones. Cardeex no puede recuperar bytes que un cliente ya descargó, límite que la política de uso debe declarar.

### 7.3 Secuencia y eventos

Cada inventario posee una secuencia uint64 local, creciente y no reutilizable, serializada como cadena decimal. Un evento tiene `event_id` UUIDv7 y exactamente un `seq`; reintentar el mismo efecto con la misma idempotency key no crea otro evento. No se infiere orden entre dos inventarios.

El snapshot entrega un `event_cursor` opaco ligado a inventory, `snapshot_seq`, filtros, orden irrelevante para el stream, tenant/principal, revisión de autorización y epoch. El endpoint de catch-up escanea el log canónico por `seq`, aplica la misma autorización y proyecta cambios de esa vista:

- si un item entra o cambia dentro del filtro, emite `inventory.item_upserted` con after-image completa;
- si deja de cumplir el filtro, se reasigna o sale de current, emite `inventory.item_removed` con ID y razón;
- eventos no visibles crean huecos legítimos de seq en esa vista.

La respuesta siempre entrega `next_event_cursor` y `scanned_through_seq`, incluso si `data` está vacío porque ningún evento escaneado era visible. Repetir un cursor puede repetir eventos. Un mismo cambio canónico conserva `event_id` al proyectarse en varias vistas, por lo que el consumidor deduplica dentro de su vista por `(view_id, event_id)`, no globalmente. Si el punto solicitado ya no está retenido, responde `410 event_retention_exceeded` con la instrucción `snapshot_then_stream` y el mínimo disponible. El cliente crea un snapshot nuevo y continúa desde su cursor.

`seq` y `scanned_through_seq` de un log compartido pueden revelar que hubo actividad aunque el payload quede filtrado. Por ello esa metadata exige autorización explícita para el inventario/log completo; no se promete invisibilidad entre subsets. Si una política exige ocultar incluso el patrón de actividad, el endpoint queda deshabilitado para ese scope hasta servir una vista aislada con secuencia propia. Los contadores, en cambio, siempre se calculan sobre el subset autorizado.

Tipos normativos iniciales:

- `inventory.item_upserted`;
- `inventory.item_removed`;
- `listing.lifecycle_changed`;
- `listing.reclassified`;
- `offer.price_changed`;
- `inventory.membership_reassigned`;
- `observation.corrected`;
- `inventory.generation_committed`.
- `inventory.sequence_gap`.

Los eventos de stock son directamente aplicables: upsert lleva after-image completa y remove el `inventory_item_id`. Los eventos explicativos de lifecycle, precio, corrección y membresía pueden acompañarlos, pero no son el único mecanismo para reconstruir current. Merge/split o reasignación entre dealers emiten eventos compensatorios en cada inventario afectado, con `correlation_id` compartido y secuencias locales diferentes, después del gate de ownership.

### 7.4 Webhooks

Los webhooks son at-least-once. Cada entrega incluye el mismo `event_id` y `view_id`, un `delivery_id` propio y puede llegar duplicada o fuera de orden; `seq` da el orden dentro del inventario. Replay conserva `event_id` y crea otro `delivery_id`; la clave de deduplicación del receptor es `(view_id, event_id)`.

El receptor verifica HMAC sobre timestamp + cuerpo exacto, usa comparación constante, tolerancia temporal configurada y secretos rotables. Un 2xx confirma entrega. Timeout o no-2xx reintenta con backoff exponencial y jitter dentro de la retención; para 429/503 se respeta `Retry-After` dentro de límites operativos. El payload se autoriza y minimiza igual que una lectura API; cola, DLQ, replay y supresión obedecen la retención de P3.

### 7.5 Idempotencia, concurrencia y errores

Toda escritura v1 exige `Idempotency-Key`. Misma clave + mismo principal + mismo endpoint + mismo cuerpo devuelve el resultado original; misma clave con cuerpo distinto devuelve `409 idempotency_conflict`. Una decisión de reconciliación también exige `If-Match`; versión obsoleta devuelve `412 version_conflict`.

Los errores usan RFC 9457 y URI de tipo estable. `code` es la clave programática Cardeex; `detail` es humano y no contiene secretos, SQL, locators privados ni confirmación de recursos no autorizados. 429 y 503 incluyen `Retry-After`. Los cursores nunca se registran completos en logs.

## 8. Cobertura, salud e historia

Una `CoverageCell` conserva la unidad mínima `market_country × vehicle_class × source_type`, versión de universo, numerador, denominador y estado del denominador. `ratio` es `null` salvo unidades, scope y ventana comparables. El endpoint de coverage no suma fuentes, listings, vehicles y dealers como si fueran una unidad.

Un `CompletenessCertificate` prueba un run/stream; un `InventorySnapshot` sirve una proyección a consumidores. Ninguno sustituye al otro. Un snapshot API, aunque se pagine entero, **no** certifica el censo de una fuente.

Historia es append-only/reconstruible dentro de retención: presencia, atributos, ofertas, decisiones y correcciones conservan sus tiempos válido/observado/registrado/publicado. Un endpoint histórico devuelve limitaciones de retención y versiones; ausencia de una entrada antigua no demuestra que nunca existió.

## 9. Invariantes y escenarios negativos

| ID | Invariante | Escenario negativo que DEBE fallar de forma segura |
|---|---|---|
| INV-001 | `dealer_id` es el UUID de un Seller profesional efectivo, no una entidad nueva. | Un publisher particular crea automáticamente un dealer. |
| INV-002 | Tenant, dealer y POS nunca comparten identidad ni ownership implícito. | Crear dos tenants duplica el inventario del dealer. |
| INV-003 | Un POS solo existe con evidencia; la asignación puede quedar unknown/ambiguous. | Una dirección de listing fabrica una sucursal. |
| INV-004 | Candidatos M:N no afectan totales; hay como máximo una membresía efectiva por listing/generación. | Dos candidatos suman dos vehículos. |
| INV-005 | Items, listings y vehicles tienen contadores distintos. | Tres listings del mismo stock se anuncian como tres items deduplicados. |
| INV-006 | Precio pertenece a Offer y no participa en identidad de vehicle. | Un cambio de precio crea un vehicle nuevo. |
| INV-007 | Salud/frescura no cambia presencia. | 429, TTL o credencial vencida retira miles de listings. |
| INV-008 | Un primer miss comparable solo crea `absence_suspected` y sigue contando. | Un censo completo aislado provoca una baja. |
| INV-009 | `not_observed` exige dos misses completos comparables separados por la cadencia y sin positivo. | Dos runs parciales cuentan como dos misses. |
| INV-010 | Solo evidencia terminal de semántica verificada crea `withdrawn`. | Un 404 bajo bloqueo se interpreta como retirada. |
| INV-011 | «Vendido» de una fuente no confirma `vehicle.sold`. | Retirar un listing marca vendido el activo compartido. |
| INV-012 | Barrier y seen-set de la unión cierran antes de reconciliar negativos. | Una partición termina antes y retira items que otra encontrará. |
| INV-013 | Agotar páginas live no prueba completitud sin snapshot o change-window cerrada. | Una inserción desplaza offsets y el run se certifica igualmente. |
| INV-014 | Truncación invalida negativos y exige replan. | Un tope de 10.000 resultados se presenta como inventario completo. |
| INV-015 | Cambios de clase/precio cierran sobre la unión, no una partición mutable. | Una moto reclasificada o coche rebajado desaparece del stock. |
| INV-016 | Un positivo parcial puede publicar una generación atómica de after-images, pero nunca una baja por ausencia; cambios de filtro probados siguen siendo posibles. | Un run fallido reduce current solo porque no vio items. |
| INV-017 | Cada respuesta y total usa un único snapshot MVCC. | La página 2 salta una fila insertada durante paginación. |
| INV-018 | Cursor está ligado a snapshot, scope y autorización. | Un tenant reutiliza el cursor de otro. |
| INV-019 | Tras 410 se reinicia el snapshot completo; no se continúa sobre latest. | Reanudar por la última fila vieja deja un hueco por reordenación. |
| INV-020 | Snapshot entrega `event_cursor` ligado a la vista; el stream aplica todo cambio posterior al `snapshot_seq`. `resume_after_seq` es diagnóstico, no credencial de continuación. | Continuar con un `after_seq` desnudo omite contexto o deja un hueco. |
| INV-021 | `seq` es uint64 decimal string, local por inventory y nunca se reutiliza; reservas abandonadas se declaran con `inventory.sequence_gap`, mientras pérdida por restore cambia epoch y exige resnapshot. | JavaScript redondea secuencias, se reutiliza un rango o un cursor previo al restore continúa como si nada. |
| INV-022 | Webhooks son at-least-once; `event_id` deduplica y `seq` ordena localmente. | Un replay con delivery nuevo duplica stock. |
| INV-023 | Rebuild/corrección publica compensación y versión, no muta historia. | Corregir dealer borra la asignación anterior sin huella. |
| INV-024 | 404 de inexistente y no autorizado es indistinguible. | La API confirma qué dealer privado existe. |
| INV-025 | Unknown/unavailable no se serializa como contador cero ni coverage 100%. | Caída total de fuente publica inventario vacío certificado. |
| INV-026 | Snapshot serving no sirve como certificado de censo de origen. | Leer toda la API Cardeex certifica que el portal fue enumerado. |
| INV-027 | Reasignación entre inventarios usa un gate de `ownership_epoch`; después emite eventos correlacionados con secuencias locales. | Se publica media transferencia y el item queda contado simultáneamente en ambos current. |
| INV-028 | Idempotency key no se reutiliza con payload distinto. | Retry modificado crea dos decisiones incompatibles. |
| INV-029 | Un inventario extremo se materializa async/bulk sobre el mismo corte; tamaño no autoriza paginado inconsistente ni truncación. | Más de 100.000 items se rechazan o se exportan desde otro corte. |
| INV-030 | `StreamInventory` conserva listings particulares/no resueltos sin fabricar dealer, POS ni deduplicación profesional. | Un listing desaparece de la API por no poder asignarle dealer. |
| INV-031 | Presencia, delta, censo, campo y lag de proyección tienen relojes distintos. | Un 304 refresca precio o un delta vacío se presenta como censo completo. |
| INV-032 | Bulk solo se sirve mediante gateway con autorización y nonce revocable por petición. | Una URL directa al object store sigue descargando tras revocación. |

## 10. Puertas de construcción para P4

Antes de implementar serving se deben validar al menos estos fixtures sintéticos:

1. snapshot de varias páginas con inserciones y cambios concurrentes, seguido de catch-up sin huecos;
2. cursor caducado, reinicio completo y deduplicación;
3. dos misses comparables frente a parcial, 429, truncación y paginación live inestable;
4. listing que migra de coche a LCV y entre buckets de precio durante el run;
5. tres listings, dos fuentes, un stock item y POS ambiguo, sin duplicar el total;
6. tombstone que dice vendido mientras otro listing del mismo vehicle sigue activo;
7. reasignación dealer/POS y corrección de observación con replay;
8. webhook duplicado y fuera de orden;
9. cursor e ID cruzados entre tenants sin fuga por status, latencia o cuerpo;
10. rebuild bajo nuevas versiones que reproduce el estado esperado y conserva la corrección.
11. inventario extremo async/bulk, descarga parcial por `Range` y revocación entre partes;
12. listings particular y seller no resuelto accesibles por `StreamInventory` sin dealer sintético;
13. delta vacío, 304 y censo completo actualizando únicamente sus relojes propios.

Los cuantiles de latencia, frescura o throughput usados como acceptance son objetivos con carga, ventana y denominador declarados. Hasta ejecutar esas pruebas no se describen como rendimiento de Cardeex.

## 11. Lecciones históricas contrastadas

La consulta read-only de Cardeep en el commit verificado `e86efe8ed36f6590d39941bc7fec2743af838c4c` dejó estas trazas, usadas como contraste y no como herencia:

- `migrations/0024_source_coverage.sql:13-21` define una única fila de coverage cuya PK es solo `source_key`, sin run ni revisión de scope; `docs/workflows/e2e/02-SCRAPE.md:62-65` confirma además que `harvest_run` no persiste `declared_total`/`captured_distinct`. Esa forma permite que el último agregado de fuente pierda el contexto que hace comparables dos censos. Cardeex exige certificado por run, `scope_revision`, `comparison_key` y barrier.
- `pipeline/recipe_extract_web.py:27,53-83` reconoce `Car`, `Vehicle`, `MotorizedVehicle`, `Motorcycle` y `Product`, mientras `migrations/0003_vehicles_events.sql:4-25` no reserva una clase de vehículo en la fila persistida. Cardeex conserva `vehicle_class` desde la primera observación, aunque sea unknown, y no necesita repetir la adquisición para abrir motos/LCV.
- `pipeline/delta.py:183-201` contiene una buena defensa: rehúsa marcar gone cuando coverage falta, está bajo el floor o fue refutado. Cardeex retiene ese principio fail-closed, pero sustituye el agregado de coverage por evidencia terminal explícita o dos censos completos comparables.

No se copió código, esquema, receta, dato ni grafo del proyecto histórico.

## 12. Trazabilidad INV → acceptance y modelos

| Invariante P2 | Casos normativos relacionados |
|---|---|
| INV-001 | A001, A011, A078 |
| INV-002 | A002, A039, A040 |
| INV-003 | A002, A035 |
| INV-004 | A002, A009, A035, A076, M022, M028 |
| INV-005 | A002, A006, A009, M022 |
| INV-006 | A004, A005, A015 |
| INV-007 | A031, A032, A056, A080, M023 |
| INV-008 | A026, A033, M001 |
| INV-009 | A027, A028, A029, A030, A031, A033, A065, M002–M009, M014–M018, M020, M025 |
| INV-010 | A010, A034, M010, M011 |
| INV-011 | A034, M019 |
| INV-012 | A026, A029, A030, A031, M005, M015, M016 |
| INV-013 | A028, M009 |
| INV-014 | A027 |
| INV-015 | A014, A026, A029, M006, M015 |
| INV-016 | A031, M005, M017 |
| INV-017 | A036, M021 |
| INV-018 | A039, A040, A075, M024, M027 |
| INV-019 | A038, M021 |
| INV-020 | A037, A075, M021, M027 |
| INV-021 | A061, A070, A081 |
| INV-022 | A025, A041 |
| INV-023 | A009, A033, A035, A064, A076, A079, M022, M025, M028 |
| INV-024 | A039, A040 |
| INV-025 | A032, A055, A056, A057 |
| INV-026 | A028, A055, A057 |
| INV-027 | A061, A076, M028 |
| INV-028 | A022 |
| INV-029 | A042, A077 |
| INV-030 | A001, A011, A078 |
| INV-031 | A020, A063, A080 |
| INV-032 | A040, A047, A048, A082 |

Esta tabla enlaza obligaciones de diseño; los casos con `stage=implementation` continúan pendientes hasta ejecutarse contra una implementación real.
