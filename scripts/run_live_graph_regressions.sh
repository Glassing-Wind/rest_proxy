#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${LM_PROXY_PYTHON:-${LM_PROXY_INDEX_PYTHON:-}}"
if [[ -z "$PYTHON_BIN" ]] && [[ -x "/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python" ]]; then
  PYTHON_BIN="/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

WORKSPACES=(
  "/Users/michaelmarler/Projects/FrameCreator"
  "/Users/michaelmarler/Projects/sample-food-truck"
  "/Users/michaelmarler/Projects/Fruta-upstream"
  "/Users/michaelmarler/Projects/LoomBackgroundMusic"
  "/Users/michaelmarler/Projects/rest_proxy"
  "/Users/michaelmarler/Projects/pydantic-ai"
  "/Users/michaelmarler/draw-things-community"
  "/Users/michaelmarler/Projects/uv"
  "/Users/michaelmarler/Projects/axum"
  "/Users/michaelmarler/Projects/zod-upstream"
  "/Users/michaelmarler/Projects/gin-upstream"
  "/Users/michaelmarler/Projects/spring-petclinic-upstream"
  "/Users/michaelmarler/Projects/okhttp-upstream"
  "/Users/michaelmarler/Projects/swift-nio"
  "/Users/michaelmarler/Projects/tree-sitter-language-pack"
)

AVAILABLE_WORKSPACES=()
for workspace in "${WORKSPACES[@]}"; do
  if [[ -d "$workspace" ]]; then
    AVAILABLE_WORKSPACES+=("$workspace")
  else
    echo "[live-graph-regressions] skipping missing workspace: $workspace"
  fi
done

if [[ ${#AVAILABLE_WORKSPACES[@]} -eq 0 ]]; then
  echo "[live-graph-regressions] no workspaces available"
  exit 1
fi

CASES=(
  "framecreator_sidebar_symbol_context"
  "framecreator_views_directory_snapshot"
  "framecreator_flow_summary"
  "framecreator_apple_build_summary"
  "sample_food_truck_apple_build_summary"
  "sample_food_truck_project_related_files"
  "sample_food_truck_project_orientation_workflow"
  "fruta_apple_build_summary"
  "loombackgroundmusic_apple_build_summary"
  "loombackgroundmusic_workspace_related_files"
  "loombackgroundmusic_workspace_directory_snapshot"
  "loombackgroundmusic_apple_investigation_workflow"
  "loombackgroundmusic_workspace_context_workflow"
  "rest_proxy_repo_dependency_summary"
  "rest_proxy_backend_flow_summary"
  "rest_proxy_backend_context_workflow"
  "framecreator_sidebar_investigation_workflow"
  "framecreator_sidebar_context_workflow"
  "framecreator_sidebar_subgraph_explain_workflow"
  "pydantic_ai_model_inference_selected_search"
  "pydantic_ai_provider_wiring_search"
  "pydantic_ai_tool_results_to_messages_search"
  "pydantic_ai_models_directory_snapshot"
  "pydantic_ai_code_importance"
  "pydantic_ai_provider_exports_summary"
  "pydantic_ai_symbol_imports_overview"
  "pydantic_ai_provider_related_files"
  "pydantic_ai_provider_subgraph"
  "pydantic_ai_provider_query_graph"
  "pydantic_ai_provider_provenance"
  "pydantic_ai_provider_investigation_workflow"
  "pydantic_ai_provider_adjacency_workflow"
  "pydantic_ai_provider_graph_debug_workflow"
  "pydantic_ai_provider_provenance_context_workflow"
  "pydantic_ai_provider_import_surface_workflow"
  "pydantic_ai_models_orientation_workflow"
  "drawthings_model_inference_selected_search"
  "drawthings_grpc_request_routing_search"
  "drawthings_grpc_server_directory_snapshot"
  "drawthings_grpc_investigation_workflow"
  "uv_command_enum_search"
  "uv_run_symbol_context"
  "uv_find_python_installation_symbol_context"
  "uv_project_lock_resolution_search"
  "uv_lock_commit_symbol_context"
  "uv_lock_investigation_workflow"
  "axum_route_definition_search"
  "axum_nest_symbol_context"
  "axum_serve_entrypoint_search"
  "axum_method_router_symbol_context"
  "axum_serve_investigation_workflow"
  "axum_routing_orientation_workflow"
  "zod_json_schema_conversion_search"
  "zod_discriminated_union_symbol_context"
  "zod_from_json_schema_symbol_context"
  "zod_object_core_symbol_context"
  "zod_json_schema_investigation_workflow"
  "gin_add_route_symbol_context"
  "gin_request_handling_search"
  "gin_add_route_references"
  "gin_handle_http_request_symbol_context"
  "gin_request_flow_workflow"
  "spring_petclinic_owner_request_workflow"
  "spring_petclinic_owner_controller_symbol_context"
  "spring_petclinic_process_find_form_symbol_context"
  "spring_petclinic_vet_controller_symbol_context"
  "okhttp_real_interceptor_chain_symbol_context"
  "okhttp_proceed_symbol_context"
  "okhttp_real_interceptor_chain_references"
  "okhttp_interceptor_context_workflow"
  "okhttp_interceptor_subgraph_explain_workflow"
  "okhttp_interceptor_chain_workflow"
  "sample_food_truck_widget_context_workflow"
  "swift_nio_code_importance"
  "swift_nio_bytebuffer_exports_summary"
  "swift_nio_core_orientation_workflow"
  "swift_nio_posix_orientation_workflow"
  "ts_pack_detect_language_cross_project"
  "spring_petclinic_owner_orientation_workflow"
  "sample_food_truck_store_orientation_workflow"
)

ARGS=(
  "--regressions-only"
  "--verbose-progress"
  "--fail-fast"
)

for case_id in "${CASES[@]}"; do
  ARGS+=("--case-id" "$case_id")
done

echo "[live-graph-regressions] python=$PYTHON_BIN"
echo "[live-graph-regressions] workspaces=${AVAILABLE_WORKSPACES[*]}"
echo "[live-graph-regressions] cases=${CASES[*]}"

cd "$ROOT_DIR"
"$PYTHON_BIN" "$ROOT_DIR/scripts/check_brain_server_freshness.py" --restart-if-stale --quiet
"$PYTHON_BIN" "$ROOT_DIR/test_live_graph_tools.py" "${AVAILABLE_WORKSPACES[@]}" "${ARGS[@]}" "$@"
