import fs from "node:fs";
import path from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";

function acceptsGzip(header: string | undefined): boolean {
  const encodings = new Map(
    (header ?? "").split(",").map((entry) => {
      const [name, ...parameters] = entry.trim().toLowerCase().split(";");
      const quality = parameters.find((parameter) => parameter.trim().startsWith("q="));
      return [name, quality ? Number(quality.trim().slice(2)) : 1] as const;
    })
  );
  return (encodings.get("gzip") ?? encodings.get("*") ?? 0) > 0;
}

/** Serve staged JSON representations while preserving byte-range media playback. */
export function serveAsset(
  req: IncomingMessage,
  res: ServerResponse,
  filePath: string,
  contentType: string
): void {
  const isJson = filePath.endsWith(".json");
  const gzipPath = `${filePath}.gz`;
  const compressed =
    isJson &&
    !req.headers.range &&
    acceptsGzip(req.headers["accept-encoding"]) &&
    fs.existsSync(gzipPath) &&
    fs.statSync(gzipPath).isFile() &&
    !fs.lstatSync(gzipPath).isSymbolicLink();
  const selected = compressed ? gzipPath : filePath;
  const size = fs.statSync(selected).size;
  res.setHeader("Content-Type", contentType);
  res.setHeader("Accept-Ranges", compressed ? "none" : "bytes");
  if (isJson) res.setHeader("Vary", "Accept-Encoding");
  if (compressed) res.setHeader("Content-Encoding", "gzip");
  res.setHeader(
    "Cache-Control",
    filePath.split(path.sep).includes("releases")
      ? "public, max-age=31536000, immutable"
      : "no-cache"
  );
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
  const stream = fs.createReadStream(selected, { start, end });
  stream.on("error", () => res.destroy());
  res.on("close", () => stream.destroy());
  stream.pipe(res);
}
