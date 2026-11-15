// groundinsight_gui/network_canvas.js
//
// The network canvas: Vue Flow drawing the DiagramDTO of the session.
//
// The component holds no project state and makes no decision about the
// network. It draws what the server sends (props `diagram`, `colours`,
// `selected`, `tool`) and reports what the user did as events -- `place`,
// `move`, `connect`, `fault`, `select`, `open`, `delete`, `layout` -- which
// the Python side turns into commands of groundinsight.api. Even a dragged
// symbol keeps its old place until the session has applied the MoveNodes
// command and sent the new diagram back.
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
         :data-bus="id" :title="id">
      <Handle type="target" position="top" class="gi-handle" />
      <Handle type="source" position="top" class="gi-handle" />
      <span class="gi-label"
            :style="{ left: (data.w / 2 + 8 + data.label_offset[0]) + 'px', top: (data.h / 2 - 22 + data.label_offset[1]) + 'px' }">{{ id }}</span>
      <span v-if="data.has_fault" class="gi-flash">&#x26A1;</span>
      <span v-if="data.has_source" class="gi-infeed">&#x25BC;</span>
    </div>`,
};

const LineEdge = {
  name: "GiLineEdge",
  props: ["id", "data", "selected", "sourceNode", "targetNode"],
  computed: {
    points() {
      return [centre(this.sourceNode), ...(this.data.waypoints || []), centre(this.targetNode)];
    },
    path() {
      return this.points.map((p, i) => (i ? "L" : "M") + p[0] + " " + p[1]).join(" ");
    },
    middle() {
      const p = this.points;
      const a = p[Math.floor((p.length - 1) / 2)];
      const b = p[Math.ceil((p.length - 1) / 2)];
      return [(a[0] + b[0]) / 2 + this.data.label_offset[0], (a[1] + b[1]) / 2 + this.data.label_offset[1]];
    },
  },
  template: `
    <g :class="['gi-edge', { 'gi-sel': selected, 'gi-off': !data.active, 'gi-noearth': !data.grounding_conductor }]"
       :data-branch="id">
      <path :d="path" class="gi-edge-hit" />
      <path :d="path" class="gi-edge-line" :style="{ stroke: data.colour }" />
      <text :x="middle[0]" :y="middle[1] - 6" class="gi-edge-label">{{ id }}</text>
    </g>`,
};

export default {
  components: { VueFlow, MiniMap, Controls, Background },
  props: {
    diagram: Object,
    tool: { type: String, default: "select" },
    colours: Object,
    selected: Object,
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
      active: false,
      clicked: null,
    };
  },
  mounted() {
    // The delete key belongs to the canvas when it was clicked last. Listening
    // on the window, not on the canvas element, keeps it working when a
    // redraw from the server has taken the focus away from the clicked node.
    this.onPointer = (event) => {
      this.active = this.$el.contains(event.target);
    };
    this.onWindowKey = (event) => {
      const tag = (event.target && event.target.tagName) || "";
      if (this.active && !["INPUT", "TEXTAREA", "SELECT"].includes(tag) && !event.target.isContentEditable) {
        this.onKey(event);
      }
    };
    window.addEventListener("pointerdown", this.onPointer, true);
    window.addEventListener("keydown", this.onWindowKey);
  },
  unmounted() {
    window.removeEventListener("pointerdown", this.onPointer, true);
    window.removeEventListener("keydown", this.onWindowKey);
  },
  computed: {
    grid() {
      return (this.diagram && this.diagram.grid_size) || 20;
    },
    nodes() {
      const colours = (this.colours && this.colours.nodes) || {};
      return ((this.diagram && this.diagram.nodes) || []).map((n) => {
        const [w, h] = symbolSize(n);
        return {
          id: n.name,
          type: "bus",
          position: { x: n.x - w / 2, y: n.y - h / 2 },
          data: { ...n, w, h, colour: colours[n.name] || "#2a78d6" },
          selected: this.isSelected("bus", n.name),
          draggable: this.tool === "select",
          connectable: this.tool === "branch",
        };
      });
    },
    edges() {
      const colours = (this.colours && this.colours.edges) || {};
      return ((this.diagram && this.diagram.edges) || []).map((e) => ({
        id: e.name,
        type: "line",
        source: e.from_bus,
        target: e.to_bus,
        data: { ...e, colour: colours[e.name] || "#5b6470" },
        selected: this.isSelected("branch", e.name),
      }));
    },
  },
  methods: {
    isSelected(kind, name) {
      return !!this.selected && this.selected.kind === kind && this.selected.name === name;
    },
    onDragStop(event) {
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
    onNodeClick({ node }) {
      if (this.tool === "fault") {
        this.$emit("fault", { bus: node.id });
      } else if (this.tool === "select") {
        this.clicked = { kind: "bus", name: node.id };
        this.$emit("select", this.clicked);
      }
    },
    onEdgeClick({ edge }) {
      if (this.tool === "select") {
        this.clicked = { kind: "branch", name: edge.id };
        this.$emit("select", this.clicked);
      }
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
    onKey(event) {
      // The server's selection, or -- while its answer to a click is still on
      // the way -- the element clicked last.
      const target = (this.selected && this.selected.name) ? this.selected : this.clicked;
      if ((event.key === "Delete" || event.key === "Backspace") && target && target.name) {
        this.clicked = null;
        this.$emit("delete", { kind: target.kind, name: target.name });
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
      // the final size of the canvas are known only then. A short feeder
      // would otherwise fill the screen at four-fold zoom, hence maxZoom.
      this.$nextTick(() => {
        window.setTimeout(() => this.flow.fitView({ padding: 0.2, maxZoom: 1.5 }), 80);
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
    <div :class="['gi-canvas', 'gi-tool-' + tool]" tabindex="0">
      <VueFlow :id="flowId" :nodes="nodes" :edges="edges" :node-types="nodeTypes" :edge-types="edgeTypes"
               connection-mode="loose" :delete-key-code="null" :selection-key-code="null"
               :multi-selection-key-code="null" :zoom-on-double-click="false" :min-zoom="0.1" :max-zoom="4"
               @nodes-initialized="fitOnce"
               @node-drag-stop="onDragStop" @pane-click="onPaneClick" @node-click="onNodeClick"
               @edge-click="onEdgeClick" @node-double-click="onNodeDoubleClick"
               @edge-double-click="onEdgeDoubleClick" @connect="onConnect">
        <Background :gap="grid" pattern-color="#d6dbe1" />
        <MiniMap pannable zoomable />
        <Controls :show-interactive="false" />
      </VueFlow>
    </div>`,
};
