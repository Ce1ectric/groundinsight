// groundinsight_gui/network_canvas.js
//
// The network canvas: Vue Flow drawing the DiagramDTO of the session.
//
// The component holds no project state and makes no decision about the
// network. It draws what the server sends (props `diagram`, `colours`,
// `selected`, `tool`) and reports what the user did as events -- `place`,
// `move`, `connect`, `fault`, `select`, `select_many`, `open`, `delete`,
// `layout`, `key`, `menu` -- which the Python side turns into commands of
// groundinsight.api.
// Even a dragged symbol keeps its old place until the session has applied the
// MoveNodes command and sent the new diagram back.
//
// Selection: a click selects one element; with Shift, Ctrl or Cmd it adds the
// element or takes it away. Shift and a drag on the pane draw a frame, and
// what it holds is selected (the buses in it and the branches between them).
// The selection lives on the server like everything else; `selected` is the
// list it sends back.
//
// Context menu: a right click opens the menu of the element under the pointer
// (of the group, if it belongs to a selection of several; of the pane
// otherwise). The entries and their texts come from the server (`menus`); a
// chosen entry goes back as the event `menu` with the element and the place.
//
// Texts: names, the values of a numerical colouring, fault and infeed marks
// and the tooltips come with `annotations`. Their size follows the zoom
// through the CSS variable --gi-s (see CANVAS_CSS): the canvas only sets the
// variable and two classes, so zooming a plan of a thousand buses re-renders
// no symbol.
//
// Coordinates: the diagram stores the centre of a symbol, Vue Flow the top
// left corner of a node; both conversions happen here, and positions are
// snapped to the diagram's raster before they are sent.

import { Background, Controls, Handle, MiniMap, VueFlow, injectStyles, loadELK, useVueFlow } from "groundinsight-vueflow";
import { markRaw } from "vue";

injectStyles();

const SYMBOL_SIZE = 22;
const BUSBAR_HEIGHT = 8;
const BUSBAR_DEFAULT_LENGTH = 100;
// Fit of the whole diagram, for the first draw, after a layout and for the fit
// button. Vue Flow's own fit knows the symbols only; the texts stand around
// them -- the name and value to the right, the fault mark to the upper left --
// so the fit is computed here, for the symbols plus this room for their texts
// (in pixels of the screen, the texts do not shrink with the plan). A short
// feeder would fill the screen at several-fold zoom, hence at most zoom 1.
const FIT_MARGIN = { left: 56, top: 40, right: 170, bottom: 28 };
const FIT_MAX_ZOOM = 1.0;
// Texts keep their size on screen below this zoom, up to this factor ...
const TEXT_FULL_ZOOM = 0.85;
const TEXT_MAX_SCALE = 2.6;
const TEXT_STEP_MS = 160;
// ... branch names are hidden below FAR, bus names and values below VERY_FAR
// (they would overlap: buses of a laid out plan are 160 px apart).
const FAR_ZOOM = 0.45;
const VERY_FAR_ZOOM = 0.3;
// Distance between the lines of parallel branches (same two buses).
const PARALLEL_GAP = 12;

function symbolSize(node) {
  if (node.symbol === "busbar") {
    return [node.length > 0 ? node.length : BUSBAR_DEFAULT_LENGTH, BUSBAR_HEIGHT];
  }
  return [SYMBOL_SIZE, SYMBOL_SIZE];
}

function snap(value, grid) {
  return grid > 0 ? Math.round(value / grid) * grid : value;
}

function centre(graphNode) {
  const position = graphNode.computedPosition || graphNode.position;
  return [position.x + graphNode.data.w / 2, position.y + graphNode.data.h / 2];
}

const BusNode = {
  name: "GiBusNode",
  components: { Handle },
  props: ["id", "data", "selected"],
  template: `
    <div :class="['gi-node', 'gi-' + data.symbol, { 'gi-sel': selected, 'gi-off': !data.active }]"
         :style="{ width: data.w + 'px', height: data.h + 'px', background: data.colour }"
         :data-bus="id" :title="data.note.tip || id">
      <Handle type="target" position="top" class="gi-handle" />
      <Handle type="source" position="top" class="gi-handle" />
      <span class="gi-label" :style="textStyle(-4)">
        <span v-if="data.has_fault" class="gi-flash">&#x26A1;<span class="gi-flash-name">{{ (data.note.faults || []).join(", ") }}</span></span>
        <span class="gi-name">{{ id }}<span v-if="data.note.value" class="gi-value">{{ data.note.value }}</span></span>
      </span>
      <span v-if="data.has_source" class="gi-infeed">&#x25BC;</span>
    </div>`,
  methods: {
    // The name, and after it the value of the colouring: right of the symbol
    // and above its middle (branches leave through the middle); the gap to
    // the symbol grows with the text. A fault at the bus stands with its
    // name on a line of its own above the bus name.
    textStyle(dy) {
      return {
        left: "calc(" + (this.data.w + this.data.label_offset[0]) + "px + 5px * var(--gi-s, 1))",
        top: (this.data.h / 2 + dy + this.data.label_offset[1]) + "px",
      };
    },
  },
};

const LineEdge = {
  name: "GiLineEdge",
  props: ["id", "data", "selected", "sourceNode", "targetNode"],
  computed: {
    points() {
      const a = centre(this.sourceNode);
      const b = centre(this.targetNode);
      const waypoints = this.data.waypoints || [];
      // Parallel branches run side by side; a routed branch keeps its route.
      if (this.data.shift && !waypoints.length) {
        // The normal of the pair, not of this branch: two branches drawn in
        // opposite directions between the same buses still move apart.
        const [p, q] = this.data.reversed ? [b, a] : [a, b];
        const length = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1;
        const nx = -(q[1] - p[1]) / length * this.data.shift;
        const ny = (q[0] - p[0]) / length * this.data.shift;
        return [[a[0] + nx, a[1] + ny], [b[0] + nx, b[1] + ny]];
      }
      return [a, ...waypoints, b];
    },
    path() {
      return this.points.map((p, i) => (i ? "L" : "M") + p[0] + " " + p[1]).join(" ");
    },
    // The label sits beside the middle segment, on the side away from the bus
    // labels (those are above and right of their symbol): below a horizontal
    // segment, left of a vertical one -- so neither covers the other.
    // The value of a colouring follows the name. The gap to the line grows
    // with the text (--gi-s).
    label() {
      const p = this.points;
      const a = p[Math.floor((p.length - 1) / 2)];
      const b = p[Math.ceil((p.length - 1) / 2)];
      const x = (a[0] + b[0]) / 2 + this.data.label_offset[0];
      const y = (a[1] + b[1]) / 2 + this.data.label_offset[1];
      const vertical = Math.abs(b[1] - a[1]) > Math.abs(b[0] - a[0]);
      const gap = (dx, dy) => ({ transform: "translate(calc(" + dx + "px * var(--gi-s, 1)), calc(" + dy + "px * var(--gi-s, 1)))" });
      return vertical
        ? { x, y, anchor: "end", baseline: "middle", style: gap(-7, 0) }
        : { x, y, anchor: "middle", baseline: "hanging", style: gap(0, 6) };
    },
  },
  template: `
    <g :class="['gi-edge', { 'gi-sel': selected, 'gi-off': !data.active, 'gi-noearth': !data.grounding_conductor }]"
       :data-branch="id">
      <title>{{ data.note.tip || id }}</title>
      <path :d="path" class="gi-edge-hit" />
      <path :d="path" class="gi-edge-line" :style="{ stroke: data.colour }" />
      <text :x="label.x" :y="label.y" :text-anchor="label.anchor" :dominant-baseline="label.baseline"
            :style="label.style" class="gi-edge-label">{{ id }}<tspan v-if="data.note.value" dx="0.45em"
            class="gi-edge-value">{{ data.note.value }}</tspan></text>
    </g>`,
};

export default {
  components: { VueFlow, MiniMap, Controls, Background },
  props: {
    diagram: Object,
    tool: { type: String, default: "select" },
    colours: Object,
    selected: { type: Array, default: () => [] },
    menus: { type: Object, default: () => ({}) },
    annotations: { type: Object, default: () => ({ nodes: {}, edges: {}, stale: false }) },
    hint: { type: String, default: "" },
    focus: { type: Object, default: null },
    flowId: String,
  },
  setup(props) {
    return { flow: useVueFlow(props.flowId) };
  },
  data() {
    return {
      nodeTypes: markRaw({ bus: markRaw(BusNode) }),
      edgeTypes: markRaw({ line: markRaw(LineEdge) }),
      fitted: false,
      multiKeys: ["Shift", "Meta", "Control"],
      active: false,
      clicked: null,
      pointer: null,
      menu: null,
      far: false,
      veryFar: false,
    };
  },
  mounted() {
    // The keys belong to the canvas when it was clicked last. Listening on
    // the window, not on the canvas element, keeps them working when a redraw
    // from the server has taken the focus away from the clicked node. Escape
    // works from anywhere on the page (the tool was picked in the palette,
    // outside the canvas); a text field, a dialog and a menu keep their keys.
    this.onPointer = (event) => {
      this.active = this.$el.contains(event.target);
      if (this.menu && !(event.target.closest && event.target.closest(".gi-menu"))) {
        this.menu = null;
      }
    };
    this.onWindowKey = (event) => {
      const target = event.target;
      const typing = target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable);
      if (typing || document.querySelector(".q-dialog, .q-menu")) {
        return;
      }
      if (this.menu) {
        this.menu = null;
        if (event.key === "Escape") {
          return;  // Escape closes the menu and nothing else
        }
      }
      if (this.active || event.key === "Escape") {
        this.onKey(event);
      }
    };
    // Where the pointer is over the plan (null outside it): where a paste goes.
    this.onPointerMove = (event) => {
      this.pointer = this.$el.contains(event.target) ? { x: event.clientX, y: event.clientY } : null;
    };
    window.addEventListener("pointerdown", this.onPointer, true);
    window.addEventListener("pointermove", this.onPointerMove, true);
    window.addEventListener("keydown", this.onWindowKey);
    this.stopZoomWatch = this.$watch(() => this.flow.viewport.value.zoom, this.onZoom, { immediate: true });
  },
  unmounted() {
    window.removeEventListener("pointerdown", this.onPointer, true);
    window.removeEventListener("pointermove", this.onPointerMove, true);
    window.removeEventListener("keydown", this.onWindowKey);
    this.stopZoomWatch();
    window.clearTimeout(this.textTimer);
  },
  watch: {
    // The server has answered a click: its selection replaces the stand-in.
    selected() {
      this.clicked = null;
    },
    focus(target) {
      if (target) {
        this.centreOn(target, 20);
      }
    },
  },
  computed: {
    grid() {
      return (this.diagram && this.diagram.grid_size) || 20;
    },
    nodes() {
      const colours = (this.colours && this.colours.nodes) || {};
      const notes = (this.annotations && this.annotations.nodes) || {};
      return ((this.diagram && this.diagram.nodes) || []).map((n) => {
        const [w, h] = symbolSize(n);
        return {
          id: n.name,
          type: "bus",
          position: { x: n.x - w / 2, y: n.y - h / 2 },
          data: { ...n, w, h, colour: colours[n.name] || "#2a78d6", note: notes[n.name] || {} },
          selected: this.isSelected("bus", n.name),
          draggable: this.tool === "select",
          connectable: this.tool === "branch",
        };
      });
    },
    edges() {
      const colours = (this.colours && this.colours.edges) || {};
      const notes = (this.annotations && this.annotations.edges) || {};
      const all = (this.diagram && this.diagram.edges) || [];
      // Branches between the same two buses, whichever way they were drawn.
      const pair = (e) => (e.from_bus < e.to_bus ? e.from_bus + "\u0000" + e.to_bus : e.to_bus + "\u0000" + e.from_bus);
      const groups = {};
      for (const e of all) {
        (groups[pair(e)] = groups[pair(e)] || []).push(e.name);
      }
      return all.map((e) => {
        const group = groups[pair(e)];
        return {
          id: e.name,
          type: "line",
          source: e.from_bus,
          target: e.to_bus,
          data: {
            ...e,
            colour: colours[e.name] || "#5b6470",
            note: notes[e.name] || {},
            shift: (group.indexOf(e.name) - (group.length - 1) / 2) * PARALLEL_GAP,
            reversed: e.from_bus > e.to_bus,
          },
          selected: this.isSelected("branch", e.name),
        };
      });
    },
  },
  methods: {
    onZoom(zoom) {
      const element = this.$el;
      if (!element || !element.style) {
        return;
      }
      // The size of the texts follows a zoom gesture in steps, not with every
      // wheel event: each step restyles every text of the plan, and a plan of
      // a thousand buses would lose frames. In between the texts simply zoom
      // with the plan.
      this.textZoom = zoom;
      if (!this.textTimer) {
        this.applyTextScale();
        this.textTimer = window.setTimeout(() => {
          this.textTimer = null;
          this.applyTextScale();
        }, TEXT_STEP_MS);
      }
      // Reactive, but changing only when a threshold is crossed: the class
      // binding of the root must know them, or its next patch would drop them.
      this.far = zoom < FAR_ZOOM;
      this.veryFar = zoom < VERY_FAR_ZOOM;
    },
    applyTextScale() {
      const zoom = this.textZoom;
      if (!this.$el || !this.$el.style || !zoom) {
        return;
      }
      const scale = zoom >= TEXT_FULL_ZOOM ? 1 : Math.min(TEXT_FULL_ZOOM / zoom, TEXT_MAX_SCALE);
      const text = scale.toFixed(2);
      if (this.$el.style.getPropertyValue("--gi-s") !== text) {
        this.$el.style.setProperty("--gi-s", text);
      }
    },
    isSelected(kind, name) {
      return (this.selected || []).some((s) => s.kind === kind && s.name === name);
    },
    onDragStop(event) {
      // A selected symbol drags the whole selection along; all of them move in one command.
      const positions = {};
      for (const node of event.nodes) {
        const [x, y] = centre(node);
        positions[node.id] = [snap(x, this.grid), snap(y, this.grid)];
      }
      this.$emit("move", { positions });
    },
    onPaneClick(event) {
      this.clicked = null;
      if (this.tool === "bus" || this.tool === "source_bus") {
        const p = this.flow.screenToFlowCoordinate({ x: event.clientX, y: event.clientY });
        this.$emit("place", { x: snap(p.x, this.grid), y: snap(p.y, this.grid), symbol: this.tool });
      } else if (this.tool === "select") {
        this.$emit("select", { kind: null, name: null });
      }
    },
    onNodeClick({ event, node }) {
      if (this.tool === "fault") {
        this.$emit("fault", { bus: node.id });
      } else if (this.tool === "select") {
        this.pick("bus", node.id, event);
      }
    },
    onEdgeClick({ event, edge }) {
      if (this.tool === "select") {
        this.pick("branch", edge.id, event);
      }
    },
    pick(kind, name, event) {
      const add = !!event && (event.shiftKey || event.metaKey || event.ctrlKey);
      // `clicked` stands in for the selection until the server has answered,
      // so that Delete works at once; a click that adds has no such stand-in.
      this.clicked = add ? null : { kind, name };
      this.$emit("select", { kind, name, add });
    },
    onSelectionEnd() {
      // The frame is done: Vue Flow has marked the buses inside it.
      const buses = new Set(this.flow.getSelectedNodes.value.map((n) => n.id));
      const items = [...buses].map((name) => ({ kind: "bus", name }));
      for (const e of (this.diagram && this.diagram.edges) || []) {
        if (buses.has(e.from_bus) && buses.has(e.to_bus)) {
          items.push({ kind: "branch", name: e.name });
        }
      }
      this.clicked = null;
      this.$emit("select_many", { items });
    },
    onNodeDoubleClick({ node }) {
      this.$emit("open", { kind: "bus", name: node.id });
    },
    onEdgeDoubleClick({ edge }) {
      this.$emit("open", { kind: "branch", name: edge.id });
    },
    onConnect(connection) {
      if (connection.source && connection.target && connection.source !== connection.target) {
        this.$emit("connect", { from_bus: connection.source, to_bus: connection.target });
      }
    },
    onNodeContextMenu({ event, node }) {
      this.openMenu(event, "bus", node.id, node.data.active);
    },
    onEdgeContextMenu({ event, edge }) {
      this.openMenu(event, "branch", edge.id, edge.data.active);
    },
    onSelectionContextMenu({ event }) {
      this.openMenu(event, "group", null, true);
    },
    onPaneContextMenu(event) {
      this.openMenu(event, "pane", null, true);
    },
    openMenu(event, kind, name, active) {
      event.preventDefault();
      this.active = true;
      let group = kind;
      if (name) {
        if (this.isSelected(kind, name) && this.selected.length > 1) {
          group = "group";
        } else if (!this.isSelected(kind, name)) {
          this.pick(kind, name, null);  // the menu acts on what it was opened on
        }
      }
      const state = active ? "active" : "inactive";
      const entries = (this.menus[group] || []).filter((entry) => !entry.when || entry.when === state);
      if (!entries.length) {
        return;
      }
      const box = this.$el.getBoundingClientRect();
      const p = this.flow.screenToFlowCoordinate({ x: event.clientX, y: event.clientY });
      this.menu = {
        // Inside the canvas: a menu opened at the right or lower edge moves in.
        left: Math.max(0, Math.min(event.clientX - box.left, box.width - 230)),
        top: Math.max(0, Math.min(event.clientY - box.top, box.height - 30 * entries.length - 16)),
        kind: group,
        name: group === "group" ? null : name,
        x: snap(p.x, this.grid),
        y: snap(p.y, this.grid),
        entries,
      };
    },
    choose(entry) {
      const menu = this.menu;
      this.menu = null;
      if (entry.action === "fit") {
        this.fit();
        return;
      }
      this.$emit("menu", { action: entry.action, kind: menu.kind, name: menu.name, x: menu.x, y: menu.y });
    },
    keyText(key) {
      const mac = /Mac|iPhone|iPad/.test(navigator.platform || "");
      return (key || "").replace("Mod+", mac ? "\u2318" : "Ctrl+");
    },
    onKey(event) {
      // The server's selection, or -- while its answer to a click is still on
      // the way -- the element clicked last.
      const items = this.clicked ? [this.clicked] : (this.selected || []);
      const mod = (event.metaKey || event.ctrlKey) && !event.altKey;
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
      if ((key === "Delete" || key === "Backspace") && items.length) {
        this.clicked = null;
        this.$emit("delete", { items: items.map((s) => ({ kind: s.kind, name: s.name })) });
      } else if (key === "Escape") {
        this.clicked = null;
        this.$emit("key", { action: "escape" });
      } else if (mod && key === "a") {
        event.preventDefault();  // not the text of the page
        this.$emit("key", { action: "select_all" });
      } else if (mod && key === "c" && items.length) {
        this.$emit("key", { action: "copy" });
      } else if (mod && (key === "v" || key === "d")) {
        event.preventDefault();  // not the browser's bookmark dialog
        // At the pointer while it is over the plan, else beside the originals.
        const at = this.pointer ? this.flow.screenToFlowCoordinate(this.pointer) : {};
        this.$emit("key", { action: key === "v" ? "paste" : "duplicate", x: at.x, y: at.y });
      }
    },
    fitOnce() {
      // Only when the diagram is first drawn: a symbol placed later must stay
      // where it was clicked, not jump with a new fit.
      if (!this.fitted) {
        this.fitted = true;
        this.fit();
      }
    },
    fit() {
      // After the next render and layout pass: the nodes of a new diagram and
      // the final size of the canvas are known only then.
      this.$nextTick(() => {
        window.setTimeout(() => this.fitNow(), 80);
      });
    },
    centreOn(target, tries) {
      // The canvas may belong to a tab that is only just being shown: wait
      // until it has a size.
      if (!this.$el.clientWidth || !this.$el.clientHeight) {
        if (tries > 0) {
          window.setTimeout(() => this.centreOn(target, tries - 1), 50);
        }
        return;
      }
      const nodes = {};
      for (const n of (this.diagram && this.diagram.nodes) || []) {
        nodes[n.name] = n;
      }
      let point = null;
      if (target.kind === "bus" && nodes[target.name]) {
        point = [nodes[target.name].x, nodes[target.name].y];
      } else if (target.kind === "branch") {
        const edge = ((this.diagram && this.diagram.edges) || []).find((e) => e.name === target.name);
        if (edge && nodes[edge.from_bus] && nodes[edge.to_bus]) {
          point = [(nodes[edge.from_bus].x + nodes[edge.to_bus].x) / 2, (nodes[edge.from_bus].y + nodes[edge.to_bus].y) / 2];
        }
      }
      if (point) {
        // Close enough to read the names around it; never further out than now.
        const zoom = Math.max(this.flow.viewport.value.zoom, 0.7);
        this.flow.setViewport({
          x: this.$el.clientWidth / 2 - zoom * point[0],
          y: this.$el.clientHeight / 2 - zoom * point[1],
          zoom,
        });
      }
    },
    fitNow() {
      const nodes = (this.diagram && this.diagram.nodes) || [];
      const width = this.$el.clientWidth;
      const height = this.$el.clientHeight;
      if (!nodes.length || !width || !height) {
        return;
      }
      let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
      for (const n of nodes) {
        const [w, h] = symbolSize(n);
        x0 = Math.min(x0, n.x - w / 2);
        y0 = Math.min(y0, n.y - h / 2);
        x1 = Math.max(x1, n.x + w / 2);
        y1 = Math.max(y1, n.y + h / 2);
      }
      const m = FIT_MARGIN;
      const zoom = Math.max(0.1, Math.min(
        FIT_MAX_ZOOM,
        (width - m.left - m.right) / Math.max(x1 - x0, 1),
        (height - m.top - m.bottom) / Math.max(y1 - y0, 1),
      ));
      // The symbols in the middle of what the margins leave. At once, not as
      // an animation: a click right after the fit must find the symbols where
      // they are drawn.
      this.flow.setViewport({
        x: m.left + (width - m.left - m.right - zoom * (x1 - x0)) / 2 - zoom * x0,
        y: m.top + (height - m.top - m.bottom - zoom * (y1 - y0)) / 2 - zoom * y0,
        zoom,
      });
    },
    async layoutElk() {
      const ELK = await loadELK();
      const graph = {
        id: "root",
        layoutOptions: {
          "elk.algorithm": "layered",
          "elk.direction": "RIGHT",
          "elk.spacing.nodeNode": String(4 * this.grid),
          "elk.layered.spacing.nodeNodeBetweenLayers": String(7 * this.grid),
        },
        children: this.diagram.nodes.map((n) => {
          const [w, h] = symbolSize(n);
          return { id: n.name, width: w, height: h };
        }),
        edges: this.diagram.edges.map((e) => ({ id: e.name, sources: [e.from_bus], targets: [e.to_bus] })),
      };
      const result = await new ELK().layout(graph);
      const positions = {};
      for (const child of result.children) {
        positions[child.id] = [snap(child.x + child.width / 2, this.grid), snap(child.y + child.height / 2, this.grid)];
      }
      this.$emit("layout", { positions });
    },
  },
  template: `
    <div :class="['gi-canvas', 'gi-tool-' + tool,
                  { 'gi-stale': annotations && annotations.stale, 'gi-far': far, 'gi-very-far': veryFar }]"
         tabindex="0">
      <VueFlow :id="flowId" :nodes="nodes" :edges="edges" :node-types="nodeTypes" :edge-types="edgeTypes"
               connection-mode="loose" :delete-key-code="null"
               :selection-key-code="tool === 'select' ? 'Shift' : null"
               :multi-selection-key-code="multiKeys" :zoom-on-double-click="false" :min-zoom="0.1" :max-zoom="4"
               @nodes-initialized="fitOnce" @selection-end="onSelectionEnd" @selection-drag-stop="onDragStop"
               @node-drag-stop="onDragStop" @pane-click="onPaneClick" @node-click="onNodeClick"
               @edge-click="onEdgeClick" @node-double-click="onNodeDoubleClick"
               @edge-double-click="onEdgeDoubleClick" @connect="onConnect"
               @node-context-menu="onNodeContextMenu" @edge-context-menu="onEdgeContextMenu"
               @selection-context-menu="onSelectionContextMenu" @pane-context-menu="onPaneContextMenu"
               @move-start="menu = null">
        <Background :gap="grid" pattern-color="#d6dbe1" />
        <MiniMap pannable zoomable :width="150" :height="100" />
        <Controls :show-interactive="false" :show-fit-view="false">
          <button type="button" class="vue-flow__controls-button vue-flow__controls-fitview" @click="fitNow">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 30"><path d="M3.692 4.63c0-.53.4-.938.939-.938h5.215V0H4.708C2.13 0 0 2.054 0 4.63v5.216h3.692V4.631zM27.354 0h-5.2v3.692h5.17c.53 0 .984.4.984.939v5.215H32V4.631A4.624 4.624 0 0027.354 0zm.954 24.83c0 .532-.4.94-.939.94h-5.215v3.768h5.215c2.577 0 4.631-2.13 4.631-4.707v-5.139h-3.692v5.139zm-23.677.94c-.531 0-.939-.4-.939-.94v-5.138H0v5.139c0 2.577 2.13 4.707 4.708 4.707h5.138V25.77H4.631z"/></svg>
          </button>
        </Controls>
      </VueFlow>
      <div v-if="hint" class="gi-hint">{{ hint }}</div>
      <div v-if="menu" class="gi-menu" role="menu" :style="{ left: menu.left + 'px', top: menu.top + 'px' }"
           @contextmenu.prevent>
        <div v-for="entry in menu.entries" :key="entry.action" role="menuitem" :data-action="entry.action"
             :class="['gi-menu-item', { 'gi-menu-danger': entry.danger, 'gi-menu-rule': entry.rule }]"
             @click="choose(entry)">
          <span>{{ entry.label }}</span><span class="gi-menu-key">{{ keyText(entry.key) }}</span>
        </div>
      </div>
    </div>`,
};
