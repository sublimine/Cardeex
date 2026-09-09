---
title: Runtime, operaciones y capacidad de Cardeex
type: architecture-contract
status: design-baseline
last_updated: 2026-09-07
owner: Elias
normative_ids: OPS-001..OPS-087
capacity_contract: ../../contracts/capacity-model.json
---

# Runtime, operaciones y capacidad

## 1. Estado, alcance y lenguaje normativo

Este documento fija por mandato la base de diseño del runtime de Cardeex. No describe un sistema ya desplegado, no contiene resultados de carga y no autoriza adquisición desde ninguna fuente. Las estrategias y el software local de descubrimiento están en [05-discovery-system.md](05-discovery-system.md); las recetas de navegación y adquisición/scraping de inventarios se decidirán después. Las decisiones arquitectónicas son baseline; sus objetivos operativos siguen sin probar hasta superar los gates declarados.

Las palabras **DEBE**, **NO DEBE**, **DEBERÍA** y **PUEDE** son normativas. Un incumplimiento de un DEBE impide declarar conforme el componente o perfil afectado.

- **OPS-001 — Estado honesto.** Toda cifra de capacidad, SLO, RPO o RTO de este documento es un objetivo de diseño sin validar hasta superar sus gates y drills. La interfaz y los informes DEBEN mostrar proposed, tested o production-proven; nunca inferir uno de otro.
- **OPS-002 — Frontera.** La unidad raíz de ejecución es Stream y no un dealer, dominio mutable ni vehículo. Cada fallo DEBE poder aislarse al menos por Partition y Stream, y cuando proceda por Surface, origin, credential y country.
- **OPS-003 — Alcance permitido.** La allowlist inicial del runtime es car, lcv, motorcycle y motorhome. Un conector autorizado PUEDE adquirir varias clases en un Stream mixto cuando Surface, ruta, derechos, cadence, particionado y cierre comparable son comunes; el scope enumera todas esas clases y una sola captura admisible se normaliza sin repetir la pasada. Si cualquiera de esas fronteras difiere, usa Streams separados. Motocicletas se pueden conservar desde el principio y certificar después. Trailer, caravan, construction_machine y agricultural_machine quedan bloqueados por defecto.
- **OPS-004 — Cobertura separada.** País, clase de vehículo, tipo de fuente y estado de acceso DEBEN conservar denominadores separados. Incorporar motos nunca modifica retroactivamente la certificación de turismos.
- **OPS-005 — Sin estrategia de adquisición aquí.** Este contrato recibe planes de trabajo autorizados y define cómo ejecutarlos, conservar evidencia y servir resultados. No decide cómo localizar fuentes, evadir barreras ni construir recetas.

La terminología se contrastó en modo de solo lectura con inventarios históricos de Cardex y Cardeep únicamente para detectar ambigüedades. Las definiciones siguientes se deciden de nuevo para Cardeex; no implican herencia de arquitectura, código o datos.

## 2. Identidad operativa y evidencia

### 2.1 Jerarquía estable

```text
SourceRegistration (source_id)
  -> Surface (surface_id)
    -> Stream (stream_id)
      -> scope_revision + plan_revision
        -> AcquisitionRun (run_id, run_key)
          -> Partition (partition_id)
            -> FetchAttempt (fetch_attempt_id)
              -> RawCapture (raw_capture_id)
                -> ObservationEnvelope
```

- **OPS-006 — Stream raíz.** stream_id DEBE ser un UUID estable y opaco. No se recompone a partir de país, dominio, dealer o credencial.
- **OPS-007 — Revisiones inmutables.** scope_revision y plan_revision DEBEN ser cadenas decimales monotónicas. El primero fija alcance y el segundo identifica la revisión registrada del plan. plan_sha256 es SHA-256 de los bytes exactos del artefacto canónico inmutable al que apunta el registro de plan: el campo digest y las firmas quedan fuera del payload hasheado, por lo que no existe autorreferencia. El perfil de codificación/canonicalización pertenece a la versión del artefacto y no se cambia implícitamente. Un digest sintético de fixture solo prueba forma, no prueba esos bytes. Cambiar alcance o contenido crea otra revisión; no sobrescribe la anterior.
- **OPS-008 — Identificadores sin causalidad falsa.** run_id y fetch_attempt_id DEBEN ser UUIDv7 opacos. El orden causal no se deduce de ellos. run_key DEBE ser determinista sobre stream_id, scope_revision, plan_revision, ventana programada y run_kind. idempotency_key es independiente del ID opaco.
- **OPS-009 — Partición reproducible.** partition_id DEBE ser UUIDv7 opaco registrado. partition_key y predicate_sha256 son deterministas y permiten encontrar o deduplicar la partición dentro de stream_id + scope_revision + plan_revision. Reintentar conserva partition_id y crea fetch_attempt_id nuevo. barrier_id identifica el conjunto esperado y la etapa. La codificación canónica de run_key, partition_key e idempotency_key DEBE estar versionada y probada byte a byte.
- **OPS-010 — ListingIdentity.** La interfaz wire es listing_id, listing_namespace_id, source_listing_key e incarnation. La identidad externa es listing_namespace_id + source_listing_key + incarnation. Source, Surface, país y clase solo separan identidades si el registro prueba namespaces distintos. Dealer, publisher, seller, legal_entity y point_of_sale nunca forman parte de la clave ni del routing: pueden remapearse con nueva evidencia.
- **OPS-011 — Run como evidencia.** AcquisitionRun DEBE registrar run_kind —incremental, reconciliation, backfill o reprocess—, conjunto esperado de Partition, completadas, fallidas, truncadas y cambiadas. Un run parcial no se presenta como snapshot completo.
- **OPS-012 — Secuencia.** Una secuencia de productor, cuando exista, DEBE codificarse como uint64 decimal y solo ordena dentro de su ordering_key. No es reloj global.

Una respuesta 304 o equivalente demuestra presencia y referencia prior_raw_capture_id; no crea por sí sola un nuevo attribute_snapshot. Una ausencia en un run parcial, truncado, limitado o con 429 no demuestra baja.

### 2.2 Canonicalización criptográfica v1

El perfil `cardeex-jcs-sha256-v1` aplica JSON Canonicalization Scheme de RFC 8785, codificación UTF-8 sin BOM y SHA-256; el resultado es hexadecimal minúsculo [9]. Los producers validan primero el objeto tipado, no concatenan strings y no normalizan Unicode silenciosamente. Revisiones, epochs, secuencias, contadores y cantidades exactas se representan en la preimagen como strings decimales canónicos —`0` o `[1-9][0-9]*` según admita el campo— y nunca como float JSON.

- `PlanArtifactV1` contiene exactamente `artifact_type`, `schema_version`, `stream_id`, `scope_revision`, `plan_revision`, `capability_manifest_id`, `partitioning_semantics`, `reconciliation_boundary`, `max_partitions`, `max_requests`, `max_runtime_seconds` y `partitions`. El array se ordena ascendentemente por partition_key. Cada elemento contiene exactamente `partition_id`, `partition_key`, `predicate_ref`, `predicate_sha256`, `interval_convention`, `parent_partition_id` y `saturation_policy`. plan_sha256 hashea ese payload; excluye plan_id, plan_sha256, firmas, planning_evidence_refs, created_at y cualquier envelope de registro.
- `RunKeyV1` contiene exactamente `key_profile=cardeex.run-key.v1`, `stream_id`, `scope_revision`, `plan_revision`, `run_kind`, `window_start` y `window_end`. No contiene run_id.
- `PartitionKeyV1` contiene exactamente `key_profile=cardeex.partition-key.v1`, `stream_id`, `scope_revision`, `plan_revision` y `predicate_sha256`. No contiene partition_id. Dos predicados iguales dentro del mismo contexto son la misma partición lógica y el registro rechaza el duplicado.
- Cada idempotency_key declara su propio `key_profile` y objeto `logical_effect` con lista cerrada en el schema del evento. Un valor libre concatenado o una preimagen sin versión se rechaza.

Vector normativo mínimo de `RunKeyV1`; la segunda línea son los bytes ASCII/UTF-8 canónicos exactos, sin newline final:

```text
input fields: key_profile=cardeex.run-key.v1; stream_id=01992000-0000-7000-8000-000000000005; scope_revision=1; plan_revision=1; run_kind=reconciliation; window_start=2026-09-07T00:00:00Z; window_end=2026-09-08T00:00:00Z
{"key_profile":"cardeex.run-key.v1","plan_revision":"1","run_kind":"reconciliation","scope_revision":"1","stream_id":"01992000-0000-7000-8000-000000000005","window_end":"2026-09-08T00:00:00Z","window_start":"2026-09-07T00:00:00Z"}
SHA-256 = ce52081c1fe64ea208080e1215989be095a63dfb64262b58c6cc666f102d2ecf
```

El mismo vector DEBE producir idénticos bytes y digest en todas las implementaciones. Cambiar campos, tipos, normalización, orden de arrays o perfil abre una versión nueva; no se acepta mediante fallback.

## 3. Arquitectura en tres planos

```text
                         +-----------------------------+
                         |        Control plane        |
                         | PostgreSQL + policy + lease |
                         +-------------+---------------+
                                       |
                               outbox / work intent
                                       v
 +----------------+       +------------+------------+       +------------------+
 | authorized     | ----> |          Data plane     | ----> | Serving plane    |
 | work plans     |       | workers + log + objects |       | current + search |
 +----------------+       |      + Iceberg history  |       | derived only     |
                          +-------------------------+       +------------------+
```

### 3.1 Control plane

- **OPS-013 — Autoridad de control.** PostgreSQL, en una versión soportada, DEBE ser la autoridad de SourceRegistration, Surface, Stream, revisiones, políticas, runs, particiones, leases, cursors, barriers, outbox, routing epochs y deletion ledger. No almacena el historial masivo ni un row por cada observación a escala. El [descubrimiento local](05-discovery-system.md) usa SQLite como área previa al handoff; no sustituye esta autoridad productiva ni comparte sus garantías de escala/recuperación.
- **OPS-014 — Transacciones locales.** Creación de trabajo, cambio de estado y su outbox DEBEN ocurrir en la misma transacción PostgreSQL. Los conflictos serializables se reintentan desde el principio; SKIP LOCKED solo se utiliza para colas, porque ofrece una vista inconsistente para consultas generales [1].
- **OPS-015 — Secretos por referencia.** PostgreSQL solo guarda secret_ref y metadatos. El secreto vive en un gestor de secretos con cifrado, rotación y auditoría.
- **OPS-016 — Alta disponibilidad.** El control plane DEBE tener failover probado, PITR y backup lógico verificable. Una réplica no probada no cuenta como recuperación.

### 3.2 Data plane

- **OPS-017 — Log particionado.** Un log compatible con Apache Kafka transporta work intents y eventos con entrega al menos una vez. Kafka conserva orden dentro de una partición y limita su exactly-once a topologías coordinadas; destinos externos requieren cooperación o idempotencia [2]. Cardeex NO DEBE prometer exactly once extremo a extremo.
- **OPS-018 — Workers sin estado.** Schedulers, fetchers, normalizers, media workers y projectors DEBEN ser stateless respecto al progreso durable. Pueden perder memoria y reiniciar desde lease, cursor, raw manifest y log.
- **OPS-019 — Raw en objetos.** Respuestas y media permitidas se almacenan en object storage con checksum, cifrado, lifecycle y versionado donde esté disponible. RawCapture es lógico; los bytes PUEDEN agruparse en bundles inmutables para evitar miles de millones de objetos pequeños.
- **OPS-020 — Manifest fuera de control.** El manifest detallado de RawCapture vive en una tabla Iceberg raw_manifest. PostgreSQL conserva resúmenes de run/partition, commit pointers y excepciones operativas, no cada captura.
- **OPS-021 — Histórico Iceberg.** ObservationEnvelope y sus revisiones normalizadas viven en Parquet bajo Apache Iceberg. Iceberg publica snapshots mediante un cambio atómico de metadata y usa concurrencia optimista; el writer debe reintentar cuando pierde la carrera [3]. Un REST catalog o catálogo equivalente DEBE proporcionar compare-and-swap correcto.
- **OPS-022 — Cómputo.** Reprocesado, compaction y consultas históricas se ejecutan con motores compatibles con Iceberg —por ejemplo Spark o Trino—, separados del serving online.
- **OPS-023 — Media separada.** Imágenes nunca se incrustan en PostgreSQL, Kafka ni la proyección actual. Se sirven mediante proxy/CDN autorizado y referencias revocables.

### 3.3 Serving plane

- **OPS-024 — Proyección reconstruible.** CurrentListingProjection es derivada de observaciones comprometidas. No es fuente de verdad y DEBE poder reconstruirse por shard desde Iceberg y el log retenido.
- **OPS-025 — Ruta de escala concreta.**
  - Hasta el perfil de 10 millones activos, la referencia es PostgreSQL particionado con réplicas de lectura.
  - Para 100 millones, se evalúa PostgreSQL con sharding de aplicación o Citus y un router con routing_epoch.
  - Para 1.000 millones, NO se presupone que la opción anterior baste: se compara con una proyección key-value/wide-column —Cassandra o ScyllaDB como referencias— usando trazas reales. El contrato de proyección permanece estable para permitir sustitución.
- **OPS-026 — Search opcional.** OpenSearch es una proyección secundaria opcional para texto/facetas, nunca autoridad de estado, cobertura o identidad. Su caída no bloquea point reads ni adquisición.
- **OPS-027 — Sin tecnología infinita.** Superar un gate de perfil solo acredita el workload probado, versión, topología y margen medidos. No acredita el siguiente orden de magnitud.

## 4. Ownership y contratos de evento

| Dato o decisión | Único owner durable | Réplicas o derivados |
|---|---|---|
| scope, legal gate, plan, run, lease, cursor, routing | PostgreSQL control | cache de lectura |
| secreto | secret manager | memoria efímera del worker |
| bytes raw/media permitidos | object storage | cache/CDN revocable |
| manifest y observaciones históricas | Iceberg catalog + tablas | Trino/Spark |
| intención/evento en tránsito | log particionado | consumidores |
| estado actual | shard de CurrentListingProjection | read replica/cache |
| texto/facetas | ninguna autoridad propia | OpenSearch reconstruible |
| borrado exigible | deletion ledger de control | tombstones aplicados en todos los planos |

- **OPS-028 — Un owner por hecho.** Ningún consumidor DEBE decidir que su copia es más autoritativa que el owner de la tabla.
- **OPS-029 — Envelope tipado.** La base común de todo evento durable incluye event_id, event_type, schema_version, produced_at, trace_id, idempotency_key y una variante context tipada. AcquisitionContext añade source_id, surface_id, stream_id, revisiones, run/partition/attempt, country y vehicle_class; ProjectionContext añade inventory/generation/sequence; IdentityContext añade entidades afectadas y correlation_id; PolicyContext y ApiContext exigen sus propios campos. Un merge multifuente, una revocación o un evento API NO DEBE inventar source, clase o run para satisfacer un envelope plano. payload_ref y payload_sha256 son obligatorios solo cuando hay payload externo.
- **OPS-030 — Payload pequeño.** Kafka transporta envelopes y referencias, no HTML, imágenes, secretos, contactos ni cuerpos arbitrarios.
- **OPS-031 — Compatibilidad.** Cambios compatibles son aditivos. Un producer no publica una versión incompatible hasta que todos los consumidores críticos la acepten. Eventos desconocidos van a quarantine; no se ignoran silenciosamente.
- **OPS-032 — Idempotencia semántica.** observation_id DEBE ser UUIDv7 opaco. idempotency_key_sha256 o derivation_key determinista permite lookup y evita otra mutación lógica. Reextraer con otra derivation/normalization revision conserva observed_at original y genera una revisión/corrección trazable; no deriva el ID ni finge otra observación.

## 5. Protocolos de commit sin transacción distribuida

### 5.1 Programación y outbox

1. El scheduler abre una transacción serializable.
2. Inserta AcquisitionRun, manifest esperado de Partition y filas de outbox con run_key único.
3. Confirma PostgreSQL.
4. El dispatcher publica cada outbox_id en el log y después marca published_at.
5. Un crash entre 3 y 4 retrasa; entre publicación y marca puede duplicar. El consumidor deduplica por idempotency_key.

- **OPS-033 — Outbox obligatoria.** Queda prohibido el dual-write directo PostgreSQL+Kafka.

### 5.2 Fetch y raw

1. El worker adquiere lease mediante compare-and-set e incrementa lease_epoch.
2. Una transacción corta registra RawBatchIntent con batch_key determinista, object_key exacta, fence, revisiones y límites esperados. No crea una fila ni outbox por captura.
3. El worker ejecuta I/O fuera de PostgreSQL y construye un bundle self-describing: manifest completo de FetchAttempt/RawCapture, payloads direccionables, hashes por registro y hash del bundle.
4. Escribe el bundle inmutable en staging y verifica tamaño y SHA-256. El disco local es spool descartable, nunca el único estado durable.
5. En una transacción corta revalida admisión, worker_id y lease_epoch; registra un summary pointer por batch, avanza cursor por compare-and-set y añade RawBatchCommitted al outbox.
6. Si PostgreSQL cae después del PUT, el reconciler encuentra object_key desde RawBatchIntent y verifica el bundle. Un owner vigente puede adoptarlo solo si fence, scope, tiempo de fetch y política siguen siendo aceptables; en otro caso se cuarentena o elimina según retención.
7. El indexador consume RawBatchCommitted, escribe todas las filas —incluidas 304 y payloads duplicados— en Iceberg raw_manifest y registra su snapshot. barrier_id espera tanto el summary de control como ese commit de manifest.
8. Un GC elimina staging sin referencia después de 72 horas y de consultar intents pendientes; nunca elimina bundles publicados o protegidos por reconciliación.

- **OPS-034 — Fence obligatorio.** Un worker con epoch vencido no puede avanzar cursor, completar Partition ni publicar un resultado aceptado.
- **OPS-035 — Raw verificable.** raw_capture_id es UUIDv7 y siempre identifica un receipt con transporte/resultado, headers permitidos, fetched_at, recipe/plan revision, política y lineage. Solo la variante stored_body referencia bundle, offset/length y SHA-256 de payload; empty_by_protocol y metadata_only_by_policy no inventan bytes ni digest de cuerpo. Deduplicar bytes nunca elimina la fila de evidencia del fetch.

### 5.3 Normalización e Iceberg

1. El consumidor toma un lote acotado, calcula batch_key y registra NormalizationCommitIntent en PostgreSQL antes de escribir.
2. Escribe data files temporales con observation_id, idempotency key y normalization_revision.
3. Hace commit optimista de un snapshot Iceberg que registra batch_key en snapshot summary.
4. Una transacción PostgreSQL verifica ese snapshot, cambia el intent a committed, registra snapshot_id/input watermark y crea NormalizedCommitted en outbox. Solo tras este intent durable se confirma el consumo.
5. Si cae antes del commit Iceberg, el intent permite reintentar y los ficheros no referenciados quedan para GC. Si cae después del commit pero antes del paso 4, PublicationReconciler busca batch_key en los snapshots, completa la transacción y publica el outbox. Si cae tras el paso 4, el outbox se reenvía idempotentemente.
6. Snapshot expiry y orphan cleanup NO DEBEN retirar metadata/data de un batch con intent preparado o committed pero publication_state pendiente. El reconciler tiene un SLO menor que la retención mínima de esos snapshots y bloquea expiry si está atrasado.

- **OPS-036 — Garantía declarada.** El resultado es at-least-once físico con vista lógica idempotente. Compaction puede retirar duplicados físicos; no se declara exactly once.

### 5.4 Proyección actual

1. El projector consume por ordering_key.
2. Construye una generación P2 con before/after coherentes. El sequence allocator del inventario asigna un intervalo público contiguo y monotónico; Kafka offset, outbox_id, Observation seq y public inventory seq son namespaces distintos y nunca se convierten entre sí.
3. Estado, totales, ownership, intervalo público y generation outbox se hacen durables detrás de un único publication pointer. La generación solo se vuelve visible al conmutar ese puntero. Si un crash abandona una reserva sin perder hechos, el coordinador publica antes del siguiente intervalo un control record sequence_gap que consume exactamente el rango no usado y permite avanzar scanned_through_seq; no lo reutiliza ni deja al lector esperando. Si existe loss_window de hechos, no se fabrica ese marcador: cambia restore_epoch, invalida el cursor con 410 y exige otro snapshot.
4. Si target y checkpoint comparten shard PostgreSQL, se actualizan en una transacción local; si no, replays y compare-and-set son obligatorios.
5. Un evento anterior puede enriquecer histórico, pero no reemplaza current con una versión más vieja. Correcciones y merge/split generan eventos compensatorios; no reescriben el log público.

- **OPS-037 — Serving monotónico.** Cada row guarda last_applied_observation_order, last_event_id, routing_epoch, projection_version y normalization_revision. La secuencia pública la posee el inventario P2, no el transporte físico. Dentro de un restore_epoch, cada seq reservado queda cubierto por evento/generación o por un sequence_gap durable explícito; los huecos de una vista filtrada solo significan eventos no visibles y aun así avanzan scanned_through_seq.
- **OPS-038 — Barrier.** Un run solo queda complete cuando todas las particiones esperadas son terminales, ningún truncamiento queda oculto y los commits de raw/histórico alcanzan barrier_id. El serving puede avanzar continuamente y no equivale a ese certificado.

### 5.5 Snapshot API sin transacción larga

- **OPS-081 — Snapshot materializado.** Crear un InventorySnapshot fija generation_id, snapshot_seq, ownership_epoch, entitlement revision, filtros y orden. La implementación DEBE usar una generación durable/versionada o un manifest materializado de IDs; NO DEBE mantener una transacción MVCC PostgreSQL abierta durante páginas u horas, porque retendría versiones y dificultaría vacuum.
- **OPS-082 — Paginación estable.** El page_cursor usa keyset sobre el snapshot/manifest, está firmado o referenciado server-side y ligado a principal, tenant, scope y expiración. Todas las páginas y totales leen el mismo corte. El snapshot entrega además event_cursor opaco ligado a snapshot_seq, filtro, authorization revision y epoch; la continuación empieza estrictamente después del corte y cada página de eventos avanza scanned_through_seq aunque no contenga eventos visibles. Una secuencia desnuda no es token de reanudación.
- **OPS-083 — TTL y tamaño extremo.** TTL default es 1.800 segundos y máximo 86.400. page_size default 100 y máximo 500. Si estimated_item_count supera 100.000 o estimated_traversal_seconds supera la mitad del TTL, delivery_mode=auto elige bulk; no es un límite de inventario. POST fija el mismo corte y devuelve 201 ready o 202 building. El bulk manifest es inmutable, checksumado y ligado a snapshot_seq/autorización; sus parts expiran con el snapshot, como máximo en 24 horas. Cada descarga o Range pasa por un gateway autenticado que revalida principal, tenant, entitlement, snapshot, erasure y un nonce revocable. Su URL opaca dura como máximo 15 minutos y solo se renueva con derechos vigentes; queda prohibido entregar un presigned URL directo de object storage que sobreviva a la revocación.
- **OPS-084 — Caducidad segura.** Tras 410, el cliente crea otro snapshot desde la primera página y deduplica; nunca continúa una posición vieja sobre latest. Revocación o erasure invalida inmediatamente snapshot, cursor, manifest, nonce y acceso en el gateway aunque su TTL no haya vencido. Cardeex no puede revocar remotamente bytes que el cliente ya descargó: el contrato limita conservación/uso y registra la revocación, sin fingir borrado del dispositivo externo.

## 6. Leases, reanudación, retry y backpressure

- **OPS-039 — Lease fenced.** El lease contiene partition_id, owner_worker_id, lease_epoch decimal, acquired_at, heartbeat_at y expires_at. Claim y takeover incrementan epoch de forma atómica.
- **OPS-040 — Defaults de lease.** Duración 120 segundos, heartbeat cada 30 segundos y máximo cuatro heartbeats perdidos antes de takeover. Una operación individual mayor debe trocearse o renovar; no aumentar el lease sin límite.
- **OPS-041 — Cursor.** El cursor pertenece a Partition + scope_revision + plan_revision. Solo avanza tras commit durable y nunca forma identidad del Stream.
- **OPS-042 — Retry transitorio.** Timeout, conexión, 408, 429 y 5xx usan full jitter: random(0, min(900 s, 1 s × 2^attempt)). Retry-After es el mínimo cuando exista. Máximo ocho intentos y 24 horas por trabajo antes de dead letter.
- **OPS-043 — No retry ciego.** 401/403, cambio contractual, bloqueo, CAPTCHA o revocación suspenden el scope para revisión. Parse/schema determinista va a quarantine. Un 404 de listing se registra como evidencia; no derriba el Stream.
- **OPS-044 — Dead letter.** La DLQ contiene envelope, error_class, primer/último fallo, attempts y payload_ref; no duplica datos sensibles. Redrive exige idempotency_key, límite de volumen, actor y motivo auditado.
- **OPS-045 — Quarantine.** Contenido válido pero desconocido, enum nuevo, schema incompatible, parse ambiguo o salida de IA no validada se conserva sin entrar en current ni en certificación.
- **OPS-046 — Backpressure.** Al superar watermark de lag, error de objetos, saturación del control plane o cola de compaction, el scheduler reduce nueva enumeración, conserva deltas prioritarios y deja capacidad de recuperación. Nunca acelera contra una fuente para compensar deuda interna.
- **OPS-047 — Presupuesto jerárquico.** El límite efectivo es el mínimo de global, origin_id, source_id, credential_id, country y Stream. Concurrencia, requests/segundo y bytes/segundo son budgets distintos. No se rotan credenciales ni hosts para eludir un límite.
- **OPS-048 — Límite visible.** Si el permiso o rate limit de una fuente hace imposible la cadence, health y slo_state quedan rate_limited/unmet y el gap permanece en cobertura. Desactivar alertas o cambiar el denominador no es remediación.
- **OPS-049 — Circuit breaker.** Un origin que cruza el umbral abre circuito solo para los scopes que lo comparten. La API, otros países y otros orígenes continúan.

Health usa healthy, degraded, unavailable, rate_limited, suspended o unknown. SLO se evalúa aparte como meeting, at_risk, unmet o not_applicable.

## 7. Hot dealers, hot Streams y routing epochs

- **OPS-050 — Ordering por etapa.** Fetch, observación, normalización y mutación de item usan hash estable de ListingIdentity y no se particionan solo por stream_id o dealer_id. La publicación externa sí tiene secuencia por inventory: un coordinador lógico por inventory/view serializa manifest, intervalo seq y generation pointer en batches mientras el cálculo de items sigue shardeado. No se mezcla esa secuencia con el ordering físico del data plane.
- **OPS-051 — Dealer repartible.** Los listings de un dealer grande se distribuyen por su propia ListingIdentity; las consultas usan un índice derivado. El coordinador de publicación del inventory solo ordena metadata/batches y DEBE tener benchmark de hot-inventory, batch máximo, tiempo de gate y backpressure; no procesa cada payload de forma monolítica.
- **OPS-052 — Stream repartible.** Enumeration work se divide por rangos, hash buckets o ventanas definidos por el plan. Un Partition que excede dos veces la mediana de duración o tamaño durante tres runs es candidato a split.
- **OPS-053 — Hot key indivisible.** Una sola ListingIdentity conserva orden en un owner. Si es anormalmente caliente se coalescen eventos de presencia, se limita input o se aísla; no se rompe el orden para ganar throughput.
- **OPS-054 — Routing epoch.** Toda asignación de work, log o projection lleva routing_epoch monotónico. Un writer de epoch viejo queda fenced.
- **OPS-055 — Repartición segura.** Para pasar de epoch N a N+1: publicar mapa, fijar barrier, drenar hasta watermark, backfill del nuevo shard, catch-up, conmutar reads de forma atómica, rechazar writers N y retirar N después de validación. Nunca existen dos owners autoritativos.
- **OPS-056 — Escala independiente.** Fetch, normalization, media, history y serving tienen pools y quotas separados. Un backlog de imágenes no frena presence ni precio.

Una reasignación entre inventarios prepara salida y entrada bajo transfer_id. El registro único de membership ownership avanza ownership_epoch solo cuando ambos manifests son durables; antes se ve el owner previo y después el nuevo. No se intenta una transacción distribuida entre shards ni se publica media transferencia.

## 8. SLO y error budgets 24/7

Monitorizar 24/7 significa medir de forma continua la deuda de cada Stream; no observar cada listing cada segundo.

### 8.1 Frescura por tier

| Tier | Uso de referencia | Delta watermark | Reconciliación completa |
|---|---|---:|---:|
| T0 | fuente prioritaria con delta autorizado | ≤ 15 min | ≤ 24 h |
| T1 | fuente regular | ≤ 6 h | ≤ 72 h |
| T2 | long tail o baja rotación | ≤ 24 h | ≤ 168 h |

- **OPS-057 — Tres relojes de frescura.** En ventana móvil de 30 días, el 99% de los stream-minutes elegibles DEBE cumplir por separado: delta_watermark_age desde el último delta poll/barrier verificado; last_full_census_age desde el último censo completo comparable; y field_observed_age p50/p95/max para precio, presencia, atributos y media. El objetivo delta de 15 minutos no exige un censo completo cada 15 minutos ni refrescar cada listing. Intentos fallidos no mueven ningún reloj.
- **OPS-058 — Denominador.** Rate limit, error interno o receta rota permanecen en el denominador operativo. Una prohibición legal puede suspender ejecución, pero el scope sigue siendo gap de cobertura con causa y fecha.
- **OPS-059 — Ingest latency.** Desde RawCaptured hasta histórico comprometido: p95 ≤ 30 min y p99 ≤ 2 h bajo carga nominal.
- **OPS-060 — Projection latency.** Desde NormalizedCommitted hasta current: p95 ≤ 15 min. Search opcional: p95 ≤ 60 min.
- **OPS-061 — Serving.** Point-read API: disponibilidad mensual 99,9%, p95 ≤ 300 ms y p99 ≤ 1 s en servidor. Search, si se ofrece: 99,5% y p95 ≤ 2 s. Las respuestas DEBEN exponer observed_at y freshness.
- **OPS-062 — Error budget.** 99,9% permite 43 min 12 s de indisponibilidad en 30 días; freshness 99% permite 1% de stream-minutes fuera de objetivo. A 50% consumido antes de mitad de ventana se congelan cambios de riesgo; a 100% solo se priorizan mitigación y trabajo aprobado de fiabilidad.
- **OPS-063 — Cero budget.** Exposición entre tenants, corrupción silenciosa, bypass de legal gate y resurrección tras erasure son incidentes aunque duren un segundo.

### 8.2 Alertas y runbooks

| Alerta | Default | Severidad | Runbook mínimo |
|---|---|---|---|
| cross_tenant, erasure_resurrected, legal_gate_bypassed | cualquier evento | SEV-1 | aislar tenant/scope, bloquear serving, preservar evidencia, seguridad/legal |
| control_plane_unavailable | > 15 min | SEV-1 | failover, validar fences/outbox, reconciliar runs |
| freshness_budget_burn | > 10× durante 1 h o > 2× durante 6 h | SEV-2 | drilldown por scope, budget/origin, backlog y ETA real |
| recovery_eta | supera RTO | SEV-2 | reducir input, reservar recovery pool, escalar capacidad |
| dlq_ratio | > 0,1% o 100 items/15 min por stage | SEV-2 | clasificar, detener redrive masivo, corregir causa |
| quarantine_ratio | > 1%/30 min o nueva reason_class | SEV-2 | congelar parser revision, comparar fixture, revisión humana |
| stale_fence_rejects | > 10/5 min | SEV-2 | comprobar clock/leases/rebalance; no ampliar TTL automáticamente |
| capacity_utilization | > 70% sostenido 30 min | SEV-2 | backpressure, shard split o rollback |
| anomalía de volumen | fuera del baseline aprobado | SEV-3 | descartar parcial/truncation antes de inferir mercado |

- **OPS-064 — Runbook completo.** Cada alerta DEBE enlazar owner, alcance, dashboard, consulta de alta cardinalidad, contención, replay seguro, criterio de escalado, verificación de recuperación y closure evidence.
- **OPS-065 — Sin PII en páginas.** Alertas y mensajes no incluyen URLs con tokens, credenciales, contactos, VIN completos ni payload.

## 9. Observabilidad con cardinalidad controlada

Prometheus advierte que cada combinación de labels crea otra serie y desaconseja dimensiones no acotadas [4].

- **OPS-066 — Labels permitidos.** Metrics solo usan conjuntos acotados como service, stage, region, country, vehicle_class, source_type, tier, result, status_class, error_class y shard_class.
- **OPS-067 — Labels prohibidos.** listing_id, dealer_id, source_id, surface_id, stream_id, partition_id, run_id, credential_id, raw URL, excepción libre y tenant_id no son labels de metrics.
- **OPS-068 — Drilldown.** Esos identificadores viven en logs estructurados, traces muestreados y tablas operativas con acceso auditado. La UI de dealer resuelve dealer como filtro de consulta; no crea una time series por dealer.
- **OPS-069 — Métricas mínimas.** Deben existir run_terminal_total, freshness_debt_seconds, event_lag_seconds, fetch_total, rate_budget_utilization_ratio, lease_fence_reject_total, dlq_total, quarantine_total, projection_apply_seconds, current_read_seconds, erasure_pending_age_seconds y metric_overflow_total.
- **OPS-070 — Correlación acotada.** trace_id conecta run, attempt, raw, normalization y projection sin incluir payload. El sistema conserva los primeros N errores por reason/scope/ventana y después contadores agregados con ejemplares muestreados; ni un ataque ni un fallo masivo crea un spool ilimitado. Auditoría de decisiones legales, acceso privilegiado y erasure usa un canal durable con cuota y fail-closed, no traces sin límite.

## 10. Disaster recovery y borrado que no resucita

### 10.1 Targets iniciales no probados

| Plano | RPO objetivo | RTO objetivo |
|---|---:|---:|
| deletion/revocation ledger ya confirmado | 0 para ACK reconocido | fail-closed hasta obtener watermark |
| public sequence high-water ya publicado | 0 reutilización | fail-closed hasta reservar epoch/rango superior |
| PostgreSQL control | 5 min | 60 min |
| log de eventos | 5 min | 2 h |
| raw/history, una partición afectada | 15 min o último commit durable | 8 h |
| serving con failover caliente | 15 min | 2 h |
| rebuild serving 10m / 100m / 1b | desde histórico | 24 h / 72 h / 168 h |
| search opcional | 24 h | 72 h |

- **OPS-071 — Particionar recuperación.** Restore, replay y validación DEBEN poder ejecutarse por país, Stream, tabla y projection shard. Una restauración global no es el único runbook.
- **OPS-072 — Backup.** PostgreSQL mantiene PITR 14 días y backups diarios 35 días por defecto. Object storage usa replicación o versionado según residencia. Cada trimestre se restaura, no solo se inspecciona que el backup exista.
- **OPS-073 — Recovery capacity.** El perfil debe sostener carga actual y vaciar 24 horas de backlog en 8 horas. La fórmula equivale a cuatro veces el QPS steady; si la fuente no admite catch-up, se declara otro RTO, no se viola el rate budget.
- **OPS-074 — Orden de restore.** Aislar serving; restaurar control; obtener del quorum independiente los watermarks más recientes de deletion/revocation y public sequence; restaurar manifests/history; aplicar erasures y tombstones; reservar restore_epoch y rangos seq superiores; replay idempotente; validar barriers/tenants; abrir lecturas. Si cualquier watermark no puede probarse, serving permanece cerrado.
- **OPS-075 — No resurrección.** deletion ledger se confirma en almacenamiento durable independiente del backup restaurado y se retiene 400 días, más que el máximo backup. Un ACK de supresión solo se devuelve tras confirmar su watermark y revocar online derivatives. Ninguna copia restaurada sirve datos antes de aplicar todas las entradas hasta ese watermark.
- **OPS-085 — Secuencia pública no reutilizable.** El allocator conserva por inventory el high-water confirmado en quorum independiente. Tras DR asigna restore_epoch monotónico y reserva un rango estrictamente superior; nunca reinicia seq desde backup, Kafka offset o contador local. Todo cursor del epoch anterior recibe 410 y resnapshot antes de cruzar una pérdida; un sequence_gap solo cierra una reserva demostrablemente sin hechos, nunca encubre el RPO. Si no puede probar el high-water, falla cerrado.
- **OPS-086 — RPO con deuda explícita.** Pérdida permitida por el RPO general crea loss_window con alcance y tiempos, invalida certificados/censos afectados y deja recovery_evidence_state incompleto hasta reconciliación. Refetch posterior tiene su observed_at real; nunca reconstruye ficticiamente una observación perdida.
- **OPS-087 — Erasure direccionable.** Un bundle compartido mantiene offsets y cifrado por registro o permite rewrite/compaction atómico sin el registro. Crypto-shred con una key compartida entre registros está prohibido. Erasure cubre bundles, todos los snapshots Iceberg vivos, delete files, manifests, proyecciones, search, caches, CDN y backups al expirar.

### 10.2 Failure drills y aceptación

Los drills se ejecutan antes de cada salto de perfil y al menos trimestralmente. Son requisitos de aceptación, no resultados existentes.

| Drill | Aceptación |
|---|---|
| worker cae tras PUT y antes de commit | cursor no avanza; retry converge; huérfano se elimina tras grace |
| lease expira durante fetch | epoch antiguo recibe fence reject y no completa Partition |
| dispatcher duplica outbox | un solo resultado lógico por idempotency_key |
| replay completo de log | current e histórico lógico no cambian salvo métricas de replay |
| conflicto Iceberg | no faltan observation_id; retry publica un snapshot válido; temporales quedan para GC |
| hot Stream/dealer | split con routing_epoch sin reordenar listing ni romper SLO |
| failover PostgreSQL | RPO/RTO cumplidos y sin lease/outbox doble aceptado |
| corrupción de projection shard | rebuild por shard dentro del RTO del perfil |
| 429/403 masivo | rate budget/circuit contienen origin; otros scopes siguen; SLO unmet visible |
| restore desde backup anterior a erasure | dato borrado no aparece en API, search, cache, raw ni histórico activo |
| intento SSRF/prompt injection | sin acceso interno, ejecución ni cambio canónico |
| consulta cruzada de tenant | cero filas y evento de seguridad |

- **OPS-076 — Evidencia de drill.** Cada ejecución conserva versión/topología, inyección, timestamps, resultado, métricas y remediaciones. Un drill fallido bloquea el perfil.

## 11. Seguridad, tenants, contenido hostil y agentes

- **OPS-077 — Tenant isolation.** tenant_id se deriva de autenticación, nunca del cuerpo de la petición. Toda consulta, cache key, export y job aplica entitlement. PostgreSQL usa roles/RLS donde corresponda; shards y object prefixes aplican políticas equivalentes. Feeds privados se separan de adquisición compartida.
- **OPS-078 — Secretos.** Credenciales son de mínimo privilegio, cortas cuando sea posible, rotadas y nunca visibles a IA, logs, traces, Kafka o artefactos raw. Acceso de emergencia queda auditado.
- **OPS-079 — SSRF y contenido no confiable.** Todo fetch pasa por egress proxy: solo protocolos autorizados, DNS resuelto y revalidado tras redirect, bloqueo de loopback/private/link-local/metadata, límites de bytes/tiempo/redirects/descompresión y sin callbacks arbitrarios. HTML, JSON, documentos, imágenes y texto son datos hostiles; parsers aislados no ejecutan scripts, macros ni instrucciones contenidas.
- **OPS-080 — IA sin autoridad silenciosa.** Un agente puede proponer clasificación, parser patch o identidad con provenance y confidence; no puede aprobar acceso legal, cambiar scope, desbloquear quarantine/DLQ, fusionar vehículos/dealers, alterar evidencia, ampliar retención ni publicar una autocorrección sin policy gate y actor auditado.

## 12. Privacidad, retención y gates legales

La visibilidad pública no concede por sí sola permiso contractual, derechos de reutilización ni base jurídica para tratar datos personales. En la UE, RGPD exige finalidad, minimización, limitación de conservación, seguridad y una base jurídica [5]; la Directiva de bases de datos contempla extracción/reutilización sustancial y extracciones repetidas sistemáticas [6]. En Suiza, la FADP vigente es el punto de partida legal [7], y el FDPIC recuerda específicamente que quienes hacen scraping siguen siendo responsables del tratamiento lícito de datos personales [8]. La evaluación exacta es por fuente, Surface, país, finalidad, campo y clase; este documento no emite aprobación legal general.

Antes de activar un scope deben estar en allowed: access_decision, contractual_decision, database_rights_decision, copyright/media_decision, privacy_basis, retention_policy y residency_policy. public_accessible no es un valor sustituto.

### Retención default, siempre reducible

| Tier de dato | Default | Regla |
|---|---:|---|
| buffers temporales de fetch | 24 h | cifrados y borrados al comprometer o fallar raw |
| raw HTML/API autorizado | 90 días | inmutable mientras se retiene; extensión requiere legal gate |
| media binaria autorizada | 30 días | preferir hash/refetch o licencia explícita |
| receipts/raw_manifest de todos los fetches | 90 días | una fila por intento, incluso 304, duplicado o media; campos minimizados |
| contacto directo y texto libre con datos personales | 30 días | excluir antes si no es necesario |
| current listing y estado de presencia | sin baja por TTL | persiste known/unknown/suspended hasta evidencia o withholding por derechos; expone edad |
| observaciones normalizadas | 24 meses | campos personales minimizados o tokenizados |
| Kafka | 7 días | no es archivo histórico |
| logs/traces operativos | 30 días hot, 180 días cold | redacción y acceso auditado |
| backups | 35 días | borrado lógico inmediato y físico al expirar |
| deletion ledger pseudonimizado | 400 días | evita resurrección de backups |

Raw es append-only para operación normal, no legalmente imborrable. Erasure crea tombstone, revoca serving/cache/search, elimina el registro mediante key individual o rewrite de bundle, aplica equality/position deletes en Iceberg, expira los snapshots que aún lo referencian y compacta dentro de la política. Se conserva solo evidencia mínima no personal del cumplimiento. Una key compartida no se destruye para borrar un solo registro. El historial es completo dentro de la retención declarada, no eterno.

Que expiren raw o atributos históricos no convierte un listing en retirado. Si los derechos ya no permiten servir detalles, current mantiene únicamente el estado mínimo autorizado con content_status=withheld y edades visibles; una transición de presencia necesita evidencia P2, nunca un temporizador.

Transferencia entre regiones, UE/EEE y Suiza requiere residency_policy aprobada; una réplica de DR no evita esa revisión. Legal hold solo existe con autoridad, alcance y fecha de expiración registrados.

## 13. Modelo de capacidad y aritmética

El contrato ejecutable es [capacity-model.json](../../contracts/capacity-model.json). Usa bytes decimales, expone todas las ecuaciones y separa perfiles activos del histórico de diez mil millones de observaciones.

### 13.1 Resultado mecánico de los supuestos, no benchmark

| Perfil | Fetch steady/design | Peak recovery | Ingress peak si coincide backfill media | Steady combinado / peak backfill |
|---|---:|---:|---:|---:|
| 10m activos | 115,74 / 231,48 QPS | 462,96 QPS | 49,72 MB/s | 30,25 / 47,94 TB |
| 100m activos | 1.157,41 / 2.314,81 QPS | 4.629,63 QPS | 497,22 MB/s | 302,55 / 479,38 TB |
| 1b activos | 11.574,07 / 23.148,15 QPS | 46.296,30 QPS | 4,97 GB/s | 3,03 / 4,79 PB |
| 10b observaciones históricas, aisladas | n/a | n/a | scan design 425,93 MB/s | 23,00 TB |

El combinado steady sí incluye la retención efectiva de 730 días: 7,3b, 73b y 730b observaciones para 10m, 100m y 1b activos con un evento diario. Por eso el perfil histórico aislado de 10b se alcanza en 1.000, 100 o 10 días respectivamente y no representa por sí solo una factura completa. En steady de 24 meses domina el histórico normalizado; durante el backfill inicial dominan los bytes binarios de medios: 52,6% del peak combinado bajo estos supuestos.

En 1b activos hay 100,8b filas de evidencia raw durante 90 días: 90b de fetches no-media y 10,8b de fetches recurrentes de imagen. Los 9b payloads no-media únicos asumidos se agrupan en 9m bundles, mientras todas las evidencias requieren 100,8m batches de manifest de 1.000 filas; deduplicar bytes nunca deduplica FetchAttempt/RawCapture. El backfill inicial suma 12b descargas de imagen, 12m batches y 4,31 TB de metadata, además de 2,52 PB binarios bajo 70% de contenido único y objetivo de 30 días. Por ello raw_manifest, observaciones y media no caben sensatamente en el control plane.

El fetch/listing/día representa reconciliación completa de 24 horas. La frescura delta es otra carga, proporcional a Streams y al tamaño de cada delta, no a listings. La sensibilidad por un millón de Streams es:

| Tier | Polls por Stream/día | QPS steady/design |
|---|---:|---:|
| T0, 15 min | 96 | 1.111,11 / 2.222,22 |
| T1, 6 h | 4 | 46,30 / 92,59 |
| T2, 24 h | 1 | 11,57 / 23,15 |

Esto no ordena leer todos los listings cada 15 minutos. Un delta poll puede devolver cero o miles de cambios; su payload medio de 5 KB es solo un supuesto que deberá medirse. Los demás números también dependen de hipótesis deliberadamente visibles: 45 KB por fetch, 250 B por fila de manifest, 10% de payload raw único, 12 imágenes de 240 KB, 1% de conjuntos media cambiados/día, 70% de media única, read demand y tamaños de proyección. Ninguna ha sido medida aún.

### 13.2 Gates antes de comprometer un perfil

1. **Permission gate:** la cadence cabe en los budgets autorizados por origin/source/credential/country con al menos 30% de reserva. Capacidad interna no autoriza tráfico.
2. **Measurement gate:** existen p50/p95/p99 reales de latencia, bytes, 304/duplicados, cambios, media y read/write mix por tipo de fuente.
3. **Sustained gate:** dos horas a design load —2× steady— con CPU, memoria y storage ≤70%, error ≤0,1% y SLO de latencia del contrato.
4. **Recovery gate:** provisionar y medir max(design, recovery). Con backlog 24 h que se vacía en 8 h, recovery es 4× steady y supera el headroom normal. El pool debe sostenerlo ocho horas mientras continúa carga nueva. Cuotas de fuente siguen mandando; si no permiten catch-up, se declara RTO/SLO incumplido o se rediseña cadence.
5. **Hot/repartition gate:** el peor hot dealer/Stream observado se reparte con routing_epoch sin pérdida, duplicado lógico ni reorder por ListingIdentity.
6. **History gate:** commit, compaction, deletes, schema/partition evolution y full scan Iceberg cumplen los límites del JSON con object counts reales.
7. **DR/erasure gate:** restore por partición y backup antiguo no resucitan datos; se cumplen RPO/RTO del perfil.
8. **Security gate:** aislamiento tenant, egress/SSRF, secrets y contenido hostil pasan pruebas.
9. **Spend gate:** solo después de medir requests, bytes, operaciones, egress y retención se aplican precios contractuales vigentes. estimated_monthly_cost permanece null hasta entonces.

Si un gate falla se reduce el perfil, cadence o retención de forma explícita, o se cambia tecnología y se repite. No se extrapola linealmente ni se oculta la deuda.

## 14. Trazabilidad a aceptación

Un caso puede comprobar varios contratos y aparecer en más de una fila. Esta tabla señala la parte operativa que corresponde a este documento; no convierte a OPS en owner de semántica de dominio, identidad o API definida en otros contratos.

| Requisitos OPS | Casos A/M relacionados |
|---|---|
| OPS-001..005 | A012, A013, A026, A030, A055, A056, A058, A068, A083, M023 |
| OPS-006..012 | A001, A003, A011, A020, A022, A026, A029, A030, A041, A063, A069, A070, A078, A086, M006, M007, M008, M018, M029 |
| OPS-013..016 | A022, A023, A024, A025, A045, A049, A050, A051, A052, A061 |
| OPS-017..023 | A018, A019, A020, A021, A024, A025, A043, A044, A047, A050, A051, A053, A060, A064, A071, A072, A074, A079, A082, A085 |
| OPS-024..027 | A014, A036, A042, A050, A052, A053, A061, A064, M021, M022, M023 |
| OPS-028..032 | A003, A004, A005, A006, A007, A008, A009, A010, A014, A015, A016, A017, A020, A021, A022, A025, A033, A034, A035, A041, A043, A044, A059, A060, A063, A066, A067, A069, A070, A071, A072, A073, M008, M010, M011, M012, M013, M014, M020, M022, M025, M026 |
| OPS-033..038 | A019, A020, A021, A022, A023, A024, A025, A027, A028, A029, A030, A031, A032, A033, A036, A037, A041, A044, A061, A063, A064, A065, A075, A076, A079, A085, M001, M002, M003, M004, M005, M006, M007, M008, M009, M010, M011, M012, M013, M014, M015, M016, M017, M018, M019, M020, M021, M025, M027, M028 |
| OPS-081..084 | A036, A037, A038, A039, A040, A042, A061, A064, A075, A077, M021, M024, M027 |
| OPS-039..049 | A023, A031, A032, A043..046, A051, A052, A056, A062, M005, M009, M011, M016, M023, M024 |
| OPS-050..056 | A001, A002, A011, A014, A029, A035, A042, A061, A064, A076, A078, M015, M022, M028 |
| OPS-057..063 | A027, A028, A031, A032, A045, A050, A052, A055, A056, A057, A063, A065, A080, A083, M001, M002, M003, M004, M005, M006, M007, M008, M009, M010, M011, M012, M013, M014, M015, M016, M017, M018, M019, M020, M023, M025 |
| OPS-064..070 | A018, A043, A044, A046, A054, A060, A072, A084 |
| OPS-071..076 y OPS-085..087 | A021, A024, A025, A048, A049, A050, A052, A061, A064, A074, A079, A081, A082 |
| OPS-077..080 | A018, A039, A040, A045, A046, A047, A048, A049, A059, A060, A062, A067, A071, A072, A074, A077, M024 |

La presencia de un caso en la tabla solo identifica su efecto operativo secundario; sus oráculos de ontología o API siguen perteneciendo a P1/P2.

### Cierre de huecos operativos del catálogo

Los ocho caminos de fallo que no tenían caso aislado quedan cubiertos por casos de implementación, todavía no ejecutados: commit Iceberg/publicación por A079; erasure selectivo de bundle por A082; high-water y supresiones tras DR por A081; tres relojes por A080; inventario extremo asíncrono por A077; carga combinada/recovery por A083; tormenta de telemetría por A084; y PUT terminado tras revocación o fence vencido por A085. Que exista el caso no acredita la implementación: cada perfil sigue bloqueado hasta ejecutarlo con la evidencia de OPS-076.

## 15. Decisiones pendientes de evidencia

- Distribución real de listings activos, churn y payload por país, clase y tipo de fuente.
- Presupuestos autorizados y límites efectivos por origin, API, feed y credential.
- Frecuencia necesaria por tier para cumplir producto y contratos.
- Ratio 304 o hash duplicado y coste de verificar presencia.
- Tamaño, cambio, deduplicación, copyright y retención de media.
- Hot-key distribution y patrón real de consultas API.
- Store ganador de la proyección en 100m y 1b.
- Topología o región y mecanismos legales de transferencia y DR.
- Coste por perfil con precios y descuentos contractuales.

Hasta resolverlos, esta arquitectura es una preparación con invariantes comprobables, no una afirmación de producción.

[1]: https://www.postgresql.org/docs/current/sql-select.html
[2]: https://kafka.apache.org/41/design/design/
[3]: https://iceberg.apache.org/spec/
[4]: https://prometheus.io/docs/practices/naming/
[5]: https://eur-lex.europa.eu/eli/reg/2016/679/oj
[6]: https://eur-lex.europa.eu/eli/dir/1996/9/oj
[7]: https://www.fedlex.admin.ch/eli/cc/2022/491/en
[8]: https://www.edoeb.admin.ch/dam/en/sd-web/TvfoxgaTWHUl/Data%20Scraping%20-Joint%20Statement%20-%20August%2024%202023.pdf
[9]: https://www.rfc-editor.org/rfc/rfc8785.html
