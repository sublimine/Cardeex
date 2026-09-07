# Instrucciones para agentes de Cardeex

Usa `CLAUDE.md` en este mismo directorio como guía operativa de alta prioridad, interpretando cualquier referencia de herramienta según el entorno del agente actual.

## Aislamiento obligatorio

Cardeex es totalmente independiente de Cardex y Cardeep. Está prohibido leerlos como contexto del proyecto, copiar o adaptar sus archivos, importar sus datos o recetas, reutilizar sus grafos, o inferir que una decisión tomada allí aplica aquí. Solo Elias puede autorizar una comparación concreta y explícita en una petición futura.

La coincidencia de nombres, dominio o propósito no constituye autorización para mezclar proyectos.

## Arranque obligatorio

Antes de responder o actuar sobre Cardeex, lee en este orden:

1. `README.md`
2. `docs/CARDEEX_FOUNDATION.md`
3. `docs/research/vehicle-scope-2026-09-07.md` cuando la pregunta afecte a alcance o mercado
4. La documentación específica de la tarea, si ya existe

El repositorio y GitHub son la fuente canónica. Obsidian y Graphify son vistas derivadas y deben construirse exclusivamente desde este repositorio.

Si `graphify-out/graph.json` no existe o está desactualizado, ejecuta `graphify update .` desde la raíz de Cardeex antes de consultarlo. No uses ni combines otro archivo `graph.json`, y no registres Cardeex mediante `graphify global add`.
