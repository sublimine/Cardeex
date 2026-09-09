# Verificación y orden de construcción

Estado: base de diseño por encargo de Elias, 2026-09-07. Este archivo es el plan canónico de entregas, pruebas y promoción; se actualiza en lugar de crear planes por sesión.

Para quienes construyan: trabajar por componente con autor y revisión independiente, usando `subagent-driven-development` o `executing-plans` cuando proceda. El encargo posterior autoriza el sistema local de descubrimiento de §8, ahora implementado. Las rutas de producto P0–P4 fuera de ese módulo siguen siendo destinos futuros, no código ya probado.

Objetivo: permitir construir un inventario vivo por dealer y POS que conserva evidencia, corrige identidad y escala por límites medidos sin perder trazabilidad.

Arquitectura: los planos de control, evidencia/procesamiento y serving se comunican por contratos versionados. Cada escritor tiene autoridad limitada, una transacción local, publicación recuperable y efectos idempotentes. La API lee proyecciones con corte definido; el raw admisible permite reprocesamiento.

Tecnología de referencia y evolución: [P3, ejecución y operaciones](03-runtime-and-operations.md). Ontología y formatos internos: [P1](01-domain-and-evidence.md). Semántica externa: [P2](02-inventory-and-api.md). Alcance y entradas de los proyectos siguientes: [fundamentos](../CARDEEX_FOUNDATION.md).

## 1. Qué significa «terminado» en esta fase

La base queda preparada cuando las responsabilidades tienen dueño, los contratos concuerdan, los ejemplos pasan validación, los riesgos tienen comportamiento definido y las pruebas futuras tienen oráculos de aceptación. No se declara que un servicio soporte una cifra de carga hasta ejecutarlo con una mezcla representativa; tampoco se declara que una fuente está autorizada, cubierta o fresca sin evidencia de esa fuente.

| Evidencia | Qué demuestra | Qué no demuestra |
|---|---|---|
| JSON Schema y ejemplos válidos/negativos | Tipos, requisitos y variantes comprobables del contrato | Veracidad de la fuente, integridad transaccional de una futura base |
| Contrato OpenAPI validado | Estructura de API, referencias y formas de respuesta | Autorización efectiva, latencia o disponibilidad |
| Modelo ejecutable pequeño | Coherencia de reglas bajo los escenarios modelados | Corrección del runtime distribuido que todavía no existe |
| Cálculo reproducible de capacidad | Coherencia aritmética bajo supuestos explícitos | Benchmark ni factura de proveedor |
| Revisión cruzada | Contraejemplos examinados y defectos corregidos | Ausencia universal de errores |
| Prueba de integración futura | Propiedad probada en una implementación y entorno concretos | Rendimiento en cualquier escala o distribución |

La garantía de organización se materializa en IDs, procedencia, contratos, validación y evolución. Una incógnita empírica no se rellena con una cifra inventada: tiene dueño, experimento, límite de promoción y fallback.

## 2. Anatomía de una entrega

Cada tarea produce cambios revisables con: requisito/invariante; entradas y contrato de salida; archivos y migraciones; precondiciones; presupuesto; prueba positiva y negativa; métricas; comportamiento al fallar; rollback; revisión independiente. El autor no marca una puerta como superada porque creó el archivo de prueba: debe adjuntar resultado, configuración, versiones y corpus de la ejecución.

La prueba de contrato se escribe antes del componente. El corpus inicial es sintético y cubre adversarios; las capturas reales se incorporan únicamente tras admisión y saneamiento. Un fixture lleva origen permitido, checksum, versión de esquema, caso que reproduce y expectativa. No se añade una prueba por cada línea de código: se prueban invariantes, límites y fallos.

Los resultados operativos viven en el sistema de evidencias de pruebas y CI cuando exista. El repositorio conserva contratos y casos duraderos; no se acumulan informes de sesión ni capturas de terminal. Las excepciones vigentes se reflejan en el componente afectado con vencimiento y responsable.

## 3. Puertas de avance

| Puerta | Entrada y condición de salida | Acción si falla | Estado de esta entrega |
|---|---|---|---|
| G0. Base coherente | Esquemas, referencias, ejemplos, aritmética y revisión cruzada sin defecto crítico abierto | Corregir contratos y repetir solo comprobaciones afectadas | Superada en el alcance estático/modelado el 2026-09-07; comando reproducible en §7, sin certificación de producto |
| G1. Descubrimiento diseñado | Métodos finitos por celda, procedencia, resolución de candidatos y denominadores honestos | Mantener candidatos sin convertirlos en entidades certificadas | Estrategia y sistema local implementados/probados; campañas, calibración y cobertura reales pendientes |
| G2. Adquisición diseñada | Capacidad por superficie, permisos, enumeración, replay, límites y reconciliación defendibles | Mantener superficie bloqueada o solo positivos según capacidad | Proyecto posterior a G1 |
| G3. Núcleo implementado | Pruebas unitarias/modelo/integración de contratos, privacidad y recuperación | No conectar fuentes reales | Construcción posterior |
| G4. Fuente certificada | Piloto admitido, corpus real permitido, censos comparables, deriva y alarmas verificadas | Cuarentena por fuente/stream; serving marcado degradado | Requiere G2 y G3 |
| G5. Servicio operativo | SLO observados, on-call efectivo, restauración y autorización multi-cliente verificadas | Servir solo bajo nivel de servicio realmente demostrado | Requiere despliegue y medición |
| G6. Escala promovida | Carga representativa, sesgo extremo, recuperación y coste dentro del perfil aprobado | Mantener nivel anterior y limitar admisiones/cadencia | Se repite por escalón, no una vez para siempre |

G1 y G2 cierran estrategias; no prueban stock real. G3 puede desarrollarse con sintéticos una vez autorizado sin esperar que todos los países tengan fuentes. G4 se concede por superficie/alcance, G5 por servicio y G6 por perfil de carga. Una puerta no concede automáticamente otra.

## 4. Proyectos y paquetes de trabajo

### P0. Constitución, control y conocimiento

#### P0.1 Contratos y vocabulario — entregado en esta base

- [x] Conservar misión, ampliar captura inicial a las cuatro clases admitidas y separar certificación.
- [x] Definir relaciones temporales, ámbitos de autoridad y denominadores.
- [x] Fijar entradas de descubrimiento y manifiesto de capacidades sin escoger recetas todavía.
- [x] Mantener documentos canónicos y contratos estructurados con verificador.

Archivos actuales: `docs/CARDEEX_FOUNDATION.md`, `contracts/control.schema.json`, `contracts/domain.schema.json`, `contracts/openapi.yaml`, los cuatro documentos de arquitectura y los casos/verificador de esta base. Criterio: ningún miembro del equipo necesita un checkpoint separado para recuperar decisiones o el siguiente paso.

#### P0.2 Registro y admisión — construcción futura

Archivos futuros: `src/control/registry/`, `src/control/admission/`, `migrations/control/`, `tests/control/`.

1. Crear pruebas de registro de fuente/superficie/stream usando ejemplos del contrato; incluir namespace compartido y namespaces incompatibles.
2. Implementar transiciones con revisión esperada, actor, evidencia y fechas, rechazando actualización concurrente obsoleta.
3. Implementar restricciones de país/clase/superficie y revocación en admisión; comprobar también vencimiento antes de cada acceso y publicación.
4. Probar candidato ambiguo, duplicado con evidencia, admisión caducada, recorte de alcance y cambio de titularidad del dominio.
5. Probar outbox y recuperación de cambio de registro conforme a P3; una repetición no duplica entidad ni evento.

Salida: registro transaccional consultable por los planificadores, con motivos legibles de bloqueo. Fallback: rechazar nuevo trabajo cuando no se puede verificar permiso; no fabricar inventario vacío. Rollback: versión compatible y reproducción desde historial de decisiones, preservando revocaciones posteriores.

### P1. Dominio, evidencia e identidad

#### P1.1 Persistencia de capturas

Archivos futuros: `src/evidence/capture/`, `src/evidence/manifests/`, `src/evidence/retention/`, `tests/evidence/`.

1. Probar captura de bytes, checksum completo y manifiesto con referencias de fuente/run; incluir truncamiento, tipo incorrecto y respuesta de error.
2. Implementar escritura durable y protocolo de publicación P3; matar el proceso antes/después de cada frontera durable y comprobar huérfanos recuperables.
3. Probar 304: conserva la referencia al contenido anterior y añade evidencia de presencia, sin fingir nueva captura de atributos.
4. Probar permiso revocado durante fetch; no publicar el resultado ni conservarlo fuera de la política vigente.
5. Probar vencimiento y borrado admisible en todos los derivados, incluida restauración; mantener solo el recibo permitido de supresión.

Salida: artefactos verificables o fallo explícito; ningún evento dice «capturado» antes de evidencia durable. Rollback: parar publicación, reparar manifiestos y reemitir idempotentemente; nunca recuperar por red el contenido histórico ausente y llamarlo replay.

#### P1.2 Observaciones, atributos y normalización

Archivos futuros: `src/domain/observations/`, `src/domain/normalization/`, `src/domain/taxonomy/`, `tests/domain/fixtures/`, `tests/domain/test_observations.*`.

1. Aplicar `domain.schema.json` con comprobación de formatos y variantes; conservar desconocidos como desconocidos o cuarentena.
2. Probar por separado presencia y snapshot de atributos; un parser que omite precio no borra el precio anterior ni lo presenta como actualizado.
3. Probar valores cero, null explícito, ausencia, monedas, importes netos/brutos, cuotas financieras, unidades y precisión de fechas.
4. Probar las cuatro clases, distinción de condición/canal/estado, y categorías excluidas o no resueltas.
5. Probar eventos tardíos, relojes erróneos y reextracción con nueva versión sin falsificar tiempo observado.

Salida: afirmaciones con procedencia que P2 puede proyectar. No se certifica exactitud del parser solo por pasar el esquema. Rollback: fijar versión anterior y reconstruir una proyección sombra; conservar las dos derivaciones y sus límites.

#### P1.3 Resolución de entidades y vehículos

Archivos futuros: `src/identity/candidates/`, `src/identity/decisions/`, `src/identity/relationships/`, `tests/identity/`.

1. Crear corpus estratificado con pares positivos, negativos difíciles y casos ambiguos; separar país, clase, fuente y familia de publicación para evitar fuga de entrenamiento/evaluación.
2. Probar VIN repetido, inválido o ausente; coche de flota idéntico; foto de catálogo; anuncio republicado; ID externo reutilizado y venta posterior por otro dealer.
3. Implementar generación de candidatos con límites de bloque y fallback para bloques enormes; medir recall de candidatos antes del clasificador.
4. Aplicar reglas de P1 y registrar evidencia/versión/conflictos; un score sin calibración no autoriza autofusión.
5. Probar merge, evidencia contradictoria, split y corrección de referencias API sin perder observaciones ni hacer ciclos de alias.
6. Probar asignación vendedor/POS con sucursales, intermediación, traslado, vendedor desconocido y cuentas compartidas; no copiar el inventario del grupo en cada sucursal.

Salida: decisiones reversibles y candidatos no resueltos explícitos. Rollback: revocar versión de política y reproducir decisiones permitidas, emitiendo correcciones a las proyecciones afectadas. Una revisión manual se audita igual que la automática.

### P2. Inventario y API

#### P2.1 Reconciliación de presencia

Archivos futuros: `src/inventory/reconciliation/`, `src/inventory/lifecycle/`, `src/inventory/membership/`, `tests/inventory/`.

1. Traducir los escenarios de presencia del catálogo a pruebas de implementación con las reglas exactas de P2.
2. Implementar unión de particiones, validez del alcance y barrera de completitud; incluir filtros de precio que cambian durante el censo.
3. Reproducir barridos parciales, límites silenciosos, páginas duplicadas, cambios de clase y errores tras la última página.
4. Probar señales positivas tardías, retiradas explícitas, relist e ID reincarnado; nunca deducir venta del vehículo desde desaparición de un anuncio.
5. Probar aislamiento entre fuentes/clases y corrección de asignaciones entre dealers/POS.

Salida: transiciones justificadas por evidencia, con frescura y salud independientes. Fallback: conservar último estado conocido marcado según P2, no ejecutar bajas destructivas. Rollback: reconstruir generación sombra y publicar compensaciones con versión.

#### P2.2 Proyección y publicación atómica

Archivos futuros: `src/serving/projections/`, `src/serving/snapshots/`, `src/serving/events/`, `tests/serving/`.

1. Construir snapshot fijo y watermark de eventos en la misma frontera de publicación.
2. Inyectar cambios entre cada página, escritura concurrente de dos particiones y caída durante conmutación de generación.
3. Probar reintentos de un evento, secuencia fuera de orden, reequilibrio de shard y antiguo escritor con lease vencido.
4. Probar borrado/rectificación durante snapshot y revocación de permisos durante paginación con la semántica de P2.
5. Probar dealer enorme, POS ambiguo y el mismo vehículo ofertado en varios canales; contadores deben indicar unidad, corte y grado de resolución.

Salida: snapshot consistente, secuencia local y continuación recuperable; proyección incompleta no se anuncia como completa. Rollback: volver a generación servible compatible con derechos actuales, sin retroceder la secuencia pública ni resucitar supresiones.

#### P2.3 API y consumidores

Archivos futuros: `src/api/`, `src/delivery/`, `tests/api/contract/`, `tests/api/security/`, `tests/delivery/`.

1. Generar validación de request/response desde `contracts/openapi.yaml`; probar formas de error y campos obligatorios.
2. Probar filtros y cursores ligados a tenant, permisos y snapshot; IDs impredecibles no sustituyen autorización.
3. Verificar handshake snapshot→eventos, replay, expiración y reconstrucción completa del consumidor desde cero.
4. Probar timeouts, destinos de webhook fallidos, reintentos, duplicados y ausencia de orden de entrega; el consumidor converge con secuencia/cursor.
5. Probar acceso a historia y medios con derechos distintos de los de inventario; un enlace cacheado no evita revocación.

Salida: cliente de referencia que reconstruye el inventario exactamente para un corte, se recupera de una desconexión y explica cada elemento. Rollback: versión compatible y cambios aditivos; los cambios incompatibles abren versión mayor.

### P3. Ejecución, capacidad y operación

#### P3.1 Scheduler y trabajadores

Archivos futuros: `src/runtime/scheduler/`, `src/runtime/workers/`, `src/runtime/limits/`, `src/runtime/outbox/`, `tests/runtime/`.

1. Implementar trabajo durable con dedup de tareas, lease y fencing conforme a P3.
2. Probar 429, timeouts, errores permanentes y cola saturada con budgets compartidos por origen/credencial; la clase moto no obtiene un segundo presupuesto del mismo host.
3. Probar backpressure de almacenamiento y serving: frenar nuevas adquisiciones antes de perder evidencia durable.
4. Probar fuente dominante y colas de larga cola; verificar fairness y que ninguna fuente sana quede oculta por promedios.
5. Probar pausa/reanudación, cancelación, credencial rotada y replay con red deshabilitada.

Salida: trabajos recuperables y gobernados, aislamiento observable y gasto acotado. Fallback: circuito abierto y deuda de frescura visible; no aumentar carga sobre una fuente caída.

#### P3.2 Recuperación, seguridad y derechos

Archivos futuros: `src/policy/`, `src/security/`, `ops/runbooks/`, `tests/recovery/`, `tests/security/`.

1. Crear runbooks ejecutables con roles para caída de fuente, corrupción de proyección, deriva de parser, incidente de identidad y supresión.
2. Inyectar pérdida de proceso, disco, nodo, zona, broker y región; medir RPO/RTO por clase de dato.
3. Restaurar un backup previo a una revocación/supresión y demostrar que no se sirve contenido ya prohibido.
4. Probar SSRF, redirección a red privada, DNS cambiante, archivo expansivo, markup hostil, secretos en URL/log y prompt injection en descripción.
5. Probar privilegio mínimo, auditoría sin PII, rotación de secretos y ausencia de acceso cruzado entre clientes.

Salida: runbooks probados y evidencias de recuperación, no solo procedimientos escritos. Fallback: mantener serving seguro o cerrado según riesgo, con estado degradado y un responsable activo.

#### P3.3 Capacidad y economía

Archivos futuros: `benchmarks/workloads/`, `benchmarks/recovery/`, `ops/capacity/`, `tests/load/`.

1. Generar datasets sintéticos reproducibles para los perfiles de `capacity-model.json`; separar activos, comprobaciones, cambios, historia y medios.
2. Variar distribución de dealers, fuentes calientes, longitud de textos, clases, duplicados, cambios de precio y tamaño de medios; no probar solo distribución uniforme.
3. Medir throughput sostenible con redundancia, p95/p99, backlog, amplificación de escritura, tamaño de índices y costo unitario por operación.
4. Inyectar interrupción y drenar backlog mientras continúa carga normal; respetar también cuotas de origen.
5. Ensayar resharding, reindexación y borrado a la vez que se sirve tráfico, manteniendo contratos y supresiones.

Salida: perfil máximo certificado con margen, costo y límites de admisión/cadencia. Una capacidad no demostrada se mantiene como objetivo; no se compra infraestructura ilimitada por anticipado.

### P4. Calidad y promoción continua

#### P4.1 Certificación de datos y cobertura

Archivos futuros: `src/quality/`, `src/coverage/`, `tests/quality/`, `tests/coverage/`.

1. Implementar registro de denominador/celda/versión y separar descubierto, admitido, accesible, monitorizado y certificado.
2. Probar universo desconocido, cero real y fuente retirada; los motivos de exclusión siguen visibles.
3. Medir completitud de enumeración aparte de extracción, identidad, asignación POS y frescura por campo.
4. Probar cambios de taxonomy y scope: invalidar certificados incompatibles sin mezclar series históricas.
5. Construir reporte reproducible cuyo porcentaje tenga numerador, denominador, fecha, unidad, evidencia y límites.

Salida: ningún mensaje «100%» puede generarse sin condiciones comprobadas. Fallback: mostrar intervalo/unknown y la tarea de exploración pendiente.

#### P4.2 Promoción y cambio de versiones

Archivos futuros: `ops/release/`, `tests/migrations/`, `tests/compatibility/`, configuración CI del proveedor escogido.

1. Ejecutar matriz de contrato, integración, privacidad, recuperación y carga que corresponda al cambio.
2. Comparar versiones antigua/nueva sobre el mismo corpus retenido: diffs por campo y categoría, falsos merges, retiradas y costo.
3. Publicar canary por subconjunto explícito de streams y presupuesto; bloquear promoción ante pérdida de presencia, precisión o derechos.
4. Ensayar consumidores N/N-1, migración expand/contract y rollback con políticas vigentes.
5. Registrar evidencia de promoción en release/CI y actualizar contratos existentes; no crear documentación paralela de estado.

Salida: versión atribuible y reversible dentro de sus límites declarados. Una migración destructiva necesita procedimiento de restauración o decisión explícita sobre irreversibilidad.

## 5. Estrategia de pruebas adversariales

El catálogo [acceptance-cases.json](../../contracts/acceptance-cases.json) tiene IDs estables, invariantes enlazadas, estímulo, oráculo y etapa de ejecución. Los casos de arquitectura son obligaciones para la implementación. Los casos modelados son experimentos pequeños ejecutados por [verify_foundation.py](../../tools/verify_foundation.py); su alcance está limitado al modelo.

Familias obligatorias: identidad y clases; evidencia y tiempo; admisión y seguridad; enumeración/reconciliación; publicación y clientes; supresión/recuperación; capacidad; cobertura. La suite de producto importará esos IDs. Una prueba de API que omite las fuentes caídas o una carga sin dealers calientes no representa el sistema objetivo.

Las pruebas de concurrencia usarán interleavings controlados y fallos tras cada escritura durable. Las pruebas de orden temporal permutarán llegadas conservando órdenes de origen conocidos; cuando no hay orden verificable, el resultado correcto es conflicto o incertidumbre. Los merges se prueban en cadenas y triángulos con restricciones negativas, no solo en pares aislados.

Un test falla también si el sistema se queda bloqueado sin deadline, si consume presupuesto sin límite o si el diagnóstico no identifica el alcance causante. Se mide seguridad de resultado y capacidad de recuperación; disponibilidad no justifica publicar evidencia falsa.

## 6. Incógnitas empíricas con cierre definido

| Incógnita | Responsable lógico | Experimento y puerta | Comportamiento hasta resolverla |
|---|---|---|---|
| Universo real de profesionales/POS por celda | Descubrimiento y cobertura | Triangulación, revisión de candidatos y muestra independiente; G1/G4 | Denominador abierto; no certificado universal |
| Acceso y derechos por superficie/país | Admisión/política | Evaluación documentada, permisos y finalidad; G2/G4 | No ejecutar adquisición no admitida |
| Estabilidad de IDs y cursores | Integración de fuente | Fixtures, revisitas autorizadas y análisis de límites; G2/G4 | Namespace separado y ausencia no inferible |
| Cadencia viable para cada fuente | Operación | Cuotas, tiempos, volumen y demanda; G4/G5 | Objetivo visible como incumplido o no habilitado |
| Umbrales de identidad por clase | Identidad/calidad | Corpus etiquetado independiente y calibración; G3/G4 | Resolución conservadora y candidatos ambiguos |
| Rendimiento y costo físico | Runtime/capacidad | Mezcla de carga, fallos y recuperación; G6 | Perfil de referencia, sin promesa de escala efectiva |
| Retención exacta por dato/licencia | Política/evidencia | Matriz de finalidad y derechos; G2/G4 | Política restrictiva y no conservar lo no autorizado |
| Guardia real 24/7 | Operación | Personal/automatización responsable y simulacro; G5 | Servicio no etiquetado operativo 24/7 |

Estos cierres no son contradicciones pendientes del diseño: son requisitos explícitos para activar capacidades con evidencia real. Si un experimento refuta una decisión arquitectónica, se modifica el contrato y su migración antes de ampliar.

## 7. Comprobación reproducible de esta base

Desde la raíz del repositorio:

```powershell
.\.venv-verify\Scripts\python.exe tools/verify_foundation.py
git diff --check
```

El verificador usa Python 3.11 o superior, `jsonschema[format]`, `referencing`, `PyYAML` y `openapi-spec-validator`; las versiones fijadas están en `tools/requirements-verify.txt` y la preparación del entorno está en el README. Comprueba estructura y ejemplos de contratos, referencias internas, cobertura del catálogo, coherencia aritmética y escenarios del modelo. Devuelve código distinto de cero ante fallo; no realiza red, scraping ni writes en los datos del proyecto.

La validación debe fallar si faltan validadores de formatos. `jsonschema` trata formatos como anotaciones salvo activación explícita y algunas comprobaciones requieren dependencias adicionales; por eso una prueba negativa de fecha/URI forma parte del verificador. [Documentación oficial de jsonschema](https://python-jsonschema.readthedocs.io/en/stable/validate/).

La revisión independiente examina: invariantes cruzadas; clocks y finalización de barridos; alcance y aislamiento; identidades ambiguas; retención/backup; y compatibilidad snapshot/eventos. Las correcciones quedan en los contratos afectados. La cantidad de tests o páginas no sustituye la calidad del oráculo.

Las tablas de trazabilidad de P1/P2/P3 son el único mapa de sus invariantes hacia el catálogo: el verificador rechaza reglas sin mapa o casos inexistentes. Esa vinculación registra una obligación de aceptación, no una prueba de producto superada. La [comprobación CI](../../.github/workflows/verify-foundation.yml) ejecuta el mismo verificador sin acceso a fuentes de inventario; instalación de dependencias y checkout preceden a su aislamiento de red. El workflow no configura protección de rama ni demuestra por sí solo que GitHub lo haya ejecutado.

## 8. Construcción autorizada: descubrimiento

El encargo posterior de Elias autoriza construir el sistema de descubrimiento, con estrategias específicas para ES/FR/DE/NL/BE/CH, extensibilidad nacional y coste externo cero. No autoriza compras, cuentas, elusión de controles, campañas sin límites ni afirmar cobertura empírica inexistente. El diseño global y la personalización nacional fueron aceptados en conversación; las decisiones de implementación se delegaron al agente.

Objetivo: software ejecutable de planificación, recepción/descubrimiento de candidatos, procedencia, cola recuperable, revisión, exportación contractual y auditoría de cobertura. Arquitectura: paquetes nacionales declarativos sobre un motor único; adaptadores de evidencia independientes; SQLite local para el área de trabajo de descubrimiento, no sustituto del PostgreSQL de control productivo de OPS-013. Python 3.11+, biblioteca estándar y validadores ya fijados; ninguna dependencia SaaS ni llamada LLM de pago.

Plan de implementación (TDD y revisión independiente; este apartado sustituye cualquier plan de sesión):

- [x] D1. `discovery/profiles/*.json`, `discovery/profiles.py`, `tests/discovery/test_profiles.py`: fuentes primarias, perfiles nacionales propios, validación acotada/segura y extensión probadas con revisión independiente.
- [x] D2. `discovery/model.py`, `discovery/store.py`, `discovery/queue.py`, `tests/discovery/test_store.py`: transacciones/savepoints, evidencia, decisiones reversibles, contradicciones, retención, fencing, presupuestos y turnos persistentes probados.
- [x] D3. `discovery/planner.py`, `discovery/coverage.py`, `tests/discovery/test_planner.py`, `tests/discovery/test_coverage.py`: frontera versionada y reanudación por conteos sin reconstruir prefijos; geografía explícita, universos desconocidos y deuda por celda probados.
- [x] D4. `discovery/transport.py`, `discovery/adapters.py`, `tests/discovery/test_transport.py`, `tests/discovery/test_adapters.py`: normalización/SPA, DNS/TLS/SSRF, robots, límites, deadlines, acceso opt-in y parsers puros adversariales probados.
- [x] D5. `discovery/cli.py`, `discovery/__main__.py`, `discovery/engine.py`, `discovery/handoff.py`, `tests/discovery/test_cli.py`, `tests/discovery/test_engine.py`, `tests/discovery/test_runtime_guards.py`: flujo completo local, revisión, exportación contractual, replay, cooldown tras reinicio y revocación/caducidad durante fetch probados.
- [x] D6. `docs/architecture/05-discovery-system.md`, README, fundamentos, Home y CI: fuentes/decisiones/runbook integrados, pruebas adversariales y auditoría independiente cerradas; canvas del usuario preservado; verificación de toda la base y pruebas de descubrimiento exigida antes de integrar.

Comando de cada ciclo: `python -m unittest discover -s tests/discovery -p 'test_*.py' -v`. Antes de implementar cada comportamiento, ejecutar su prueba y comprobar fallo por capacidad ausente; después comprobar verde y regresiones. Integración: `python tools/verify_foundation.py`, suite completa y `git diff --check`. Cierre exige inspección de artefactos, no solo el informe del autor. La campaña paneuropea y el scraping de inventarios se distinguen del software entregado; las barreras de acceso quedan explícitas.

## 9. Próximo trabajo exacto

El sistema de [descubrimiento](05-discovery-system.md) permite preparar y ejecutar pilotos locales gobernados. Próximo paso operativo: catálogos territoriales verificados, admisión de canales gratuitos, campañas acotadas y auditoría independiente de huecos. Próximo proyecto de estrategia: adquisición/scraping con el apartado 15 de los fundamentos. Después se promueven componentes y superficies por G3–G6, sin confundir trabajo planificado con inventarios ya obtenidos.

Se actualizan esos contratos y este plan conforme se obtenga evidencia. No se añade una nueva base, un segundo mapa de decisiones ni una pila de checkpoints.

## 10. Reapertura: completar las capacidades de descubrimiento

Estado: **capacidades locales R1–R7 verificadas**, con revisión independiente de cumplimiento y calidad cerrada. La auditoría comparativa de código de Cardex/Cardeep detectó capacidades locales ausentes y dos defectos reproducibles en `6cd2f2d`; aquel cierre D1–D6 no acreditaba estas capacidades. Ningún resultado histórico, implementación, dato ni receta se importa: las referencias históricas justifican preguntas; las decisiones y el código nuevos son propios de Cardeex. Este cierre no certifica campañas, datos nacionales, acceso a todas las estrategias ni cobertura del universo abierto.

**Objetivo:** cerrar los defectos de conservación/frontera y entregar canales ejecutables, geografía resoluble, clasificación técnica, revisitas y estimación explícita de desconocidos, integrados con evidencia, CLI, cola, permisos y recuperación.

**Arquitectura:** parsers y análisis puros producen afirmaciones; el worker conserva autoridad exclusiva sobre red gobernada y confirmación transaccional. Los canales se vinculan explícitamente a estrategias y evaluaciones vigentes. Geografía y estimaciones conservan sus entradas/versiones y no atribuyen hechos desde una consulta. Las revisitas producen nuevas ejecuciones sin reiniciar presupuestos ni borrar resultados previos.

**Tecnología:** Python 3.11+, SQLite local y dependencias de verificación existentes. Sin servicio de pago obligatorio. La ejecución real de cada fuente conserva sus puertas de admisión; la prueba de un parser o cliente no certifica disponibilidad externa ni cobertura nacional.

### Responsabilidades y condiciones de cierre

| ID / propietario | Archivos exclusivos | Resultado exigido y evidencia de aceptación |
|---|---|---|
| R1 / integración | `discovery/adapters.py`, `discovery/engine.py`, pruebas existentes afectadas | POI OSM sin web conserva identidad de registro, nombre y ubicación; ningún sitemap, JSON o enlace expansible puede introducir detalles/medios reconocidos en la cola. Casos negativos reproducidos antes del cambio y positivos después. |
| R2 / canales | `discovery/channels.py`, `tests/discovery/test_channels.py` | Contratos ejecutables de petición y parseo para búsqueda admitida, OSM, directorios estructurados y registros abiertos seleccionados con documentación primaria. Paginación finita, marcadores de truncamiento, IDs de registro sin web, ausencia de permisos y formatos cambiados probados. No hay conectores nominales que devuelvan vacío por defecto. |
| R3 / geografía y medición | `discovery/geography.py`, `discovery/estimation.py`, `tests/discovery/test_geography.py`, `tests/discovery/test_estimation.py` | Catálogo versionado, códigos con ceros iniciales, alias/localidades menores, ambigüedades y evidencia de origen; resolución no inventada. Estimación reproducible por estrato con grupos independientes declarados, intervalos y rechazo de datos insuficientes; ninguna estimación se publica como certificación. |
| R4 / tecnología | `discovery/technology.py`, `tests/discovery/test_technology.py` | Inspección pura de HTML/cabeceras y URLs para CMS, proveedor de inventario y superficies posibles; señales/versiones, contradicciones, desconocido y páginas dinámicas explícitos. Nada se ejecuta ni se promueve a receta certificada por una firma. |
| R5 / integración | `discovery/scheduling.py`, `tests/discovery/test_scheduling.py`, `discovery/store.py`, `discovery/queue.py` | Calendario persistente por estrategia/alcance, vencimiento, pausa, reanudación, idempotencia concurrente y revisitas con historia; fallos y presupuestos no se resetean. `tick` acotado y modo de inspección sin mutaciones. |
| R6 / integración | `discovery/cli.py`, `discovery/planner.py`, `discovery/coverage.py`, `discovery/channel_runtime.py`, pruebas CLI/engine/coverage | Todo módulo tiene recorrido utilizable por operador. Canales admitidos resuelven consultas y semillas a peticiones reales a través del transporte existente; su configuración entra en la identidad/revisión. Importar catálogo, analizar tecnología, programar revisitas y estimar desconocidos produce salida trazable. |
| R7 / revisión independiente | Documentos canónicos, CI y conjunto de cambios | Revisión de cumplimiento por requisito, después revisión de calidad/seguridad; suite completa, verificador fundacional y prueba CLI de punta a punta. Integración local preservando AGENTS/canvas del usuario. No se cierra una fila por existir su archivo o test. |

### Operaciones y oráculos atómicos

- [x] R1.1 Reproducir con un nodo OSM sintético con `type/id`, nombre y coordenadas, pero sin `website`; exigir candidato con localizador del registro y `website` desconocido. Mantener distintos `node/way/relation` y no deduplicarlos por el host OSM.
- [x] R1.2 Reproducir sitemap con `/car/1`; aplicar una frontera común a todas las expansiones, incluidas rutas codificadas, dominios distintos, medios y SPA. Conservar la pista de superficie y el motivo de exclusión; no descargar detalles.
- [x] R2.1 Definir bindings con adaptador, endpoint, países/clases, evaluación, parámetros y límite de páginas; validar campos, esquemas HTTPS, país y compatibilidad con estrategia antes de generar red.
- [x] R2.2 Construir clientes/parsers desde formatos documentados primarios. Probar payload de éxito, negocio sin web, paginación, página vacía terminal, respuesta inesperada, exceso de registros y cursor repetido. El controlador recibe filas, continuación y deuda explícitas.
- [x] R2.3 Probar que una consulta sin binding sigue bloqueada; una consulta admitida conserva el texto solo como procedencia de búsqueda. Un resultado nunca obtiene país, actividad o identidad porque coincidía con la consulta.
- [x] R3.1 Importar catálogo con versión, URL de origen, fecha, checksum, país y unidades. Aceptar normalización y alias revisados; rechazar duplicados contradictorios y versiones incompatibles, conservar alias ambiguos sin resolverlos arbitrariamente y devolver unknown para nombres ajenos al catálogo.
- [x] R3.2 Resolver municipio/núcleo y región conservando los valores originales, candidatos alternativos y regla aplicada; `unknown` y `ambiguous` nunca se convierten en coincidencia automática.
- [x] R3.3 Probar estimación sobre una población sintética conocida y grupos de captura definidos: solapamiento, ninguna coincidencia, única fuente, fuentes dependientes y datos caducados. Reportar observados separados de estimados, supuestos y límites; mantener `coverage_ratio=null` para el universo abierto.
- [x] R4.1 Probar firmas positivas y negativas, coincidencias múltiples, HTML genérico, código mostrado como texto, páginas de error y proveedores compartidos. Las pistas tienen selector, regla y versión.
- [x] R4.2 Probar pistas de endpoints/rutas como referencias no autorizadas, sin acceso adicional ni fusión de dealers por proveedor; integrar señales en observaciones retenidas y exportables.
- [x] R5.1 Probar calendario vencido/no vencido y reloj inválido; una generación se crea una vez aun con dos procesos y se conserva tras reinicio.
- [x] R5.2 Probar pausa y revisión, límites por tick, fallo entre generación/confirmación, tareas antiguas en curso y deuda acumulada. La inspección del calendario no crea base, filas ni ejecuciones.
- [x] R6.1 Ejecutar en base temporal el recorrido catálogo → plan/canal → worker con transporte simulado → candidato sin web → revisión → handoff; probar también revocación durante fetch y cambio de configuración.
- [x] R6.2 Ejecutar en la misma base revisita tras reinicio y estimación explicable; comprobar observaciones antiguas/nuevas, separación de país/clase/POS y mantenimiento de supresiones.
- [x] R6.3 Conservar resultados de canal vacíos/parciales, su retención y supresión; Sirene no retiene el raw con campos privados. La continuación revisada crea plan/tarea nuevos sin resetear presupuesto. Migración aditiva a base v2 impide interpretación incorrecta por workers antiguos; prueba física simulada cuenta robots/peticiones y bloquea redirección a inventario antes de descargar.
- [x] R7.1 Revisar código y contraejemplos por agente distinto del autor; corregir hallazgos antes de la revisión de calidad posterior.
- [x] R7.2 Ejecutar `python -m unittest discover -s tests/discovery -p 'test_*.py' -q`, `python tools/verify_foundation.py` y `git diff --check`; observar salidas y alcance antes de integrar.

Evidencia de aceptación: suites `test_discovery_frontier`, `test_channels`, `test_geography`, `test_estimation`, `test_technology`, `test_scheduling`, `test_channel_engine`, `test_channel_operations`, `test_completion_workflow`, `test_completion_cli`, más regresiones existentes y verificador fundacional. La revisión independiente reprodujo y cerró límites de consultas incompatibles, cursores agotados, coordenadas gigantes, catálogo fuera de ámbito, reloj inválido, revocación en replay, redirección de canal, parseo sin diagnóstico y rutas técnicas ejecutadas antes de revisión. Las pruebas físicas simulan exclusivamente la red; ningún resultado se presenta como campaña real.

### Límites que necesitan evidencia operativa propia

Disponibilidad actual, permisos particulares, catálogos nacionales completos, calidad de fuentes reales, calibración de identidad y campañas nacionales no se acreditan con fixtures. Cada canal informa soporte técnico y estado de admisión por separado. Cualquier limitación de esos ámbitos queda visible en la salida y en su evaluación; no habilita un cierre ficticio de las capacidades locales anteriores.
