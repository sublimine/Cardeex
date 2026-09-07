---
title: Fundamentos de Cardeex
type: canonical-project-context
status: approved-foundation
last_updated: 2026-09-07
owner: Elias
aliases:
  - Cardeex Foundation
tags:
  - cardeex
  - canonical
  - strategy
---

# Fundamentos de Cardeex

Este documento es la fuente canónica del contexto acordado hasta el 7 de septiembre de 2026. Si una nota, conversación o grafo lo contradice, prevalece este documento hasta que Elias apruebe y se registre un cambio.

## 1. Frontera absoluta del proyecto

Cardeex es un proyecto totalmente nuevo. **No es una continuación, refactor, sustituto ni mezcla de Cardex o Cardeep.** No se heredan código, datos, recetas, arquitectura, decisiones, taxonomías, métricas ni conocimiento de esos proyectos.

Compartir el dominio de los vehículos no crea relación entre ellos. Obsidian y Graphify también deben usar vaults y grafos independientes.

## 2. Objetivo al 100%

Construir y mantener viva una API del inventario de vehículos que se publican digitalmente en:

- España
- Francia
- Alemania
- Países Bajos
- Bélgica
- Suiza

La ambición final es cubrir el 100% del universo digital observable: desde grandes plataformas hasta la web de un concesionario, compraventa, garaje o desguace pequeño, siempre que exista inventario de vehículos publicado digitalmente.

Para cada fuente y entidad relevante, Cardeex debe poder:

1. Descubrirla y clasificarla.
2. Obtener todo su stock digital observable.
3. Normalizarlo sin perder la evidencia original.
4. Servir el estado actual mediante una API viva.
5. Registrar el delta: altas, bajas y cambios, incluidos precio y fotografías.
6. Conservar el historial completo y la procedencia de cada observación.
7. Guardar la receta reproducible de adquisición.
8. Ordenar y consultar por país, provincia o región, ciudad y punto de venta.
9. Asignar identificadores únicos y estables a las entidades y puntos de venta.
10. Detectar fallos, alertar con el origen exacto, intentar la recuperación y aislar el fallo para que Cardeex no se caiga.

El 100% se refiere al inventario **publicado digitalmente y observable**. Cardeex no puede conocer vehículos que nunca se hayan publicado por ningún medio accesible.

## 3. Unidad de monitorización y modelo conceptual

Una fuente o plataforma es el sistema que Cardeex monitoriza. Quien publica en ella no se convierte automáticamente en dealer o punto de venta.

Ejemplo aprobado: si Juan publica un único coche en AutoScout24 España, AutoScout24 es la fuente monitorizada; Juan es un publicador particular vinculado a ese anuncio. Juan no es por ello un punto de venta.

La cadena conceptual inicial es:

```text
source/platform
  -> publisher account
  -> seller
  -> legal entity
  -> point of sale
  -> listing
  -> canonical vehicle
  -> observations/history
```

No todos los eslabones existirán o podrán resolverse para cada anuncio. El modelo debe admitir identidad desconocida, parcial o probabilística sin inventar relaciones.

### Entidades que deben permanecer separadas

- **Fuente/plataforma:** lugar digital observado; puede alojar inventario propio o de terceros.
- **Cuenta publicadora:** identidad técnica que publica dentro de una fuente.
- **Vendedor:** persona u organización que ofrece el vehículo.
- **Entidad legal:** organización jurídicamente identificable, cuando sea resoluble.
- **Punto de venta:** ubicación comercial física o virtual operada por una entidad profesional.
- **Anuncio/listing:** representación específica de una oferta en una fuente.
- **Vehículo canónico:** activo real al que pueden apuntar varios anuncios.
- **Observación:** estado de un anuncio o vehículo visto en un instante determinado.

Un profesional, una entidad legal y un punto de venta tampoco son sinónimos. Un mismo profesional puede operar varios puntos de venta y una plataforma puede mezclar vendedores profesionales y particulares.

Sin cerrar todavía la taxonomía completa, la clasificación mínima deberá distinguir concesionario o compraventa profesional, garaje o taller, desguace, otro vendedor profesional y publicador particular. Un desguace solo cuenta como fuente de vehículos cuando ofrece unidades completas vendibles o reparables; sus piezas no entran en el núcleo.

## 4. Identidad, evidencia e historial

El mismo vehículo puede aparecer simultánea o sucesivamente en varias plataformas. Cardeex debe representarlo como un vehículo canónico relacionado con varios anuncios específicos de fuente.

Reglas iniciales:

- Nunca borrar o sobrescribir la evidencia cruda que explica una conclusión.
- No fusionar registros solo por parecido textual.
- Guardar señales, reglas, confianza y versión del proceso de resolución de identidad.
- Hacer que una fusión sea reversible cuando aparezca evidencia contradictoria.
- Distinguir desaparición observada, venta inferida, retirada, caducidad, error de adquisición y baja confirmada.
- Mantener el historial de atributos y medios, no solo la última versión.

## 5. Axioma de modelado

Estas dimensiones son ortogonales y no se deben colapsar:

```text
clase de activo
!= condición
!= canal de venta
!= tipo de vendedor o entidad
!= estado legal o de fin de vida
```

Consecuencias:

- Accidentado, siniestrado o salvage describe condición o historia; no es una clase de vehículo.
- Subasta describe un canal o evento de venta; no una clase de activo ni un tipo de entidad.
- Un desguace entra en el inventario de vehículos solo cuando publica vehículos completos vendibles o reparables. El inventario de piezas queda fuera del núcleo.
- Una caravana o remolque es un activo no autopropulsado y debe distinguirse de un vehículo a motor.

## 6. Medición honesta del 100%

La cobertura no se publicará como un único porcentaje global. La unidad mínima de medición es:

```text
país × clase_de_vehículo × tipo_de_fuente
```

Además, deben separarse al menos:

- universo de fuentes descubierto;
- fuentes clasificadas;
- fuentes técnicamente accesibles;
- fuentes con receta válida;
- fuentes monitorizadas dentro de la frecuencia acordada;
- inventario observado;
- frescura y completitud del inventario;
- cobertura certificada y fecha de la certificación.

La expansión a nuevas clases de vehículo nunca debe diluir ni volver ambiguo el 100% certificado de turismos.

## 7. Alcance aprobado por fases

Principio rector: **diseñar amplio, ejecutar estrecho**. El modelo raíz será `vehicle`, con extensiones tipadas, pero la cobertura se ejecutará por verticales y denominadores separados.

| Orden | Alcance | Decisión |
|---|---|---|
| Núcleo | Turismos | Primera certificación de cobertura al 100%. |
| Núcleo adyacente | Vehículos comerciales ligeros y furgonetas de hasta 3,5 t | Ingerir desde el principio cuando compartan fuente; medir por separado. Son demasiado grandes y cercanos al esquema de coche para omitirlos. |
| Expansión 1 | Motocicletas y scooters | Primera expansión formal por volumen y solapamiento digital. Los ciclomotores serán subtipo separado. |
| Expansión 2 | Autocaravanas y campers | Vertical menor pero de alto valor, con fuerte concentración europea en los países objetivo. |
| Expansión posterior | Camiones pesados de más de 3,5 t | Vertical B2B separada; piloto recomendado en Alemania y Francia, después España y Países Bajos. |
| Posterior y separada | Caravanas y remolques | Activos no autopropulsados; no mezclarlos con autocaravanas. |
| Prioridad baja | Autobuses y autocares | Volumen muy pequeño; abordar después o junto con la vertical pesada. |
| Fuera de Cardeex | Maquinaria agrícola y de construcción | Exige otra ontología, fuentes y compradores; posible producto futuro independiente. |

Quedan fuera de la primera expansión de motos: quads, triciclos, bicicletas eléctricas y vehículos no matriculados, hasta decisión explícita posterior.

Los conceptos de subasta, daño y salvage existirán desde el modelo inicial. Los conectores a subastas B2B autenticadas se posponen hasta después de las fuentes públicas prioritarias.

## 8. Dictamen estratégico aprobado

Ninguna categoría evaluada iguala a los turismos en volumen absoluto. La oportunidad no se decide solo por unidades:

- Los comerciales ligeros combinan volumen material, fuentes compartidas y bajo coste marginal de integración.
- Las motos tienen mucha más rotación de ocasión de la que sugiere la intuición y comparten plataformas y patrones de publicación.
- Las autocaravanas son una pepita de oro: menor volumen, precio alto, concentración relevante en los países objetivo y buen solapamiento digital.
- Los pesados merecen una vertical posterior por valor B2B y concentración de fuentes, no por volumen unitario.
- Autobuses y autocares no justifican prioridad temprana.
- Agricultura y construcción podrían tener valor, pero incluirlas convertiría Cardeex en “todo lo que tenga ruedas” y rompería el foco y el modelo.

Formulación acordada:

> Cardeex nace sobre una entidad genérica `vehicle`; opera primero turismos y furgonetas; incorpora después motos y scooters, autocaravanas y finalmente pesados. Así preservamos la ambición sin convertir el proyecto en “todo lo que tenga ruedas”.

## 9. Requisitos de fiabilidad ya expresados

Aunque la arquitectura todavía no se ha decidido, el resultado deberá contemplar:

- aislamiento de fallos por fuente y receta;
- alertas con causa y origen exactos;
- reintentos y autorreparación controlados;
- degradación parcial en vez de caída global;
- observabilidad de frescura, volumen, errores y cambios anómalos;
- recetas versionadas, reproducibles y auditables;
- historial inmutable o reconstruible de observaciones y transformaciones.

Estos son requisitos del producto, no decisiones tecnológicas todavía.

## 10. Estado al pausar la conversación

Elias aprobó la jerarquía de alcance anterior el 7 de septiembre de 2026 y pidió guardar todo antes de continuar al día siguiente.

No se ha elegido todavía:

1. El flujo de trabajo humano y de agentes.
2. Los procedimientos y estándares de investigación, cobertura y verificación.
3. La arquitectura técnica y el contrato de la API.
4. La taxonomía completa de fuentes y entidades.
5. La metodología formal de certificación del 100%.
6. Las fronteras legales, contractuales y de cumplimiento por país y fuente.
7. Los SLO, frecuencias de actualización, retención y política exacta de autorreparación.
8. El orden de ejecución por país y por fuente.

La próxima sesión debe continuar desde aquí, sin iniciar código ni asumir esas decisiones.

## 11. Evidencia cuantitativa

La investigación, sus cifras, fuentes y limitaciones se conservan en [Evaluación del alcance de vehículos](research/vehicle-scope-2026-09-07.md).
