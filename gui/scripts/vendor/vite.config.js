// gui/scripts/vendor/vite.config.js
//
// One ES module (plus the lazily loaded elkjs chunk), Vue left to NiceGUI's import map, minified, no source map
// (the readable sources are the pinned npm tarballs named in MANIFEST.json).
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "./src/index.mjs",
      fileName: () => "index.js",
      formats: ["es"],
    },
    outDir: "dist",
    emptyOutDir: true,
    target: "es2020",
    minify: true,
    sourcemap: false,
    rolldownOptions: {
      external: ["vue"],
      // A stable name for the elkjs chunk, so a rebuild with the same pins
      // reproduces SHA256SUMS line for line.
      output: { chunkFileNames: "[name].js" },
    },
  },
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
});
