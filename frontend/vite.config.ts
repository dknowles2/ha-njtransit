import { defineConfig } from "vite";

// The bundle is committed, because HACS copies files and never runs a build.
// It therefore has to be reproducible: CI rebuilds it and fails on a diff, so
// anything varying between runs -- a hash in a filename, a source-map comment
// naming an absolute path -- would break the build on every pull request.
export default defineConfig({
  build: {
    outDir: "../custom_components/njtransit/frontend",
    emptyOutDir: false,
    target: "es2022",
    sourcemap: false,
    lib: {
      entry: "src/njtransit-card.ts",
      formats: ["es"],
      fileName: () => "njtransit-card.js",
    },
    rollupOptions: {
      output: {
        // Lit is bundled in. A card cannot rely on Home Assistant's own copy:
        // the frontend does not export it, and a bare `lit` specifier would
        // reach the browser unresolved.
        inlineDynamicImports: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    include: ["test/**/*.test.ts"],
    setupFiles: ["test/setup.ts"],
  },
});
