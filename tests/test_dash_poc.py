import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dash_app
from app import db, graph, local_markdown, ontology_documents, ontology_sharing
from seed import main as seed_demo
from ui.templates import COMMERCE_TEMPLATE


class DashPocCallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "poc.db"
        self.db_patcher = patch.object(db, "DB_PATH", str(self.database_path))
        self.db_patcher.start()
        seed_demo()
        self.client = dash_app.app.server.test_client()

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def invoke(self, marker, input_values, state_values, changed):
        key = next(key for key in dash_app.app.callback_map if marker in key)
        metadata = dash_app.app.callback_map[key]
        output_objects = metadata["output"]
        if not isinstance(output_objects, (list, tuple)):
            output_objects = [output_objects]
        outputs = [
            {"id": output.component_id, "property": output.component_property}
            for output in output_objects
        ]
        if len(outputs) == 1:
            outputs = outputs[0]
        inputs = [
            {
                "id": item["id"],
                "property": item["property"],
                "value": input_values.get((item["id"], item["property"])),
            }
            for item in metadata["inputs"]
        ]
        states = [
            {
                "id": item["id"],
                "property": item["property"],
                "value": state_values.get((item["id"], item["property"])),
            }
            for item in metadata.get("state", [])
        ]
        response = self.client.post(
            "/_dash-update-component",
            json={
                "output": key,
                "outputs": outputs,
                "inputs": inputs,
                "state": states,
                "changedPropIds": [changed],
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["response"]

    def test_graph_workbench_navigation_renders(self):
        layout = dash_app.workbench_layout().to_plotly_json()
        self.assertEqual(layout["props"]["className"], "app-shell studio-shell")
        for section in ("ontology-section", "mapping-section", "instance-section", "resolution-section"):
            self.assertIn(section, str(layout))
        self.assertEqual(dash_app.switch_workflow("instance-section"), [True, False, True, True, True, True])
        self.assertIn("workflow-mode", str(layout))

    def test_secondary_pages_share_branded_shell(self):
        for path, shell_class in (
            ("/catalogue", "page-shell-catalogue"),
            ("/designer", "page-shell-designer"),
            ("/learn", "page-shell-learn"),
            ("/share/not-a-valid-token", "page-shell-share"),
        ):
            serialized = str(dash_app.render_route(path))
            self.assertIn(shell_class, serialized)
            self.assertIn("知识工程工作台", serialized)
        shared_page = str(dash_app.render_route("/share/not-a-valid-token"))
        self.assertIn("page-link active", shared_page)

    def test_local_data_workspace_renders(self):
        route = self.invoke("route-content.children", {("route-location", "pathname"): "/data"}, {}, "route-location.pathname")
        serialized = json.dumps(route, ensure_ascii=False)
        for component_id in (
            "local-source-upload",
            "local-source-select",
            "local-source-table",
            "local-query-run",
            "local-query-sql-run",
            "local-map-table-save",
            "local-materialize-preview-button",
            "local-materialize-confirm",
            "local-markdown-upload",
            "local-markdown-load",
            "local-markdown-new",
            "local-markdown-save",
            "local-markdown-preview",
            "evidence-semantic-object",
            "evidence-source-type",
            "evidence-source",
            "evidence-add",
            "evidence-list",
            "agent-task-start",
            "agent-task-refresh",
            "agent-task-complete",
            "agent-task-delete",
            "agent-task-id",
            "agent-task-events",
        ):
            self.assertIn(component_id, serialized)
        self.assertIn("本地数据工作台", serialized)
        self.assertIn('"href": "/data"', serialized)

    def test_evidence_panel_lists_and_adds_references(self):
        from app import evidence

        semantic_id = "runtime_type:Person"
        conn = db.init_db()
        try:
            subject = graph.add_entity(conn, "Evidence subject", "Person")
            target = graph.add_entity(conn, "Evidence target", "Thing")
            fact_id = graph.add_fact(conn, subject, "supports", target, "2020-01-01", source="test source")
        finally:
            conn.close()

        empty = self.invoke(
            "evidence-list.children",
            {("evidence-add", "n_clicks"): 0, ("evidence-semantic-object", "value"): semantic_id},
            {
                ("evidence-source-type", "value"): "fact",
                ("evidence-source", "value"): str(fact_id),
                ("evidence-locator", "value"): "",
                ("evidence-label", "value"): "",
            },
            "evidence-semantic-object.value",
        )
        self.assertIn("尚无证据引用", json.dumps(empty, ensure_ascii=False))

        added = self.invoke(
            "evidence-list.children",
            {("evidence-add", "n_clicks"): 1, ("evidence-semantic-object", "value"): semantic_id},
            {
                ("evidence-source-type", "value"): "fact",
                ("evidence-source", "value"): str(fact_id),
                ("evidence-locator", "value"): "source field",
                ("evidence-label", "value"): "Supporting record",
            },
            "evidence-add.n_clicks",
        )
        self.assertIn("证据引用已记录", json.dumps(added, ensure_ascii=False))
        self.assertIn("current", json.dumps(added, ensure_ascii=False))
        self.assertIn("Supporting record", json.dumps(added, ensure_ascii=False))

    def test_agent_task_panel_starts_completes_and_deletes_trajectory(self):
        started = self.invoke(
            "agent-task-select.options",
            {
                ("agent-task-start", "n_clicks"): 1,
                ("agent-task-refresh", "n_clicks"): 0,
                ("agent-task-complete", "n_clicks"): 0,
                ("agent-task-delete", "n_clicks"): 0,
                ("agent-task-select", "value"): None,
            },
            {("agent-task-version", "value"): None, ("agent-task-id", "data"): ""},
            "agent-task-start.n_clicks",
        )
        task_id = started["agent-task-id"]["data"]
        self.assertTrue(task_id)
        self.assertIn("轨迹记录已开始", json.dumps(started, ensure_ascii=False))

        completed = self.invoke(
            "agent-task-select.options",
            {
                ("agent-task-start", "n_clicks"): 1,
                ("agent-task-refresh", "n_clicks"): 0,
                ("agent-task-complete", "n_clicks"): 1,
                ("agent-task-delete", "n_clicks"): 0,
                ("agent-task-select", "value"): task_id,
            },
            {("agent-task-version", "value"): None, ("agent-task-id", "data"): task_id},
            "agent-task-complete.n_clicks",
        )
        self.assertEqual(completed["agent-task-id"]["data"], "")
        self.assertIn("任务已完成", json.dumps(completed, ensure_ascii=False))

        deleted = self.invoke(
            "agent-task-select.options",
            {
                ("agent-task-start", "n_clicks"): 1,
                ("agent-task-refresh", "n_clicks"): 0,
                ("agent-task-complete", "n_clicks"): 1,
                ("agent-task-delete", "n_clicks"): 1,
                ("agent-task-select", "value"): task_id,
            },
            {("agent-task-version", "value"): None, ("agent-task-id", "data"): ""},
            "agent-task-delete.n_clicks",
        )
        self.assertIn("任务及其轨迹事件已删除", json.dumps(deleted, ensure_ascii=False))

    def test_markdown_selection_does_not_replace_editor_until_explicit_load(self):
        with patch("app.local_data.markdown_root", return_value=Path(self.temp_dir.name) / "markdown"):
            conn = db.init_db()
            try:
                saved = local_markdown.save_file(conn, "Saved note", "# Keep this draft")
            finally:
                conn.close()

            callback_key = next(key for key in dash_app.app.callback_map if "local-markdown-title.value" in key)
            callback_inputs = dash_app.app.callback_map[callback_key]["inputs"]
            self.assertEqual(
                [(item["id"], item["property"]) for item in callback_inputs],
                [("local-markdown-load", "n_clicks"), ("local-markdown-new", "n_clicks")],
            )

            loaded = self.invoke(
                "local-markdown-title.value",
                {("local-markdown-load", "n_clicks"): 1, ("local-markdown-new", "n_clicks"): 0},
                {("local-markdown-select", "value"): saved["file_id"]},
                "local-markdown-load.n_clicks",
            )
            self.assertEqual(loaded["local-markdown-title"]["value"], "Saved note")
            self.assertEqual(loaded["local-markdown-body"]["value"], "# Keep this draft\n")

            blank = self.invoke(
                "local-markdown-title.value",
                {("local-markdown-load", "n_clicks"): 1, ("local-markdown-new", "n_clicks"): 1},
                {("local-markdown-select", "value"): saved["file_id"]},
                "local-markdown-new.n_clicks",
            )
            self.assertEqual(blank["local-markdown-title"]["value"], "")
            self.assertEqual(blank["local-markdown-body"]["value"], "")
            self.assertIsNone(blank["local-markdown-select"]["value"])

    def test_remote_dash_data_callbacks_are_blocked_without_token(self):
        response = dash_app.app.server.test_client().post(
            "/_dash-update-component",
            json={"output": "local-source-status.children", "inputs": [{"id": "local-source-upload", "property": "contents", "value": ""}]},
            environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
        )
        self.assertEqual(response.status_code, 403)
        route_response = dash_app.app.server.test_client().post(
            "/_dash-update-component",
            json={"output": "route-content.children", "inputs": [{"id": "route-location", "property": "pathname", "value": "/data"}]},
            environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
        )
        self.assertEqual(route_response.status_code, 403)

    def test_designer_structured_modeling_groups(self):
        layout = str(dash_app.render_route("/designer"))
        for component_id in (
            "designer-model-mode",
            "designer-model-entity-panel",
            "designer-model-property-panel",
            "designer-model-relation-panel",
            "designer-entity-id",
            "designer-property-identifier",
            "designer-relation-cardinality",
            "designer-version-select",
            "designer-version-create",
            "designer-version-evaluate",
            "designer-version-accept",
            "designer-version-reject",
            "designer-version-activate",
        ):
            self.assertIn(component_id, layout)

        for mode, expected_hidden in (
            ("entity", (False, True, True)),
            ("property", (True, False, True)),
            ("relation", (True, True, False)),
        ):
            result = self.invoke(
                "designer-model-entity-panel.hidden",
                {("designer-model-mode", "value"): mode},
                {},
                "designer-model-mode.value",
            )
            self.assertEqual(
                (
                    result["designer-model-entity-panel"]["hidden"],
                    result["designer-model-property-panel"]["hidden"],
                    result["designer-model-relation-panel"]["hidden"],
                ),
                expected_hidden,
            )

    def test_ai_draft_entry_follows_history_controls_and_toggles_form(self):
        layout = dash_app.render_route("/designer")

        def component_with_id(component, target):
            if getattr(component, "id", None) == target or getattr(component, "className", None) == target:
                return component
            children = getattr(component, "children", None)
            for child in children if isinstance(children, list) else [children]:
                if child is not None:
                    found = component_with_id(child, target)
                    if found is not None:
                        return found
            return None

        actions = component_with_id(layout, "designer-suggestion-toggle")
        panel = component_with_id(layout, "designer-suggestion-panel")
        toolbar = component_with_id(layout, "button-row designer-actions")
        self.assertEqual([component.id for component in toolbar.children[:4]], [
            "designer-validate", "designer-undo", "designer-redo", "designer-suggestion-toggle",
        ])
        self.assertEqual(actions.to_plotly_json()["props"]["aria-expanded"], "false")
        self.assertTrue(panel.hidden)
        self.assertIn("designer-suggestion-provider", str(panel))

        opened = self.invoke(
            "designer-suggestion-panel.hidden",
            {("designer-suggestion-toggle", "n_clicks"): 1},
            {("designer-suggestion-panel", "hidden"): True},
            "designer-suggestion-toggle.n_clicks",
        )
        self.assertFalse(opened["designer-suggestion-panel"]["hidden"])
        self.assertEqual(opened["designer-suggestion-toggle"]["aria-expanded"], "true")
        closed = self.invoke(
            "designer-suggestion-panel.hidden",
            {("designer-suggestion-toggle", "n_clicks"): 2},
            {("designer-suggestion-panel", "hidden"): False},
            "designer-suggestion-toggle.n_clicks",
        )
        self.assertTrue(closed["designer-suggestion-panel"]["hidden"])
        self.assertEqual(closed["designer-suggestion-toggle"]["aria-expanded"], "false")
        self.assertIn("designer-suggestion-provider", str(panel))
        for provider, expected_hidden in (("local", True), ("remote", False)):
            result = self.invoke(
                "designer-suggestion-remote.hidden",
                {("designer-suggestion-provider", "value"): provider},
                {},
                "designer-suggestion-provider.value",
            )
            self.assertEqual(result["designer-suggestion-remote"]["hidden"], expected_hidden)

    def test_designer_candidate_evaluation_and_publish_flow(self):
        conn = db.init_db()
        try:
            document_id = ontology_documents.create_document(conn, COMMERCE_TEMPLATE)["id"]
        finally:
            conn.close()

        base_inputs = {
            ("designer-document-id", "data"): document_id,
            ("designer-version-create", "n_clicks"): 1,
            ("designer-version-evaluate", "n_clicks"): 0,
            ("designer-version-accept", "n_clicks"): 0,
            ("designer-version-reject", "n_clicks"): 0,
            ("designer-version-activate", "n_clicks"): 0,
        }
        created = self.invoke(
            "designer-version-select.options",
            base_inputs,
            {("designer-version-select", "value"): None, ("designer-version-reason", "value"): ""},
            "designer-version-create.n_clicks",
        )
        candidate_id = created["designer-version-select"]["value"]
        self.assertIn("Candidate 快照已创建", created["designer-version-status"]["children"])

        evaluated_inputs = {**base_inputs, ("designer-version-evaluate", "n_clicks"): 1}
        evaluated = self.invoke(
            "designer-version-select.options",
            evaluated_inputs,
            {("designer-version-select", "value"): candidate_id, ("designer-version-reason", "value"): "Reviewed"},
            "designer-version-evaluate.n_clicks",
        )
        self.assertIn("结构校验通过", json.dumps(evaluated["designer-version-status"], ensure_ascii=False))

        accepted_inputs = {**evaluated_inputs, ("designer-version-accept", "n_clicks"): 1}
        accepted = self.invoke(
            "designer-version-select.options",
            accepted_inputs,
            {("designer-version-select", "value"): candidate_id, ("designer-version-reason", "value"): "Reviewed"},
            "designer-version-accept.n_clicks",
        )
        self.assertIn("Candidate 已接受并发布", accepted["designer-version-status"]["children"])
        self.assertIn(candidate_id[:8], accepted["designer-version-active"]["children"])

    def test_legacy_mapping_button_cannot_bypass_snapshot_preview(self):
        layout = str(dash_app.workbench_layout())
        self.assertIn("className='mapping-group'", layout)
        self.assertIn("children='来源字段'", layout)
        self.assertIn("children='目标声明'", layout)
        self.assertIn("children='物化流程'", layout)
        result = self.invoke(
            "cm-status.children",
            {("cm-map-btn", "n_clicks"): 0, ("cm-materialize-btn", "n_clicks"): 1},
            {
                ("cm-mount", "value"): "hr",
                ("cm-table", "value"): "employees",
                ("cm-column", "value"): "dept",
                ("cm-target-type", "value"): "Department",
                ("cm-predicate", "value"): "works_in",
                ("cm-attr-name", "value"): "",
            },
            "cm-materialize-btn.n_clicks",
        )
        self.assertIn("快照必须先预览并确认", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("/data", json.dumps(result, ensure_ascii=False))
        conn = db.init_db()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM materialization_batches").fetchone()[0], 0)
        finally:
            conn.close()

    def test_designer_preview_layout_zoom_and_export_controls(self):
        layout = str(dash_app.render_route("/designer/template-commerce"))
        for component_id in (
            "designer-preview-layout",
            "designer-preview-zoom-in",
            "designer-preview-zoom-out",
            "designer-preview-fit",
            "designer-preview-download",
        ):
            self.assertIn(component_id, layout)
        self.assertIn("minZoom=0.2", layout)
        self.assertIn("maxZoom=3", layout)

        circle = self.invoke(
            "designer-preview.layout",
            {("designer-preview-layout", "value"): "circle"},
            {},
            "designer-preview-layout.value",
        )
        self.assertEqual(circle["designer-preview"]["layout"]["name"], "circle")
        self.assertTrue(circle["designer-preview"]["layout"]["fit"])

        invalid = self.invoke(
            "designer-preview.layout",
            {("designer-preview-layout", "value"): "not-a-layout"},
            {},
            "designer-preview-layout.value",
        )
        self.assertEqual(invalid["designer-preview"]["layout"]["name"], "cose")

        exported = self.invoke(
            "designer-preview.generateImage",
            {("designer-preview-download", "n_clicks"): 1},
            {},
            "designer-preview-download.n_clicks",
        )
        self.assertEqual(exported["designer-preview"]["generateImage"]["action"], "download")
        self.assertEqual(exported["designer-preview"]["generateImage"]["filename"], "ontology-preview")
        self.assertIn("designer-preview-viewport.data", dash_app.app.callback_map)

    def test_studio_views_selection_and_graph_tools(self):
        route = self.invoke("route-content.children", {("route-location", "pathname"): "/"}, {}, "route-location.pathname")
        self.assertIn("studio-workspace", json.dumps(route))
        self.assertIn('"id": "studio-workspace"', json.dumps(route))
        for component_id in ("graph-render-mode", "graph-2d-view", "graph-3d-view", "graph-3d-layout-mode", "graph-3d-event", "graph-selection", "graph-render-error", "graph-legend"):
            self.assertIn(component_id, json.dumps(route))
        self.assertIn("layout-grid.svg", json.dumps(route))
        self.assertIn("box-3d.svg", json.dumps(route))
        render_3d = self.invoke(
            "graph-2d-view.style",
            {("graph-render-mode", "value"): "3d"},
            {},
            "graph-render-mode.value",
        )
        self.assertEqual(render_3d["graph-2d-view"]["style"]["display"], "none")
        self.assertEqual(render_3d["graph-3d-view"]["style"]["display"], "flex")
        self.assertEqual(render_3d["graph-3d-layout-control"]["style"]["display"], "flex")
        self.assertEqual(render_3d["graph-download"]["title"], "导出当前三维视角 PNG")
        self.assertEqual(dash_app.show_graph_render_error("WebGL unavailable", "2d"), "WebGL unavailable")
        self.assertEqual(dash_app.show_graph_render_error("WebGL unavailable", "3d"), "")
        for mode in ("inspect", "instance-section", "resolution-section", "ontology-section", "mapping-section", "search"):
            self.assertEqual(dash_app.switch_workflow(mode).count(False), 1)
        self.assertEqual(dash_app.switch_instance_form("fact"), [True, False, True])
        self.assertEqual(dash_app.switch_resolution_form("axiom"), (True, False))
        elements = dash_app.build_elements("2023-06-01")
        node = next(element["data"] for element in elements if "entity_id" in element["data"])
        selected_3d = self.invoke(
            "graph-selection.data",
            {
                ("graph", "tapNodeData"): None,
                ("graph", "tapEdgeData"): None,
                ("graph-3d-event", "data"): {"kind": "node", "id": node["id"], "nonce": 1},
                ("graph", "elements"): elements,
                ("clear-inspector-btn", "n_clicks"): 0,
            },
            {("graph-selection", "data"): None},
            "graph-3d-event.data",
        )
        self.assertEqual(selected_3d["graph-selection"]["data"], {"kind": "node", "id": node["id"]})
        edge = next(element["data"] for element in elements if "source" in element["data"])
        selected_edge = self.invoke(
            "graph-selection.data",
            {
                ("graph", "tapNodeData"): None,
                ("graph", "tapEdgeData"): None,
                ("graph-3d-event", "data"): {"kind": "edge", "id": edge["id"], "nonce": 2},
                ("graph", "elements"): elements,
                ("clear-inspector-btn", "n_clicks"): 0,
            },
            {("graph-selection", "data"): {"kind": "node", "id": node["id"]}},
            "graph-3d-event.data",
        )
        self.assertEqual(selected_edge["graph-selection"]["data"], {"kind": "edge", "id": edge["id"]})
        edge_details = self.invoke(
            "inspector-content.children",
            {
                ("graph-selection", "data"): {"kind": "edge", "id": edge["id"]},
                ("graph", "elements"): elements,
                ("clear-inspector-btn", "n_clicks"): 0,
            },
            {},
            "graph-selection.data",
        )
        self.assertIn(f"事实 ID", json.dumps(edge_details, ensure_ascii=False))
        self.assertIn(str(edge["fact_id"]), json.dumps(edge_details, ensure_ascii=False))
        selected = self.invoke(
            "fact-subject-input.value",
            {("use-selected-entity", "n_clicks"): 1},
            {("graph-selection", "data"): {"kind": "node", "id": node["id"]}, ("graph", "elements"): elements},
            "use-selected-entity.n_clicks",
        )
        self.assertEqual(selected["fact-subject-input"]["value"], node["entity_id"])
        self.assertEqual(selected["workflow-mode"]["value"], "instance-section")
        self.assertEqual(selected["instance-mode"]["value"], "fact")
        self.assertEqual(dash_app.selection_label(node, []), ("未选择实体", True))
        self.assertIn("graph-viewport.data", dash_app.app.callback_map)
        self.assertIn("graph-3d-selection-sync.data", dash_app.app.callback_map)
        self.assertEqual(dash_app.switch_graph_renderer("2d")[4], "导出完整图谱 PNG")
        self.assertEqual(dash_app.graph_layout("circle")["name"], "circle")
        self.assertEqual(dash_app.graph_layout("invalid")["name"], "cose")
        self.assertEqual(dash_app.download_graph(1)["action"], "download")
        metrics = dash_app.graph_metrics(elements)
        self.assertEqual(int(metrics[0].children[0].children), sum("entity_id" in element["data"] for element in elements))

    def test_graph_workflow_panel_collapses_and_restores(self):
        collapsed = self.invoke(
            "workflow-collapsed.data",
            {("workflow-collapse", "n_clicks"): 1},
            {("workflow-collapsed", "data"): False},
            "workflow-collapse.n_clicks",
        )
        self.assertTrue(collapsed["workflow-collapsed"]["data"])
        hidden_panel = self.invoke(
            "studio-workspace.className",
            {("workflow-collapsed", "data"): True},
            {},
            "workflow-collapsed.data",
        )
        self.assertEqual(hidden_panel["studio-workspace"]["className"], "studio-workspace studio-workspace-collapsed")
        self.assertEqual(hidden_panel["workflow-collapse"]["aria-label"], "展开工作流")
        expanded_panel = self.invoke(
            "studio-workspace.className",
            {("workflow-collapsed", "data"): False},
            {},
            "workflow-collapsed.data",
        )
        self.assertEqual(expanded_panel["studio-workspace"]["className"], "studio-workspace")
        self.assertEqual(expanded_panel["workflow-collapse"]["aria-label"], "收起工作流")

    def test_global_theme_toggle_persists_and_updates_graph_labels(self):
        layout = str(dash_app.app.layout.to_plotly_json())
        self.assertIn("Store(id='theme-mode'", layout)
        self.assertEqual(dash_app.toggle_theme(1, "light"), "dark")
        self.assertEqual(dash_app.toggle_theme(2, "dark"), "light")
        self.assertIs(dash_app.toggle_theme(0, "dark"), dash_app.dash.no_update)
        self.assertEqual(dash_app.update_theme_toggle("dark")[0], "/assets/icons/sun.svg")
        self.assertEqual(dash_app.update_theme_toggle("light")[0], "/assets/icons/moon.svg")
        self.assertTrue(dash_app.update_theme_toggle(None)[3])
        self.assertFalse(dash_app.update_theme_toggle("dark")[3])
        self.assertIn("theme-mode.data", dash_app.app.callback_map)
        self.assertIn("theme-applied.data", dash_app.app.callback_map)
        self.assertIn("theme-graph-synced.data", dash_app.app.callback_map)

    def test_ontology_workflow_graph_preview_controls(self):
        layout = str(dash_app.render_route("/"))
        for component_id in (
            "ontology-graph-wrap",
            "ontology-layout-mode",
            "ontology-zoom-in",
            "ontology-zoom-out",
            "ontology-fit",
            "ontology-graph-maximize",
            "ontology-graph-viewport",
        ):
            self.assertIn(component_id, layout)
        elements = dash_app.build_ontology_elements()
        node_classes = {element["data"]["id"]: element.get("classes") for element in elements if "source" not in element["data"]}
        self.assertEqual(node_classes["Thing"], "root")
        self.assertEqual(node_classes["Agent"], "level-one")
        self.assertEqual(node_classes["Person"], "level-deep")
        self.assertIn("10 个类型", dash_app.ontology_graph_metrics(elements))
        self.assertIn("9 条子类关系", dash_app.ontology_graph_metrics(elements))
        self.assertEqual(dash_app.switch_ontology_operations("types"), (False, True, True))
        self.assertEqual(dash_app.switch_ontology_operations("hierarchy"), (True, False, True))
        self.assertEqual(dash_app.switch_ontology_operations("advanced"), (True, True, False))
        self.assertEqual(dash_app.switch_ontology_operations("hierarchy"), (True, False, True))
        self.assertEqual(dash_app.switch_ontology_operations("advanced"), (True, True, False))
        self.assertTrue(dash_app.toggle_ontology_graph(1, False))
        self.assertFalse(dash_app.toggle_ontology_graph(2, True))
        self.assertTrue(any("ontology-graph-wrap.className" in key for key in dash_app.app.callback_map))
        expanded = dash_app.render_ontology_graph(True)
        self.assertIn("ontology-graph-panel-expanded", expanded[0])
        self.assertEqual(expanded[1], "/assets/icons/minimize-2.svg")
        restored = dash_app.render_ontology_graph(False)
        self.assertEqual(restored[0], "ontology-graph-panel")
        self.assertEqual(restored[1], "/assets/icons/maximize.svg")

    def test_designer_save_rdf_export_share_and_copy(self):
        raw_document = json.dumps(COMMERCE_TEMPLATE, ensure_ascii=False)
        saved = self.invoke(
            "designer-status.children",
            {
                ("designer-validate", "n_clicks"): 0,
                ("designer-save", "n_clicks"): 1,
            },
            {
                ("designer-json", "value"): raw_document,
                ("designer-document-id", "data"): None,
            },
            "designer-save.n_clicks",
        )
        document_id = saved["designer-document-id"]["data"]
        self.assertTrue(document_id)
        self.assertIn("已保存", json.dumps(saved, ensure_ascii=False))

        exported = self.invoke(
            "designer-rdf-download.data",
            {("designer-rdf-export", "n_clicks"): 1},
            {("designer-json", "value"): raw_document},
            "designer-rdf-export.n_clicks",
        )
        self.assertIn("<owl:Class", exported["designer-rdf-download"]["data"]["content"])

        rdf_xml = (
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            'xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#" '
            'xmlns:owl="http://www.w3.org/2002/07/owl#">'
            '<owl:Ontology rdf:about="https://example.org/import"><rdfs:label>Imported</rdfs:label></owl:Ontology>'
            '<owl:Class rdf:about="https://example.org/import#Widget"><rdfs:label>Widget</rdfs:label></owl:Class>'
            '</rdf:RDF>'
        )
        imported = self.invoke(
            "designer-rdf-status.children",
            {
                ("designer-rdf-upload", "contents"): "data:application/rdf+xml;base64,"
                + base64.b64encode(rdf_xml.encode("utf-8")).decode("ascii"),
            },
            {("designer-rdf-upload", "filename"): "widget.rdf"},
            "designer-rdf-upload.contents",
        )
        self.assertIn("Widget", imported["designer-json"]["value"])
        self.assertIsNone(imported["designer-document-id"]["data"])
        self.assertEqual(imported["designer-rdf-source-xml"]["data"], rdf_xml)
        imported_document = json.loads(imported["designer-json"]["value"])
        self.assertEqual(
            imported["designer-rdf-source-document"]["data"],
            imported_document,
        )
        saved_import = self.invoke(
            "designer-status.children",
            {
                ("designer-validate", "n_clicks"): 0,
                ("designer-save", "n_clicks"): 1,
            },
            {
                ("designer-json", "value"): json.dumps(imported_document),
                ("designer-document-id", "data"): None,
                ("designer-rdf-source-xml", "data"): rdf_xml,
                ("designer-rdf-source-document", "data"): imported_document,
            },
            "designer-save.n_clicks",
        )
        document_id = saved_import["designer-document-id"]["data"]
        conn = db.init_db()
        try:
            stored = ontology_documents.get_document(conn, document_id)
            self.assertEqual(stored["rdf_source_xml"], rdf_xml)
            self.assertEqual(stored["rdf_source_document"], imported_document)
        finally:
            conn.close()

        shared = self.invoke(
            "designer-share-url.value",
            {("designer-share", "n_clicks"): 1},
            {("designer-json", "value"): raw_document},
            "designer-share.n_clicks",
        )
        share_url = shared["designer-share-url"]["value"]
        token = share_url.rsplit("/", 1)[-1]
        self.assertEqual(ontology_sharing.decode_document(token), COMMERCE_TEMPLATE)

        copied = self.invoke(
            "route-location.pathname",
            {("share-copy", "n_clicks"): 1},
            {("shared-ontology", "data"): COMMERCE_TEMPLATE},
            "share-copy.n_clicks",
        )
        destination = copied["route-location"]["pathname"]
        self.assertTrue(destination.startswith("/designer/"))

        conn = db.init_db()
        try:
            self.assertEqual(len(ontology_documents.list_documents(conn)), 3)
        finally:
            conn.close()

    def test_catalogue_filter_learning_quiz_and_graph_filter(self):
        catalogue = self.invoke(
            "catalogue-items.children",
            {("catalogue-search", "value"): "commerce"},
            {},
            "catalogue-search.value",
        )
        self.assertIn("Commerce", json.dumps(catalogue, ensure_ascii=False))
        self.assertNotIn("DeepLethe", json.dumps(catalogue, ensure_ascii=False))
        empty_catalogue = self.invoke(
            "catalogue-items.children",
            {("catalogue-search", "value"): "no matching ontology"},
            {},
            "catalogue-search.value",
        )
        self.assertIn("没有找到匹配的本体", json.dumps(empty_catalogue, ensure_ascii=False))

        learning = self.invoke(
            "learn-feedback.children",
            {("learn-check", "n_clicks"): 1},
            {
                ("learn-answer", "value"): "事实在现实世界中的结束时间",
                ("learn-article-slug", "data"): "two-clocks",
                ("learn-progress", "data"): [],
            },
            "learn-check.n_clicks",
        )
        self.assertIn("回答正确", json.dumps(learning, ensure_ascii=False))
        self.assertEqual(learning["learn-progress"]["data"], ["two-clocks"])

        graph_update = self.invoke(
            "instance-status.children",
            {
                ("asof", "value"): "2023-06-01",
                ("derive-btn", "n_clicks"): 0,
                ("cm-materialize-btn", "n_clicks"): 0,
                ("entity-type-filter", "value"): ["Person"],
                ("predicate-filter", "value"): [],
                ("add-entity-btn", "n_clicks"): 0,
                ("add-fact-btn", "n_clicks"): 0,
                ("correct-fact-btn", "n_clicks"): 0,
            },
            {},
            "entity-type-filter.value",
        )
        elements = graph_update["graph"]["elements"]
        nodes = [element["data"] for element in elements if "source" not in element["data"]]
        self.assertTrue(nodes)
        self.assertTrue(all(node["type"] == "Person" for node in nodes))

    def test_instance_workbench_creates_entities_facts_and_corrections(self):
        base_inputs = {
            ("asof", "value"): "2023-06-01",
            ("derive-btn", "n_clicks"): 0,
            ("cm-materialize-btn", "n_clicks"): 0,
            ("entity-type-filter", "value"): [],
            ("predicate-filter", "value"): [],
            ("add-entity-btn", "n_clicks"): 1,
            ("add-fact-btn", "n_clicks"): 0,
            ("correct-fact-btn", "n_clicks"): 0,
        }
        status = self.invoke(
            "instance-status.children",
            base_inputs,
            {
                ("entity-name-input", "value"): "POC Partner",
                ("entity-type-input", "value"): "Company",
            },
            "add-entity-btn.n_clicks",
        )
        self.assertIn("已新增实体", json.dumps(status, ensure_ascii=False))

        conn = db.init_db()
        try:
            partner_id = conn.execute("SELECT id FROM entities WHERE name = 'POC Partner'").fetchone()[0]
            alice_id = conn.execute("SELECT id FROM entities WHERE name = 'Alice'").fetchone()[0]
            old_fact_id = conn.execute(
                "SELECT f.id FROM facts f JOIN entities e ON e.id = f.subject_id "
                "WHERE e.name = 'Bob' AND f.predicate = 'works_at' AND f.status = 'active'"
            ).fetchone()[0]
        finally:
            conn.close()

        add_fact_inputs = {**base_inputs, ("add-entity-btn", "n_clicks"): 0, ("add-fact-btn", "n_clicks"): 1}
        fact_status = self.invoke(
            "instance-status.children",
            add_fact_inputs,
            {
                ("fact-subject-input", "value"): alice_id,
                ("fact-predicate-input", "value"): "partners_with",
                ("fact-object-input", "value"): partner_id,
                ("fact-valid-from-input", "value"): "2024-01-01",
                ("fact-valid-to-input", "value"): None,
            },
            "add-fact-btn.n_clicks",
        )
        self.assertIn("已新增事实", json.dumps(fact_status, ensure_ascii=False))

        correction_inputs = {
            **base_inputs,
            ("add-entity-btn", "n_clicks"): 0,
            ("correct-fact-btn", "n_clicks"): 1,
        }
        correction_status = self.invoke(
            "instance-status.children",
            correction_inputs,
            {
                ("correct-fact-id-input", "value"): old_fact_id,
                ("correct-object-id-input", "value"): partner_id,
                ("correct-note-input", "value"): "POC correction",
            },
            "correct-fact-btn.n_clicks",
        )
        self.assertIn("已记录纠错", json.dumps(correction_status, ensure_ascii=False))

        conn = db.init_db()
        try:
            old_status = conn.execute("SELECT status FROM facts WHERE id = ?", (old_fact_id,)).fetchone()[0]
            correction_source = conn.execute(
                "SELECT source FROM facts WHERE subject_id = ? AND object_id = ? ORDER BY id DESC LIMIT 1",
                (conn.execute("SELECT id FROM entities WHERE name = 'Bob'").fetchone()[0], partner_id),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(old_status, "superseded")
        self.assertIn("POC correction", correction_source)

    def test_structured_designer_adds_types_properties_and_relationships(self):
        document = {"name": "POC schema", "description": "", "entityTypes": [], "relationships": []}

        def add_type(type_id, name, click):
            nonlocal document
            result = self.invoke(
                "designer-model-status.children",
                {
                    ("designer-add-entity", "n_clicks"): click,
                    ("designer-add-property", "n_clicks"): 0,
                    ("designer-add-relation", "n_clicks"): 0,
                },
                {
                    ("designer-json", "value"): json.dumps(document),
                    ("designer-entity-id", "value"): type_id,
                    ("designer-entity-name", "value"): name,
                },
                "designer-add-entity.n_clicks",
            )
            document = json.loads(result["designer-json"]["value"])

        add_type("customer", "Customer", 1)
        add_type("order", "Order", 2)
        property_result = self.invoke(
            "designer-model-status.children",
            {
                ("designer-add-entity", "n_clicks"): 2,
                ("designer-add-property", "n_clicks"): 1,
                ("designer-add-relation", "n_clicks"): 0,
            },
            {
                ("designer-json", "value"): json.dumps(document),
                ("designer-property-entity", "value"): "customer",
                ("designer-property-name", "value"): "email",
                ("designer-property-type", "value"): "string",
                ("designer-property-identifier", "value"): [],
            },
            "designer-add-property.n_clicks",
        )
        document = json.loads(property_result["designer-json"]["value"])
        relation_result = self.invoke(
            "designer-model-status.children",
            {
                ("designer-add-entity", "n_clicks"): 2,
                ("designer-add-property", "n_clicks"): 1,
                ("designer-add-relation", "n_clicks"): 1,
            },
            {
                ("designer-json", "value"): json.dumps(document),
                ("designer-relation-name", "value"): "places",
                ("designer-relation-from", "value"): "customer",
                ("designer-relation-to", "value"): "order",
                ("designer-relation-cardinality", "value"): "one-to-many",
            },
            "designer-add-relation.n_clicks",
        )
        document = json.loads(relation_result["designer-json"]["value"])
        self.assertEqual(len(document["entityTypes"]), 2)
        self.assertEqual(document["entityTypes"][0]["properties"][0]["name"], "email")
        self.assertEqual(document["relationships"][0]["name"], "places")

    def test_designer_undo_redo_tracks_raw_json_edits(self):
        previous = json.dumps({"name": "Before", "entityTypes": [], "relationships": []})
        current = json.dumps({"name": "After", "entityTypes": [], "relationships": []})
        history = {"past": [previous], "future": [], "current": current}

        undone = self.invoke(
            "designer-history.data",
            {
                ("designer-json", "value"): current,
                ("designer-undo", "n_clicks"): 1,
                ("designer-redo", "n_clicks"): 0,
            },
            {("designer-history", "data"): history},
            "designer-undo.n_clicks",
        )
        self.assertEqual(undone["designer-json"]["value"], previous)
        self.assertFalse(undone["designer-redo"]["disabled"])

        redone = self.invoke(
            "designer-history.data",
            {
                ("designer-json", "value"): previous,
                ("designer-undo", "n_clicks"): 1,
                ("designer-redo", "n_clicks"): 1,
            },
            {("designer-history", "data"): undone["designer-history"]["data"]},
            "designer-redo.n_clicks",
        )
        self.assertEqual(redone["designer-json"]["value"], current)

    def test_conflict_close_and_axiom_retract_write_audit_records(self):
        conn = db.init_db()
        try:
            entities = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM entities")}
            old_fact_id = conn.execute(
                "SELECT id FROM facts WHERE subject_id = ? AND predicate = 'capital_of' AND status = 'active'",
                (entities["中国"],),
            ).fetchone()[0]
            self_loop_id = graph.add_fact(
                conn,
                entities["上海"],
                "located_in",
                entities["上海"],
                "2024-01-01",
            )
        finally:
            conn.close()

        conflict_result = self.invoke(
            "conflict-status.children",
            {("detect-conflicts-btn", "n_clicks"): 0, ("resolve-conflicts-btn", "n_clicks"): 1},
            {
                ("conflict-subject-id", "value"): entities["中国"],
                ("conflict-predicate", "value"): "capital_of",
                ("conflict-object-id", "value"): entities["上海"],
                ("conflict-valid-from", "value"): "1949-10-01",
                ("conflict-valid-to", "value"): None,
                ("conflict-resolution", "value"): "close",
                ("conflict-note", "value"): "POC close decision",
            },
            "resolve-conflicts-btn.n_clicks",
        )
        self.assertIn("处置完成", json.dumps(conflict_result, ensure_ascii=False))

        axiom_result = self.invoke(
            "axiom-status.children",
            {("detect-axioms-btn", "n_clicks"): 0, ("resolve-axiom-btn", "n_clicks"): 1},
            {
                ("axiom-fact-id", "value"): self_loop_id,
                ("axiom-resolution", "value"): "retract",
                ("axiom-note", "value"): "POC self-loop correction",
            },
            "resolve-axiom-btn.n_clicks",
        )
        self.assertIn("公理处置已记录", json.dumps(axiom_result, ensure_ascii=False))

        refreshed = self.invoke(
            "instance-status.children",
            {("asof", "value"): "2024-06-01", ("axiom-status", "children"): "公理处置已记录"},
            {},
            "axiom-status.children",
        )
        visible_facts = [element["data"].get("fact_id") for element in refreshed["graph"]["elements"]]
        self.assertNotIn(self_loop_id, visible_facts)
        self.assertNotIn(old_fact_id, visible_facts)
        self.assertNotIn("instance-status", refreshed)

        conn = db.init_db()
        try:
            old_status = conn.execute("SELECT status FROM facts WHERE id = ?", (old_fact_id,)).fetchone()[0]
            loop_status = conn.execute("SELECT status FROM facts WHERE id = ?", (self_loop_id,)).fetchone()[0]
            conflict_note = conn.execute("SELECT note FROM conflicts ORDER BY id DESC LIMIT 1").fetchone()[0]
            axiom_note = conn.execute("SELECT note FROM axiom_violations ORDER BY id DESC LIMIT 1").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(old_status, "superseded")
        self.assertEqual(loop_status, "retracted")
        self.assertEqual(conflict_note, "POC close decision")
        self.assertEqual(axiom_note, "POC self-loop correction")


if __name__ == "__main__":
    unittest.main()
