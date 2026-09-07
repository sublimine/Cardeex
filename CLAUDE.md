# Guía operativa de Cardeex

## Identidad no negociable

Este repositorio contiene únicamente **Cardeex**. Cardeex no tiene relación de continuidad con Cardex ni con Cardeep. Elias autorizó el 7 de septiembre de 2026 consultar ambos proyectos en modo de solo lectura como fuentes históricas comparativas para investigación y auditoría.

Una pepita encontrada allí es una hipótesis con procedencia, no una decisión heredada. Debe contrastarse y decidirse de nuevo dentro de Cardeex. No copies, migres ni fusiones código, datos, recetas, memorias o grafos, y no modifiques los proyectos legado.

## Fuente de verdad y orden de lectura

La verdad activa vive en GitHub y en los documentos versionados de este repositorio. Para recuperar el proyecto:

1. Lee `README.md`.
2. Lee completo `docs/CARDEEX_FOUNDATION.md`.
3. Consulta `docs/research/vehicle-scope-2026-09-07.md` para cifras y justificación del alcance.
4. Usa `docs/knowledge/00-Cardeex-Home.md` como entrada de Obsidian.

No conviertas una nota de sesión, un artefacto generado por Graphify o una inferencia en verdad canónica sin reflejarla primero en la documentación versionada.

## Estado de autorización

A fecha de 2026-09-07 Cardeex está en fase de debate, procedimientos y diseño. No hay implementación de producto autorizada todavía. Sí están aprobados la misión, las distinciones ontológicas y la jerarquía de alcance descritas en los fundamentos.

## Estándares de razonamiento

- Distingue siempre hechos verificados, decisiones de Elias, recomendaciones y cuestiones abiertas.
- Fecha y cita toda cifra de mercado; no presentes comparaciones de definiciones distintas como equivalentes exactos.
- El objetivo del 100% se mide por `país × clase_de_vehículo × tipo_de_fuente`, nunca como un porcentaje global ambiguo.
- Conserva la evidencia de origen. La deduplicación debe ser explicable, reversible y acompañada de confianza.
- No confundas plataforma, cuenta publicadora, vendedor, entidad legal, punto de venta, anuncio ni vehículo canónico.
- No confundas clase de activo, condición, canal de venta, tipo de vendedor ni estado legal o de fin de vida.

## Obsidian y Graphify

La raíz de este repositorio es el vault independiente de Cardeex. El grafo de Obsidian y cualquier grafo de Graphify deben contener solo archivos y nodos de `C:\Users\elias\Cardeex`. Nunca se unen con otro vault o grafo salvo orden explícita de Elias.

Para regenerar el grafo local desde la raíz de Cardeex:

```powershell
graphify update .
```

Para consultarlo:

```powershell
graphify query "<pregunta sobre Cardeex>" --graph .\graphify-out\graph.json
```

`graphify-out/` es derivado y está ignorado por Git. El grafo de Cardeex debe permanecer local al repositorio: no se registra en el grafo global ni se fusiona con ningún otro proyecto.

## Higiene documental y de checkpoints

- Actualiza el documento canónico existente antes de crear otro documento.
- No guardes resúmenes de sesión como documentación permanente.
- Mantén como máximo un checkpoint transitorio activo y, preferiblemente, ninguno.
- Cuando un checkpoint se restaure o su contenido pase a documentación canónica, elimínalo antes de crear otro.
- Una investigación separada solo se conserva si aporta evidencia reproducible que vaya a influir en decisiones próximas.
- Obsidian y Graphify son vistas derivadas; no conviertas su estado local o archivos de interfaz en documentación del proyecto.
