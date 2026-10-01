(function () {
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    const mountedHosts = new Set();

    function releaseGraph(host) {
        const state = host.__utopiaGraph3d;
        if (!state || !state.graph) return;
        state.releasing = true;
        state.observer?.disconnect();
        const canvas = state.graph.renderer()?.domElement;
        if (canvas && state.contextLostHandler) {
            canvas.removeEventListener("webglcontextlost", state.contextLostHandler);
            state.contextLostHandler = null;
        }
        state.graph.pauseAnimation();
        state.graph.controls()?.dispose?.();
        state.graph.scene()?.traverse(object => {
            object.geometry?.dispose?.();
            const materials = Array.isArray(object.material) ? object.material : [object.material];
            materials.filter(Boolean).forEach(material => material.dispose?.());
        });
        state.graph.renderer()?.dispose?.();
        state.graph.renderer()?.forceContextLoss?.();
        state.graph = null;
        mountedHosts.delete(host);
    }

    const lifecycleObserver = new MutationObserver(() => {
        for (const host of mountedHosts) {
            if (!host.isConnected) releaseGraph(host);
        }
    });
    lifecycleObserver.observe(document.documentElement, {childList: true, subtree: true});

    function spherePositions(nodes) {
        const ordered = [...nodes].sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
        const radius = Math.max(100, ordered.length * 12);
        return new Map(ordered.map((node, index) => {
            const y = 1 - 2 * (index + 0.5) / ordered.length;
            const ring = Math.sqrt(Math.max(0, 1 - y * y));
            const angle = goldenAngle * index;
            return [node.id, {
                x: Math.cos(angle) * ring * radius,
                y: y * radius,
                z: Math.sin(angle) * ring * radius
            }];
        }));
    }

    function dispatchSelection(kind, data) {
        if (!window.dash_clientside || !window.dash_clientside.set_props) return;
        window.dash_clientside.set_props("graph-3d-event", {
            data: {kind: kind, id: data.id, nonce: Date.now()}
        });
    }

    function colorForNode(node) {
        return ({Person: "#167568", Company: "#a83f58", Project: "#5166ae"})[node.type] || "#566075";
    }

    function textLabel(value) {
        const label = document.createElement("span");
        label.textContent = String(value || "");
        return label;
    }

    function dataForGraph(elements, mode, positionCache) {
        const nodes = (elements || [])
            .filter(element => element.data && !("source" in element.data))
            .map(element => ({...element.data, id: String(element.data.id)}));
        const nodeIds = new Set(nodes.map(node => node.id));
        const positions = mode === "sphere" ? spherePositions(nodes) : null;
        for (const node of nodes) {
            const position = positions && positions.get(node.id);
            const cached = positionCache.get(node.id);
            if (position) Object.assign(node, position, {fx: position.x, fy: position.y, fz: position.z});
            else if (cached) Object.assign(node, cached, {fx: undefined, fy: undefined, fz: undefined});
        }
        const links = (elements || [])
            .filter(element => element.data && "source" in element.data)
            .filter(element => nodeIds.has(String(element.data.source)) && nodeIds.has(String(element.data.target)))
            .map(element => ({...element.data, id: String(element.data.id), source: String(element.data.source), target: String(element.data.target)}));
        return {nodes, links};
    }

    function rememberPositions(graph, cache) {
        if (!graph) return;
        const data = graph.graphData();
        for (const node of data.nodes || []) {
            if ([node.x, node.y, node.z].every(Number.isFinite)) {
                cache.set(String(node.id), {x: node.x, y: node.y, z: node.z});
            }
        }
    }

    function updateGraph(elements, active, mode) {
        const host = document.getElementById("graph-3d-view");
        if (!host) return "";
        const state = host.__utopiaGraph3d || (host.__utopiaGraph3d = {graph: null, positions: new Map(), observer: null, fitOnStop: false, releasing: false});
        if (!active) {
            if (state.graph) state.graph.pauseAnimation();
            return "";
        }
        if (!window.ForceGraph3D) {
            window.dash_clientside.set_props("graph-render-error", {data: "三维资源未加载，已切回二维。"});
            window.dash_clientside.set_props("graph-render-mode", {value: "2d"});
            return "";
        }
        try {
            if (!state.graph) {
                state.graph = window.ForceGraph3D()(host, {controlType: "orbit", rendererConfig: {antialias: true, alpha: true}})
                    .backgroundColor("#fafbfc")
                    .showNavInfo(false)
                    .nodeLabel(node => textLabel(`${node.name} (${node.type})`))
                    .nodeColor(colorForNode)
                    .nodeRelSize(5)
                    .linkLabel(link => textLabel(link.label))
                    .linkColor(link => link.derived ? "#ab702b" : "#89959e")
                    .linkOpacity(0.72)
                    .linkDirectionalArrowLength(3)
                    .linkDirectionalArrowRelPos(0.96)
                    .linkCurvature(link => link.source === link.target ? 0.24 : 0)
                    .cooldownTime(3200)
                    .onNodeClick(node => dispatchSelection("node", node))
                    .onLinkClick(link => dispatchSelection("edge", link))
                    .onEngineStop(() => {
                        if (state.fitOnStop) {
                            state.fitOnStop = false;
                            state.graph.zoomToFit(300, 42);
                        }
                    });
                state.graph.renderer().setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
                mountedHosts.add(host);
                const canvas = host.querySelector("canvas");
                if (canvas) {
                    canvas.setAttribute("aria-label", "可旋转的三维知识图谱");
                    state.contextLostHandler = event => {
                        if (state.releasing || !host.isConnected) return;
                        event.preventDefault();
                        window.dash_clientside.set_props("graph-render-error", {data: "WebGL 已中断，已切回二维图谱。"});
                        window.dash_clientside.set_props("graph-render-mode", {value: "2d"});
                    };
                    canvas.addEventListener("webglcontextlost", state.contextLostHandler);
                }
                state.observer = new ResizeObserver(() => {
                    const width = host.clientWidth;
                    const height = host.clientHeight;
                    if (width && height) state.graph.width(width).height(height);
                });
                state.observer.observe(host);
            }
            state.graph.resumeAnimation();
            if (mode === "force") rememberPositions(state.graph, state.positions);
            const data = dataForGraph(elements, mode, state.positions);
            state.fitOnStop = true;
            state.graph.graphData(data).width(host.clientWidth).height(host.clientHeight);
            applySelection(state);
            if (mode === "sphere") state.graph.zoomToFit(300, 42);
            return `${data.nodes.length} 个实体 · ${data.links.length} 条关系`;
        } catch (error) {
            window.dash_clientside.set_props("graph-render-error", {data: "当前设备无法启动 WebGL，已切回二维图谱。"});
            window.dash_clientside.set_props("graph-render-mode", {value: "2d"});
            return `三维图谱不可用：${error.message}`;
        }
    }

    function applySelection(state) {
        if (!state || !state.graph) return;
        state.graph
            .nodeVal(node => state.selection && state.selection.kind === "node" && node.id === state.selection.id ? 2.2 : 1)
            .linkWidth(link => state.selection && state.selection.kind === "edge" && link.id === state.selection.id ? 1.8 : 0)
            .refresh();
    }

    function setSelection(selection) {
        const host = document.getElementById("graph-3d-view");
        const state = host && host.__utopiaGraph3d;
        if (!state) return;
        state.selection = selection || null;
        applySelection(state);
    }

    function setTheme(theme) {
        const host = document.getElementById("graph-3d-view");
        const state = host && host.__utopiaGraph3d;
        if (!state || !state.graph) return;
        const dark = theme === "dark";
        state.graph
            .backgroundColor(dark ? "#1a2027" : "#fafbfc")
            .linkColor(link => link.derived ? "#d5a34e" : (dark ? "#8793a0" : "#89959e"))
            .refresh();
    }

    function exportPng() {
        const host = document.getElementById("graph-3d-view");
        const state = host && host.__utopiaGraph3d;
        if (!state || !state.graph) return false;
        try {
            const graph = state.graph;
            const renderer = graph.renderer();
            renderer.render(graph.scene(), graph.camera());
            const link = document.createElement("a");
            link.href = renderer.domElement.toDataURL("image/png");
            link.download = "utopia-graph-3d-current-view.png";
            document.body.appendChild(link);
            link.click();
            setTimeout(() => link.remove(), 1000);
            return true;
        } catch (error) {
            const status = document.getElementById("graph-3d-status");
            if (status) status.textContent = "无法导出当前三维视角，请切回二维后重试。";
            return false;
        }
    }

    document.addEventListener("click", event => {
        const target = event.target instanceof Element ? event.target.closest("#graph-download") : null;
        const mode = document.querySelector('#graph-render-mode input:checked')?.value;
        if (target && mode === "3d") exportPng();
    }, true);

    window.utopiaGraph3d = {updateGraph: updateGraph, setSelection: setSelection, setTheme: setTheme, exportPng: exportPng};
})();
