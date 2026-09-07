# Instrucciones para agentes de Cardeex

Usa `CLAUDE.md` en este mismo directorio como guía operativa de alta prioridad, interpretando cualquier referencia de herramienta según el entorno del agente actual.

## Aislamiento obligatorio

Cardeex es totalmente independiente de Cardex y Cardeep. Desde el 7 de septiembre de 2026, Elias autoriza consultar ambos proyectos en modo de solo lectura como fuentes históricas comparativas durante investigación y auditoría.

Esa consulta no crea herencia: cada hallazgo debe conservar su procedencia, contrastarse de nuevo y aprobarse expresamente antes de convertirse en una decisión de Cardeex. Sigue prohibido copiar o adaptar código, datos, recetas, memorias o grafos, modificar los proyectos legado, o inferir que una decisión tomada allí aplica aquí.

La coincidencia de nombres, dominio o propósito no constituye autorización para mezclar proyectos.

## Arranque obligatorio

Antes de responder o actuar sobre Cardeex, lee en este orden:

1. `README.md`
2. `docs/CARDEEX_FOUNDATION.md`
3. `docs/research/vehicle-scope-2026-09-07.md` cuando la pregunta afecte a alcance o mercado
4. La documentación específica de la tarea, si ya existe

El repositorio y GitHub son la fuente canónica. Obsidian y Graphify son vistas derivadas y deben construirse exclusivamente desde este repositorio.

Si `graphify-out/graph.json` no existe o está desactualizado, ejecuta `graphify update .` desde la raíz de Cardeex antes de consultarlo. No uses ni combines otro archivo `graph.json`, y no registres Cardeex mediante `graphify global add`.
