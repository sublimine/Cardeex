# Cardeex

Cardeex es un proyecto nuevo e independiente para construir una API viva, histórica y verificable del inventario de vehículos publicado digitalmente en España, Francia, Alemania, Países Bajos, Bélgica y Suiza.

> Regla absoluta: **Cardeex no es Cardex ni Cardeep**. No se importa, copia, fusiona ni presupone ningún dato, código, receta, decisión o arquitectura de esos proyectos.

## Estado actual

Base de arquitectura y contratos, más sistema local de descubrimiento implementado por encargo de Elias el 7 de septiembre de 2026. Incluye perfiles nacionales, canales específicos admitibles, catálogos/alias versionados, clasificación tecnológica, revisitas persistentes, evidencia, revisión, cola recuperable y estimación condicionada con desconocidos explícitos. No hay producto desplegado, campañas nacionales certificadas ni inventario real adquirido. Los formatos soportados y los límites se detallan en el manual: estrategias investigadas no equivalen a conectores certificados.

El próximo paso operativo es verificar geografía, evaluar canales gratuitos y ejecutar pilotos acotados de descubrimiento; el siguiente proyecto de estrategia es adquisición/scraping por superficie. El módulo local no sustituye el control productivo ni certifica el 100% del universo abierto.

## Punto de entrada

- [Fundamentos canónicos](docs/CARDEEX_FOUNDATION.md): misión, mapa maestro, decisiones, admisión y contratos para descubrimiento/scraping.
- [Dominio y evidencia](docs/architecture/01-domain-and-evidence.md): entidades, atributos, historia, deduplicación y divergencias.
- [Inventario y API](docs/architecture/02-inventory-and-api.md): vistas por dealer/POS, deltas, snapshots y eventos; [OpenAPI](contracts/openapi.yaml).
- [Ejecución y operaciones](docs/architecture/03-runtime-and-operations.md): persistencia, recuperación, SLO, seguridad y capacidad.
- [Verificación y construcción](docs/architecture/04-verification-and-build-order.md): tareas, casos adversariales, aceptación y siguiente paso.
- [Sistema de descubrimiento](docs/architecture/05-discovery-system.md): software, estrategias nacionales, fuentes, operación y límites.
- [Evaluación del alcance de vehículos](docs/research/vehicle-scope-2026-09-07.md): cifras, fuentes, cautelas metodológicas y razonamiento de prioridad.
- [Inicio del vault](docs/knowledge/00-Cardeex-Home.md): navegación para Obsidian.
- [Mapa visual](docs/knowledge/Cardeex-Foundation.canvas): canvas independiente de Cardeex.

## Dictamen de producto aprobado

**Diseñar amplio, ejecutar estrecho.** Una integración por fuente, con captura de turismos, LCV, motos y autocaravanas en las superficies compartidas admitidas. Normalización y certificación por vertical: primero turismos/LCV, después motos y autocaravanas. Pesados quedan para una expansión posterior; remolques/caravanas y maquinaria están fuera del alcance activo.

## Verificar la base

Las dependencias se instalan en un entorno local aislado; el verificador posterior funciona sin red:

```powershell
python -m venv .venv-verify
.\.venv-verify\Scripts\python.exe -m pip install -r tools/requirements-verify.txt
.\.venv-verify\Scripts\python.exe tools/verify_foundation.py
.\.venv-verify\Scripts\python.exe -m unittest discover -s tests/discovery -p "test_*.py" -v
git diff --check
```

Los resultados validan contratos, ejemplos, modelos finitos y aritmética de planificación. La capacidad a escala, las fuentes reales y los SLO necesitan las pruebas de implementación descritas en el plan.

La misma comprobación y la suite de descubrimiento están preparadas en [Verify foundation](.github/workflows/verify-foundation.yml) para pushes a `main` y pull requests, con permisos de solo lectura y acciones fijadas a commit. Git/CI conservan las ejecuciones; no se crean checkpoints ni informes de estado duplicados.

## Usar el descubrimiento

```powershell
python -m discovery --help
python -m discovery plan --countries ES,FR,DE,NL,BE,CH --epoch 2026-09 --limit 1000
python -m discovery coverage
python -m discovery doctor
```

Estos comandos no acceden a fuentes externas. Sin `--localities`, el plan contiene semillas nacionales y declara geografía pendiente. La red requiere `run --policy` con evaluación, hosts y presupuesto explícitos; no se incluyen permisos reales preaprobados. El estado local vive en `.cardeex-local/`, ignorado por Git y Graphify. Importación, revisión y exportación: [manual canónico](docs/architecture/05-discovery-system.md).
