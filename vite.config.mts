import { defineConfig, loadEnv } from "vite";
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
          const size = fs.statSync(filePath).size;
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
          res.setHeader("Content-Type", types[ext] ?? "application/octet-stream");
          res.setHeader("Accept-Ranges", "bytes");
          let start = 0;
          let end = size - 1;
          if (req.headers.range) {
            const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range);
            if (!range || (!range[1] && !range[2])) {
              res.statusCode = 416;
              res.setHeader("Content-Range", `bytes */${size}`);
              res.end();
              return;
            }
            if (range[1]) {
              start = Number(range[1]);
              end = range[2] ? Math.min(Number(range[2]), end) : end;
            } else {
              start = Math.max(0, size - Number(range[2]));
            }
            if (
              !Number.isSafeInteger(start) ||
              !Number.isSafeInteger(end) ||
              start > end ||
              start >= size
            ) {
              res.statusCode = 416;
              res.setHeader("Content-Range", `bytes */${size}`);
              res.end();
              return;
            }
            res.statusCode = 206;
            res.setHeader("Content-Range", `bytes ${start}-${end}/${size}`);
          }
          res.setHeader("Content-Length", Math.max(0, end - start + 1));
          if (req.method === "HEAD" || size === 0) {
            res.end();
            return;
          }
          const stream = fs.createReadStream(filePath, { start, end });
          stream.on("error", () => res.destroy());
          res.on("close", () => stream.destroy());
          stream.pipe(res);
        });
        const CONTENT_TYPES: Record<string, string> = {
          ".json": "application/json",
          ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg",
          ".png": "image/png",
          ".webp": "image/webp",
          ".gif": "image/gif",
          ".aac": "audio/aac",
          ".mp3": "audio/mpeg",
          ".wav": "audio/wav",
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
