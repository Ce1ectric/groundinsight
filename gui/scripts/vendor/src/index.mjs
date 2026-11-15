// gui/scripts/vendor/src/index.mjs
//
// Entry point of the vendored bundle that the network canvas imports as
// "groundinsight-vueflow". Vue itself is external: the bundle must use the
// Vue instance NiceGUI serves through its import map, otherwise reactive
// state would live in a second copy of Vue and never reach the page.
//
// The style sheets are inlined as strings and injected once by
// injectStyles(), so the canvas needs no second request and no path that
// depends on where NiceGUI mounts the module.

export { VueFlow, Handle, Position, MarkerType, useVueFlow } from "@vue-flow/core";
export { MiniMap } from "@vue-flow/minimap";
export { Controls, ControlButton } from "@vue-flow/controls";
export { Background, BackgroundVariant } from "@vue-flow/background";
// elkjs is most of the bundle's weight (GWT-compiled Java) and only needed
// for the automatic layout, so it is a chunk of its own, fetched on first use.
export const loadELK = () => import("elkjs/lib/elk.bundled.js").then((module) => module.default);

import coreCss from "@vue-flow/core/dist/style.css?inline";
import themeCss from "@vue-flow/core/dist/theme-default.css?inline";
import minimapCss from "@vue-flow/minimap/dist/style.css?inline";
import controlsCss from "@vue-flow/controls/dist/style.css?inline";

export const styles = [coreCss, themeCss, minimapCss, controlsCss].join("\n");

const STYLE_ID = "groundinsight-vueflow-styles";

export function injectStyles() {
  if (document.getElementById(STYLE_ID)) {
    return;
  }
  const element = document.createElement("style");
  element.id = STYLE_ID;
  element.textContent = styles;
  document.head.append(element);
}
