# Infraestructura de Pruebas E2E (TDD) - RAG Local

Este documento detalla la estrategia, arquitectura e inventario de la suite de pruebas E2E para el proyecto `rag-local`.

---

## 1. Filosofia de Pruebas

La suite de pruebas E2E adopta un enfoque **TDD (Test-Driven Development)** de caja negra e integración de extremo a extremo. Su propósito principal es asegurar que los flujos completos de escaneo, procesamiento (chunking), metadatos, indexación incremental, consulta semántica y formateo del prompt XML funcionen en conjunto.

### Aislamiento de Entorno
Cada prueba se ejecuta dentro de un entorno temporal aislado generado por `pytest`. A través de las variables de entorno:
- `RAG_ROOT`: Redirige la raíz de los archivos de base de datos a carpetas temporales.
- `RAG_REPO_ROOT`: Modifica la ruta física simulada del monorepo a escanear.
- `RAG_CHROMA_PATH`: Reubica los archivos de persistencia de la base de datos de forma local y temporal para el test.
- `RAG_MOCK_API`: Habilita el modo offline ("1"), lo que permite generar embeddings deterministas de 768 dimensiones basados en hashes SHA256 evitando inicializar el modelo local de `sentence-transformers` en CPU/GPU durante las pruebas. También genera respuestas mockeadas del LLM exponiendo fragmentos clave del prompt original.

---

## 2. Arquitectura de las Pruebas

La suite se compone de un fixture compartido `setup_test_env` definido en [conftest.py](file:///c:/Users/Leo/Repo/project-semi/rag-local/tests/e2e/conftest.py) que se aplica automáticamente para orquestar la recarga de módulos (`importlib.reload`) del backend a fin de asegurar que lean los overrides de variables de entorno al vuelo para cada test individual. Adicionalmente, el fixture inicializa la estructura de directorios simulada del monorepo inyectando de forma automática archivos firma `angular.json` y `nest-cli.json` para simular la presencia de los frameworks esperados. Las pruebas de segmentación validan activamente que el Hierarchical AST Chunker conserve las declaraciones de importación y firmas de clases correspondientes en cada fragmento de método de TypeScript.

Las pruebas se agrupan en **4 Tiers**:
- **Tier 1 (Feature Coverage)**: Cobertura básica de cada característica del sistema (5 casos por característica).
- **Tier 2 (Boundary & Corner Cases)**: Casos de límite y de esquina para validar resiliencia (5 casos por característica).
- **Tier 3 (Cross-Feature Combinations)**: Pruebas combinadas que cruzan múltiples características.
- **Tier 4 (Real-World Application Scenarios)**: Escenarios reales de flujo de desarrollo en monorepo.

---

## 3. Inventario de Caracteristicas y Casos de Prueba (74 Casos)

### Característica 1: Escaneo y Filtrado (F1)
- **F1-01**: `test_f1_scan_basic_ts_prisma_files` (Pasa)
- **F1-02**: `test_f1_scan_respects_allowed_extensions` (Pasa)
- **F1-03**: `test_f1_scan_ignores_forbidden_directories` (Pasa)
- **F1-04**: `test_f1_scan_empty_repo_directory` (Pasa)
- **F1-05**: `test_f1_scan_non_existent_scan_dirs` (Pasa)
- **F1-06**: `test_f1_boundary_extremely_long_file_path` (Pasa)
- **F1-07**: `test_f1_boundary_file_with_multiple_extensions` (Pasa)
- **F1-08**: `test_f1_boundary_scan_dir_with_mixed_casing` (Pasa)
- **F1-09**: `test_f1_boundary_files_with_dot_prefixes` (Pasa)
- **F1-10**: `test_f1_boundary_symlink_directories` (Pasa)

### Característica 2: Chunking Inteligente (F2)
- **F2-01**: `test_f2_chunking_ts_respects_functions` (Pasa)
- **F2-02**: `test_f2_chunking_prisma_respects_models` (Pasa)
- **F2-03**: `test_f2_chunking_html_respects_tags` (Pasa)
- **F2-04**: `test_f2_chunking_fallback_linear` (Pasa)
- **F2-05**: `test_f2_chunking_single_line_file` (Pasa)
- **F2-06**: `test_f2_boundary_extremely_large_file_lines` (Pasa)
- **F2-07**: `test_f2_boundary_empty_file_chunking` (Pasa)
- **F2-08**: `test_f2_boundary_unicode_and_emojis_in_file` (Pasa)
- **F2-09**: `test_f2_boundary_lines_without_newline_at_eof` (Pasa)
- **F2-10**: `test_f2_boundary_only_comments_and_whitespace` (Pasa)

### Característica 3: Extracción de Metadatos Ricos (F3)
- **F3-01**: `test_f3_metadata_presence_in_chunk_file_output` (Pasa)
- **F3-02**: `test_f3_ts_metadata_class_extraction` (Pasa)
- **F3-03**: `test_f3_ts_metadata_method_extraction` (Pasa)
- **F3-04**: `test_f3_ts_metadata_imports_extraction` (Pasa)
- **F3-05**: `test_f3_prisma_metadata_model_extraction` (Pasa)
- **F3-06**: `test_f3_boundary_invalid_syntax_ts_file` (Pasa)
- **F3-07**: `test_f3_boundary_nested_class_metadata_extraction` (Pasa)
- **F3-08**: `test_f3_boundary_prisma_unsupported_blocks` (Pasa)
- **F3-09**: `test_f3_boundary_html_nested_custom_tags` (Pasa)
- **F3-10**: `test_f3_boundary_extremely_large_metadata_payload` (Pasa)

### Característica 4: Ingesta Incremental y Caché (F4)
- **F4-01**: `test_f4_incremental_no_changes_unchanged_count` (Pasa)
- **F4-02**: `test_f4_incremental_new_file_indexing` (Pasa)
- **F4-03**: `test_f4_incremental_modified_file_reindexing` (Pasa)
- **F4-04**: `test_f4_incremental_deleted_file_cleanup` (Pasa)
- **F4-05**: `test_f4_incremental_corrupted_cache_resilience` (Pasa)
- **F4-06**: `test_f4_boundary_reverted_file_modification` (Pasa)
- **F4-07**: `test_f4_boundary_cache_file_deleted_externally` (Pasa)
- **F4-08**: `test_f4_boundary_db_deleted_but_cache_persists_resync` (Pasa)
- **F4-09**: `test_f4_boundary_unreadable_file_error_handling` (Pasa)
- **F4-10**: `test_f4_boundary_duplicate_file_paths_in_cache` (Pasa)

### Característica 5: Consulta Semántica y Ámbito (F5)
- **F5-01**: `test_f5_query_scope_filtering_frontend` (Pasa)
- **F5-02**: `test_f5_query_scope_filtering_backend` (Pasa)
- **F5-03**: `test_f5_query_no_scope_returns_all` (Pasa)
- **F5-04**: `test_f5_query_k_limit_respected` (Pasa)
- **F5-05**: `test_f5_query_with_no_chunks_in_db` (Pasa)
- **F5-06**: `test_f5_boundary_query_extremely_long_text` (Pasa)
- **F5-07**: `test_f5_boundary_query_empty_string_error` (Pasa)
- **F5-08**: `test_f5_boundary_query_special_characters_search` (Pasa)
- **F5-09**: `test_f5_boundary_query_non_existent_scope_filter` (Pasa)
- **F5-10**: `test_f5_boundary_query_k_value_out_of_bounds` (Pasa)

### Característica 6: Fusión de Fragmentos y Prompt XML (F6)
- **F6-01**: `test_f6_chunk_fusion_adjacent_blocks` (Pasa)
- **F6-02**: `test_f6_chunk_fusion_non_adjacent_blocks` (Pasa)
- **F6-03**: `test_f6_prompt_xml_structure` (Pasa)
- **F6-04**: `test_f6_prompt_xml_special_characters_escaping` (Pasa)
- **F6-05**: `test_f6_response_includes_xml_tags` (Pasa)
- **F6-06**: `test_f6_boundary_fusion_overlapping_chunks` (Pasa)
- **F6-07**: `test_f6_boundary_fusion_contained_chunks` (Pasa)
- **F6-08**: `test_f6_boundary_fusion_max_context_limit_respected` (Pasa)
- **F6-09**: `test_f6_boundary_xml_file_content_injection_defense` (Pasa)
- **F6-10**: `test_f6_boundary_xml_tags_in_user_query_handling` (Pasa)

### Combinaciones Cruzadas (Tier 3)
- **F7-01**: `test_f7_cross_scan_and_incremental_flow` (Pasa)
- **F7-02**: `test_f7_cross_chunk_metadata_index_query` (Pasa)
- **F7-03**: `test_f7_cross_incremental_and_chunk_fusion` (Pasa)
- **F7-04**: `test_f7_cross_scope_query_and_xml_generation` (Pasa)
- **F7-05**: `test_f7_cross_corrupted_cache_and_db_restore` (Pasa)
- **F7-06**: `test_f7_cross_incremental_revert_and_fusion` (Pasa)

### Escenarios de Aplicación Real (Tier 4)
- **F8-01**: `test_f8_scenario_bootstrap_and_initial_query` (Pasa)
- **F8-02**: `test_f8_scenario_monorepo_update_cycle` (Pasa)
- **F8-03**: `test_f8_scenario_frontend_refactor_verification` (Pasa)
- **F8-04**: `test_f8_scenario_backend_schema_evolution` (Pasa)
- **F8-05**: `test_f8_scenario_adversarial_queries_and_recovery` (Pasa)

### Característica 10: Compresión de Contenido (F10)
- **F10**: `test_f10_compression.py` (Pasa)

### Característica 11: Ámbitos de Escáner (F11)
- **F11**: `test_f11_scanner_scopes.py` (Pasa)

### Cobertura MCP (`tests/unit/mcp/` & `tests/integration/mcp/`)
- Pruebas unitarias para herramientas MCP (`config`, `graph`, `ingest`, `metrics`, `project_map`, `query`, `styles`).
- Pruebas de integración para registro e invocación del servidor FastMCP.

### Cobertura CLI (`tests/unit/cli/` & `tests/integration/cli/`)
- Pruebas unitarias e integración para puntos de entrada CLI (`rag-graph`, `rag-ingest`, `rag-loc`, `rag-project-map`, `rag-query`, `rag-styles`).

---

## 4. Estado de Cobertura (TDD Baseline)

El 100% de las características principales del RAG, incluyendo el chunking sintáctico inteligente, extracción de metadatos ricos, ingesta incremental con LanceDB, herramientas MCP y comandos CLI, están completamente cubiertas.

> [!NOTE]
> La suite completa de pruebas consta de **186 casos de prueba automatizados** (todos pasados), validando el correcto funcionamiento de E2E, MCP y CLI.

