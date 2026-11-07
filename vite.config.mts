import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import fs from "fs";
import { serveAsset } from "./src/server/serveAsset.ts";

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
        const pdsEnv = loadEnv(server.config.mode, __dirname, "PDS_");
        const pdsDir = path.resolve(
          process.env.PDS_ASSETS_DIR ?? pdsEnv.PDS_ASSETS_DIR ?? "D:/ArtemisInRealTime_assets",
          "artemis-ii/raw/pds"
        );
        server.middlewares.use("/pds-assets", (req, res, next) => {
          let decoded: string;
          try {
            decoded = decodeURIComponent((req.url ?? "").split("?")[0]);
          } catch {
            res.statusCode = 400;
            res.end();
            return;
          }
          const filePath = path.resolve(pdsDir, `.${decoded}`);
          const relative = path.relative(pdsDir, filePath);
          if (relative.startsWith("..") || path.isAbsolute(relative)) {
            res.statusCode = 403;
            res.end();
            return;
          }
          if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
            next();
            return;
          }
          const realRelative = path.relative(pdsDir, fs.realpathSync(filePath));
          if (realRelative.startsWith("..") || path.isAbsolute(realRelative)) {
            res.statusCode = 403;
            res.end();
            return;
          }
          const ext = path.extname(filePath).toLowerCase();
          const types: Record<string, string> = {
            ".mp4": "video/mp4",
            ".m4a": "audio/mp4",
            ".wav": "audio/wav",
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".pdf": "application/pdf",
            ".xml": "application/xml",
            ".csv": "text/csv",
            ".json": "application/json",
          };
          serveAsset(req, res, filePath, types[ext] ?? "application/octet-stream");
        });
        const CONTENT_TYPES: Record<string, string> = {
          ".json": "application/json",
          ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg",
          ".png": "image/png",
          ".webp": "image/webp",
          ".gif": "image/gif",
          ".aac": "audio/aac",
          ".m4a": "audio/mp4",
          ".mp3": "audio/mpeg",
          ".wav": "audio/wav",
          ".mp4": "video/mp4",
          ".webm": "video/webm",
          ".gz": "application/gzip",
        };
        server.middlewares.use("/artemis-assets", (req, res, next) => {
          // Decode %20 etc. so filenames with spaces resolve on disk.
          let decoded: string;
          try {
            decoded = decodeURIComponent((req.url ?? "").split("?")[0]);
          } catch {
            res.statusCode = 400;
            res.end();
            return;
          }
          const normalized = path.resolve(assetsDir, `.${decoded}`);
          const relative = path.relative(assetsDir, normalized);
          if (relative.startsWith("..") || path.isAbsolute(relative)) {
            res.statusCode = 403;
            res.end();
            return;
          }
          if (fs.existsSync(normalized) && fs.statSync(normalized).isFile()) {
            const realRelative = path.relative(assetsDir, fs.realpathSync(normalized));
            if (realRelative.startsWith("..") || path.isAbsolute(realRelative)) {
              res.statusCode = 403;
              res.end();
              return;
            }
            const ext = path.extname(normalized).toLowerCase();
            serveAsset(req, res, normalized, CONTENT_TYPES[ext] ?? "application/octet-stream");
          } else {
            // Asset misses must stay 404: the release resolver uses this to
            // distinguish legacy roots from a malformed publication.
            res.statusCode = 404;
            res.end();
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
