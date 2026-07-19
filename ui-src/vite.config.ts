import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  base: "./",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: fileURLToPath(new URL("../extension", import.meta.url)),
    emptyOutDir: false,
    cssCodeSplit: false,
    sourcemap: false,
    target: "chrome120",
    rollupOptions: {
      input: fileURLToPath(new URL("./popup.html", import.meta.url)),
      output: {
        entryFileNames: "popup.js",
        assetFileNames: "popup.[ext]",
      },
    },
  },
});
