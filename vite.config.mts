import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import fs from "fs";

// https://vitejs.dev/config/
export default defineConfig({
  root: "./src",
  envDir: "../",
  plugins: [
    react(),
    {
      name: "serve-artemis-assets",
      configureServer(server) {
        const assetsDir = path.resolve(__dirname, "../ArtemisInRealTime_assets");
        const CONTENT_TYPES: Record<string, string> = {
          ".json": "application/json",
          ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg",
          ".png": "image/png",
          ".webp": "image/webp",
          ".gif": "image/gif",
        };
        server.middlewares.use("/artemis-assets", (req, res, next) => {
          // Decode %20 etc. so filenames with spaces resolve on disk.
          const decoded = decodeURIComponent(req.url ?? "");
          const filePath = path.join(assetsDir, decoded);
          const normalized = path.normalize(filePath);
          if (!normalized.startsWith(assetsDir)) {
            res.statusCode = 403;
            res.end();
            return;
          }
          if (fs.existsSync(normalized) && fs.statSync(normalized).isFile()) {
            const ext = path.extname(normalized).toLowerCase();
            res.setHeader("Content-Type", CONTENT_TYPES[ext] ?? "application/octet-stream");
            fs.createReadStream(normalized).pipe(res);
          } else {
            next();
          }
        });
      },
    },
  ],
  resolve: {
    alias: {
      components: path.resolve(__dirname, "./src/components"),
      utils: path.resolve(__dirname, "./src/utils"),
      styles: path.resolve(__dirname, "./src/styles"),
      pages: path.resolve(__dirname, "./src/pages"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 8000,
  },
  build: {
    outDir: "../.local/vite/dist",
    assetsDir: "assets",
    sourcemap: true,
    manifest: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/react/") || id.includes("node_modules/react-dom/")) {
            return "react";
          }
          if (id.includes("node_modules/react-router-dom/")) {
            return "router";
          }
        },
      },
    },
  },
});
