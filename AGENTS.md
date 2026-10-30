# ARNES ARGOS (entorno minimo para cmdc - comando code)

Este proyecto tiene ARGOS/OSMA instalado en:
  - Memoria SQLite: .arnes/arnes.db (cues, experiences, observations, patterns)
  - Exports JSONL: .arnes/memory/export/ (por agente)
  - Config proyecto: .arnes/config.json
  - Perfil: .arnes/project.json

cmdc mantiene su motor nativo y su modelo por defecto. No se inyecta
persona Atlas ni roster: respeta la optimizacion de tokens de Command Code.

Si necesitas consultar la memoria ARGOS, los agentes del party o la
configuracion del proyecto, lee los archivos listados arriba bajo demanda.
