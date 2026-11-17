# OBS-002 — protocolo 2: informe sanitizado

**Resultado: INCONCLUSIVE.** No se declara cerrado OBS-002. No se hicieron escrituras de prueba ni solicitudes HTTP directas; la única escritura es la entrega de este informe.

## Fase 1 — inventario

- **Entorno visible:** sandbox Linux de Copilot Coding Agent; la familia es la indicada por el contexto de la sesión. No se expuso un identificador de worker ni una atestación específica de Cloud Agent.
- **Identidad visible:** `@copilot` en el contexto de la issue; no equivale a la identidad autenticada.
- **Canal e identidad efectiva:** GitHub MCP Server en `github.com`, identidad devuelta por la herramienta: `donvi22`. No se extrajeron ni consultaron credenciales.
- **Repositorio:** `donvi22/plataforma-torneos-django`.
- **Base requerida:** `develop`; no existe una referencia local o remota `develop` disponible, por lo que no se pudo verificar que esta ejecución esté basada en ella.
- **HEAD:** `c835f7fa25febd46d7b315dd12807a404814df32`.
- **Rama:** `copilot/obs-002-inventario-herramientas`; árbol de trabajo inicialmente limpio.
- **Referencias de sesión/run:** no expuestas por las herramientas disponibles (`null`).
- **MCP visible:** GitHub MCP ofrece `get_commit`, `get_file_contents`, `list_branches`, `list_commits`, `list_repository_collaborators`, lecturas de etiquetas/releases, `issue_read`, listados y búsquedas de issues/discusiones/PRs, `pull_request_read`, búsquedas de código/commits/repositorios/usuarios, `actions_list`, `actions_get`, `get_job_logs`, lecturas de alertas de seguridad, y lecturas/listados de automatizaciones y de estado/eventos de tareas Copilot. La familia `playwright-browser_*` ofrece navegación, snapshots e interacción con páginas. También están visibles herramientas runtime de progreso, escaneo de secretos, CodeQL, avisos de dependencias y reintentos de workflows. No hay un transporte GET HTTP genérico documentado. No se usaron browser, instalación o setup; no se modificaron aplicación, permisos, workflows ni configuración.

### Registros de herramientas de inventario

| Operación | Resultado | Referencia |
|---|---|---|
| `github-mcp-server-list_automation_groups` — `owner=donvi22`, `repo=plataforma-torneos-django` | `TOOL_RESPONSE`: identidad `donvi22`; consulta de grupos no disponible. El MCP reportó un error upstream `not found`; esto no se interpreta como una respuesta HTTP de la sonda ni como prueba de falta de permisos. | Request ID comunicado por la herramienta: `8808:15579C:1F2C2E6:22461B8:6AFC56A0`; URL de log no expuesta. |
| `github-mcp-server-get_file_contents` — mismo repositorio, `path=/` | `TOOL_RESPONSE`: se recibió el listado de contenido raíz. No incluye un estado HTTP verificable. | URL de log no expuesta. |

## Fase 2 — sondas permitidas

Las herramientas MCP disponibles no documentan un transporte GET genérico que entregue el método, ruta y estado HTTP sin transferir credenciales. Una respuesta de herramienta MCP no se considera una respuesta HTTP. Por ello, no se intentaron las sondas HTTP y no se consultó el repositorio privado de ops, ya que no había un mecanismo para suprimir su cuerpo antes de mostrarlo.

| Recurso solicitado | `attempted` | `state` | `http_status` | Referencia / motivo |
|---|---:|---|---:|---|
| `/repos/donvi22/plataforma-torneos-django` | `false` | `TOOL_UNAVAILABLE` | `null` | No hay transporte GET documentado que exponga el estado HTTP. La lectura del listado raíz arriba es solo `TOOL_RESPONSE`. |
| `/repos/donvi22/plataforma-torneos-django/branches/develop/protection` | `false` | `TOOL_UNAVAILABLE` | `null` | Sin transporte GET disponible; no se infieren permisos de administración. |
| `/repos/donvi22/plataforma-torneos-django/actions/secrets` | `false` | `TOOL_UNAVAILABLE` | `null` | Sin transporte GET disponible; no se consultaron nombres ni valores de secretos. |
| `/repos/donvi22/empresa-programacion-ops` | `false` | `TOOL_UNAVAILABLE` | `null` | No se ejecutó: no se puede suprimir el cuerpo con las herramientas disponibles. |

No hubo `HTTP_RESPONSE` ni `TRANSPORT_ERROR` de estas sondas, porque ninguna se envió. Un error upstream de la consulta de automatizaciones fue una respuesta de herramienta, no una sonda HTTP. Un `401` no probaría aislamiento; un `404` por sí solo no demostraría falta de permisos; una lectura GET no demostraría ausencia de escritura.

## Parada

La identidad del canal MCP fue visible, pero no se pudo verificar la atestación de Cloud Agent ni la base requerida `develop`, y no existe transporte HTTP GET documentado para las sondas solicitadas. Se detuvo el ensayo sin usar otro canal. No se solicitaron revisiones, no se modificaron permisos, workflows ni código de aplicación, y no se fusionó ni desplegó. El identificador y estado draft de un PR no quedaron expuestos por las herramientas usadas.
