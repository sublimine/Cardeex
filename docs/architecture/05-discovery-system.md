# Sistema de descubrimiento de Cardeex

Contrato operativo canónico del módulo `discovery/`. Encargo de Elias: construir descubrimiento extensible y específico por país, incluyendo profesionales con uno, tres o ningún vehículo verificable en este momento; sin servicios externos de pago. Esta entrega implementa el sistema local, no declara ejecutado un censo paneuropeo ni adquiridos los inventarios.

## 1. Frontera y decisión de diseño

Se adopta **motor común + perfiles nacionales versionados + evidencia independiente + exploración territorial recurrente**. Una estrategia solo de grandes portales pierde vendedores exclusivos de webs propias; una estrategia solo de buscadores depende de ranking y límites. El sistema combina ambas con registros, geografía, redes comerciales, directorios e índices históricos. Ninguna vía se trata como un censo de vendedores activos por sí sola.

Descubrimiento localiza fuentes, cuentas, profesionales, puntos de venta y superficies de inventario. La adquisición de anuncios, detalles, fotografías, deltas y reconciliación sigue las puertas G2–G4. El detector conserva indicios de enlaces a anuncios sin convertirlos en vehículos resueltos. No se eliminan candidatos por tamaño de stock, calidad visual de la web, posición en buscadores o rendimiento comercial.

| Responsabilidad | Implementación y límite |
|---|---|
| Estrategia nacional | [Perfiles](../../discovery/profiles.py) y seis JSON independientes; fuentes y restricciones citadas dentro de cada perfil |
| Frontera finita | [Planificador](../../discovery/planner.py): país → territorio → método → idioma/clase; hash de perfiles, geografía y ventana |
| Evidencia y revisión | [Modelo](../../discovery/model.py), [registro](../../discovery/store.py): afirmaciones, decisiones, relaciones y supresiones locales |
| Ejecución recuperable | [Cola](../../discovery/queue.py), [motor](../../discovery/engine.py): turnos justos, lease/fencing, reintentos, frontera diferida y presupuestos |
| Acceso y replay | [Transporte](../../discovery/transport.py), [adaptadores](../../discovery/adapters.py): acceso explícito limitado y parsers sin red |
| Medición y entrega | [Cobertura](../../discovery/coverage.py), [handoff](../../discovery/handoff.py), [CLI](../../discovery/cli.py) |

Es software ejecutable, no una lista de documentos. Los perfiles contienen **estrategias investigadas**, no extractores certificados de cada portal. HTML, JSON/JSONL, JSON-LD, resultados OSM/Overpass, filas de Common Crawl y sitemaps tienen adaptadores genéricos; consultas de buscadores y canales sujetos a revisión permanecen propuestas explícitas, no scraping automático encubierto.

## 2. Personalización nacional

Las fuentes primarias y la fecha de consulta están en `sources` de cada perfil. `checked_at` acredita una consulta documental, no actividad comercial, autorización ni completitud.

| País | Vías propias y adaptación | Riesgo que no se oculta |
|---|---|---|
| [España](../../discovery/profiles/ES.json) | Municipios INE, idiomas regionales, asociaciones de compraventa/reparación y caravaning, portales nacionales, redes de marcas | Islas, territorios extrapeninsulares, talleres con ventas ocasionales, páginas exclusivamente locales |
| [Francia](../../discovery/profiles/FR.json) | Sirene/COG, establecimientos distintos de empresas, comunas, redes y asociaciones locales; vocabulario auto/moto/utilitaire/camping-car | Difusión restringida, actividad registral distinta de stock real, pequeños garages rurales |
| [Alemania](../../discovery/profiles/DE.json) | Geografía municipal oficial, asociaciones y redes Autohaus/Kfz/Motorrad/Reisemobil, portales y páginas con Impressum | Registro empresarial no equivale a base pública gratuita; grupos/sucursales; terminología compuesta |
| [Países Bajos](../../discovery/profiles/NL.json) | RDW y negocios reconocidos, BOVAG, geografía neerlandesa, vocabulario autobedrijf/bestelauto/motor/camper | Reconocimiento RDW no prueba ventas; KVK requiere revisar condiciones vigentes; intermediarios y exportación |
| [Bélgica](../../discovery/profiles/BE.json) | BCE/KBO por canal permitido, neerlandés/francés/alemán, redes sectoriales y geografía regional | Public Search no se confunde con Open Data reutilizable; bilingüismo y ubicación transfronteriza |
| [Suiza](../../discovery/profiles/CH.json) | Zefix, municipios/cantones oficiales, AGVS/UPSA y canales suizos, cuatro idiomas nacionales | Domicilio jurídico distinto de POS; valles y áreas poco visibles; mercados transfronterizos |

Cada estrategia declara familia, independencia de evidencia, prioridad, clases aplicables, URLs respaldadas por citas, plantillas por idioma y modo de acceso. Una estrategia de autocaravanas no implica cobertura de coches ni motos. Una página en alemán o un dominio `.de` no prueba país del vendedor ni mercado del anuncio.

### Añadir otro país

1. Incorporar su perfil revisado en un directorio explícito y pasarlo con `--profiles-dir`.
2. Aportar geografía y vocabulario propios; identificar canales gratuitos, autenticados, no reutilizables y no evaluados.
3. Ejecutar validación y pruebas de planificación, incluidos lugares ambiguos, remotos y multilingües.
4. Revisar alcance de producto y ampliar el contrato de países del plano de control antes del handoff. El motor puede planificar un séptimo país; el contrato actual solo admite ES/FR/DE/NL/BE/CH y falla explícitamente al exportar otros.

No se modifica la lógica del motor por una nueva capital, proveedor o idioma. La forma ISO alfa-2 se valida; la pertenencia real al estándar y las decisiones de mercado se revisan, no se inventan por regex.

## 3. Identidad, evidencia y estados

- UUIDv7 opacos para candidatos, evidencia, decisiones y eventos. El hash de visita o de candidato no es un dealer ID.
- Mismo localizador observado y mismo tipo reclamado permiten agrupar **candidatos**; otras rutas, tipos y sucursales no se fusionan por compartir dominio. Se conservan rutas SPA con fragmento; el fetch HTTP omite el fragmento conforme al protocolo.
- Cada observación guarda método/versión, localizador de origen, fecha observada, grupo de evidencia, política, vencimiento, señales y checksum del documento cuando existe. La misma descarga no cuenta como múltiples corroboraciones independientes.
- Fecha/lugar de la búsqueda, idioma, domicilio publicado, mercado y ubicación del POS son conceptos diferentes. Las señales de búsqueda permanecen en la tarea, no se convierten en atributos observados.
- `accepted` significa candidato revisado, **no** dealer certificado, negocio activo, permiso de adquisición ni inventario completo.
- Las decisiones usan revisión esperada y registran actor, motivo y fecha. Un duplicado conserva su historia y puede revertirse mediante una nueva decisión. Ciclos y restricciones `cannot_link` incompatibles bloquean la operación; una contradicción posterior retira el handoff afectado hasta revisión.
- Relaciones tipadas y evidencia por afirmación viajan en el companion ledger del handoff, porque el `DiscoveryCandidate` fundacional no contiene todos esos detalles. No se inventan claves de entidad resuelta.

### Compatibilidad con el contrato fundacional

`DiscoveryCandidate.observed_locator` admite ahora HTTP además de HTTPS para no excluir webs antiguas. Se amplía la validación del localizador de descubrimiento; no se rebaja TLS de la API ni se habilita HTTP por defecto en fetch. Lectores anteriores necesitan actualizar su validador antes de aceptar nuevos candidatos HTTP. El rollback operativo puede detener su exportación, pero no convertir sus URLs silenciosamente a HTTPS ni borrarlos del registro.

El handoff es JSONL: cabecera, candidatos validados con sus afirmaciones/relaciones, bloqueos, recibos de supresión y pie con checksum de registros de candidatos/bloqueos. La falta del pie indica exportación incompleta. Usa una lectura consistente y memoria incremental; un consumidor no debe publicar mientras no haya validado el pie y sus relaciones. El programa no sube ese archivo ni sirve una API externa.

## 4. Geografía, frontera y protección de la larga cola

El operador aporta localidades con `country`, `code`, `name` y opcionalmente `region`/`rural`; los códigos proceden del catálogo documentado por cada perfil. La lista proporcionada se etiqueta **muestra aportada**, aunque sea grande. No se afirma que contenga todos los municipios sin una verificación independiente del catálogo y su versión.

El plan identifica perfiles completos, geografía, versión del planificador y ventana `epoch`. La paginación conserva la misma identidad; cada tarea tiene ordinal. Su reanudación utiliza conteos y acceso al tramo necesario del round-robin: no construye las tareas previas. La prueba de ocho millones comprueba planificación de páginas y memoria, **no ocho millones de solicitudes ni throughput de scraping**.

Los turnos de la cola se conservan en disco por estrato. La priorización no usa tamaño mínimo de stock. Cada estrato elegible vuelve a recibir turno; se separan prioridad, restricciones de acceso y antigüedad. No se afirma cumplir una frecuencia de servicio hasta medirla sobre una campaña y recursos concretos.

Los enlaces generan trabajo acotado. Los ciclos comparten claves de visita dentro de su plan; el exceso de hijos/profundidad se conserva en `deferred_frontier`. `release-frontier` permite continuar después de revisar límites. Alcanzar un límite produce `partial`/deuda, nunca “país terminado”. Los detalles y activos reconocibles no se incorporan a esta cola de descubrimiento; URLs ambiguas requieren revisión y no equivalen a una receta de inventario.

## 5. Acceso gratuito, seguridad y presupuestos

No hay clientes de LLM, proxies de pago, CAPTCHA solvers, facturación de API ni despliegue automático. Se usan el equipo, almacenamiento y conexión existentes: **cero gasto externo autorizado no significa computación infinita ni electricidad gratuita**.

La red solo se habilita con `run --policy archivo.json`. La política contiene hosts explícitos, responsable, referencia de evaluación, vencimiento, máximo de solicitudes/bytes/tiempo, intervalo y propósito `discovery`. El contenido de una web no puede editar esa política. Métodos manuales o pendientes de permisos siguen bloqueados aunque se añada un host a la lista.

- HTTPS por defecto; HTTP público exige `allow_http: true`. No cookies, credenciales, proxies de entorno ni destinos de red privada.
- Validación de DNS y conexión a la IP pública comprobada; TLS conserva SNI/validación del hostname. Se repiten controles para redirecciones.
- Robots se evalúa antes de acceder: una respuesta `404` se trata como ausencia de reglas; otros fallos de acceso o contenido inválido deniegan el acceso según las reglas implementadas. Robots **no concede derechos de reutilización**. [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html).
- Cada intento físico, robots y redirección consume presupuesto; la cuenta y los cooldowns quedan en SQLite, compartidos entre procesos y reinicios. La política es inmutable por referencia; su revocación bloquea nuevos accesos y publicación de resultados.
- Límites de tamaño comprimido/descomprimido, parseo, JSON/XML, profundidad, URLs y plazo; sin ejecución de scripts, instrucciones de página ni entidades XML externas.
- El servicio público Nominatim no se usa para enumeración sistemática territorial: su política lo prohíbe. OSM se trata como evidencia parcial con su licencia y límites del canal elegido, no como directorio empresarial completo. [Política de Nominatim](https://operations.osmfoundation.org/policies/nominatim/).

La investigación documental no crea permisos de fuente. Los canales que requieren registro, evaluación o acuerdo permanecen visibles para que el siguiente proyecto no los confunda con cobertura conseguida.

## 6. Persistencia, supresión y recuperación

SQLite es el **área local de trabajo de descubrimiento**, no sustituye el PostgreSQL de control productivo exigido por OPS-013 ni almacena el futuro historial masivo de anuncios. Transacciones `BEGIN IMMEDIATE`, savepoints anidados, integridad referencial, índices, `journal_mode=DELETE` y `synchronous=FULL`. El modo WAL se evita por la vulnerabilidad documentada en versiones anteriores a sus correcciones, incluida la versión 3.45.1 disponible en el entorno. [SQLite, WAL-reset bug](https://www.sqlite.org/wal.html#walreset).

Una entrega de worker confirma evidencia, expansión y estado final bajo el mismo fencing y transacción. Si muere, el lease caduca y el trabajo vuelve a ser elegible; el proceso antiguo no puede publicar. La red queda fuera de la transacción. Cada importación JSONL acotada se confirma entera o revierte.

La caducidad elimina evidencia local vencida y sus relaciones; una supresión elimina material y conserva recibos mínimos/tombstones para impedir replay ordinario. Cambiar solo el vencimiento no recrea una observación ya eliminada. Los artefactos suprimidos no reaparecen al reimportar sus mismos bytes. Un candidato sin evidencia viva no se exporta.

Límite deliberado: **esta entrega no certifica restauración de backups anterior a una supresión ni controla copias que el usuario exportó fuera**. No restaurar una base vieja como autoridad vigente; preservar y aplicar el deletion ledger actual antes de cualquier recuperación. PITR, eliminación propagada, autorización multiusuario y pruebas de recuperación productiva siguen perteneciendo a G3–G5. El uso local confía en el operador del sistema de archivos; no es un servicio multi-tenant.

## 7. Medición verificable

El informe presenta todas las celdas `país × clase × tipo_de_fuente`, incluidas desconocidas/vacías. Se distinguen candidatos fuente, profesionales, cuentas y POS; decisiones, grupos de evidencia, trabajo pendiente/bloqueado, consultas humanas, frontera no generada, localidades sin explorar y antigüedad de deuda.

Nunca se construye una combinación país/clase mezclando afirmaciones independientes incompatibles. Un código local ambiguo o una definición geográfica que cambió entre planes queda señalado, no suma cobertura en varios territorios. Los contextos geográficos se versionan por plan.

`coverage_ratio=null` para el universo abierto. `certified_sources=0` en este módulo porque descubrir no certifica adquisición. Un informe con trabajo `done` describe documentos examinados, no stock completo ni ausencia de vendedores. La inferencia estadística de recall y su calibración requieren una muestra independiente que aquí todavía no existe; no se fabrica un intervalo.

## 8. Operación reproducible

Desde la raíz de Cardeex, usando Python 3.11+; para exportación contractual y suite completa, el entorno de validadores ya documentado:

```powershell
python -m discovery --help
python -m discovery profiles
python -m discovery plan --countries ES,FR,DE,NL,BE,CH --epoch 2026-09 --limit 1000
python -m discovery coverage
python -m discovery queue --status blocked --limit 50
python -m discovery doctor
```

`plan` sin `--localities` emite las semillas nacionales y marca los seis países sin geografía aportada. Para el barrido territorial se pasa `--localities ruta.json`; `--offset` usa el `next_offset` devuelto para continuar sin cambiar `epoch` ni entradas. Otra ventana crea otro plan y no elimina los anteriores.

Replay local y revisión:

```powershell
python -m discovery inspect-file --file captura.html --origin https://dealer.example/ --content-type text/html --policy-ref assessment:local-approved --evidence-group source:dealer --observed-at 2026-09-07T10:00:00Z --expires-at 2026-09-08T10:00:00Z
python -m discovery ingest --file observaciones.jsonl
python -m discovery candidate UUID
python -m discovery review --candidate UUID --status needs_evidence --revision 1 --actor operator:elias --reason "Comprobar stock y entidad con evidencia independiente"
.\.venv-verify\Scripts\python.exe -m discovery export
```

`captura.html`, `ruta.json`, `observaciones.jsonl`, `UUID` y las fechas del ejemplo son entradas ilustrativas, no archivos incluidos ni evidencia real. Es obligatorio indicar fecha original y vencimiento aprobados con `--observed-at`/`--expires-at`; no se usan la fecha del replay ni fechas de publicación inventadas. El raw de un replay debe proceder de material que se pueda conservar.

Una política de red **solo se crea después de evaluar su alcance y derechos**. Campos aceptados: `allowed_hosts`, `expires_at`, `operator`, `policy_ref`, `max_requests`, `max_bytes`, `timeout_seconds`, `min_interval_seconds`, `purpose`, `max_redirects`, `allow_http`. No se entrega una autorización real prefirmada. Ejecutar `python -m discovery run --policy politica-aprobada.json --max-tasks 20 --max-seconds 60`; revisar el resultado, los bloqueos y presupuestos antes de ampliar. Renovar una evaluación requiere nueva referencia, no resetear el contador de la anterior.

Estados de recuperación: `requeue --task CLAVE --reason MOTIVO`, `release-frontier --limit 100`, `expire`, `suppress --candidate UUID --actor ACTOR --reason MOTIVO`, `revoke-policy --policy-ref REF --actor ACTOR --reason MOTIVO`. Estos comandos no cambian derechos de terceros ni certifican inventario.

## 9. Pruebas, promoción y próximo paso

```powershell
.\.venv-verify\Scripts\python.exe -m unittest discover -s tests/discovery -p "test_*.py" -v
.\.venv-verify\Scripts\python.exe tools/verify_foundation.py
python -m compileall -q discovery tests/discovery
git diff --check
```

Pruebas negativas: tres vehículos, cero desconocido, rutas SPA, múltiples tipos por dominio, duplicados reversibles, cannot-link transitivo, caducidad/supresión/reimportación, rollback anidado, fence vencido/NaN, presupuestos compartidos, restricciones HTTP/SSRF/DNS, robots/redirecciones/429, XML hostil, JSON ambiguo, geografía fronteriza, séptimo país y paginación al final de un plan de millones.

G1 se materializa en estrategia y software local probado. El siguiente paso operativo es cargar y verificar los catálogos territoriales, evaluar los canales gratuitos seleccionados y ejecutar campañas acotadas con auditoría independiente. En paralelo, G2 diseña las recetas de adquisición por superficie. Quedan sin certificar campañas nacionales, completitud real, precisión del universo de dealers, rendimiento distribuido, acceso a todos los canales y operación 24/7. Se sustituyen estos límites por evidencia, nunca por promesas de “100%”.
