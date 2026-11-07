import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http, { type IncomingHttpHeaders, type Server } from "node:http";
import { gzipSync, gunzipSync } from "node:zlib";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { serveAsset } from "../server/serveAsset.ts";

let root: string;
let server: Server;
let port: number;
const content = Buffer.from('{"comm":[],"pao":[{"text":"Mission coverage"}]}');

beforeAll(async () => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "artemis-release-http-"));
  fs.mkdirSync(path.join(root, "releases", "baseline"), { recursive: true });
  fs.writeFileSync(path.join(root, "releases", "baseline", "transcripts.json"), content);
  fs.writeFileSync(
    path.join(root, "releases", "baseline", "transcripts.json.gz"),
    gzipSync(content)
  );
  fs.writeFileSync(path.join(root, "active-release.json"), "{}");
  fs.writeFileSync(path.join(root, "audio.m4a"), "0123456789");
  server = http.createServer((req, res) => {
    const name = req.url?.slice(1) ?? "";
    serveAsset(
      req,
      res,
      path.join(root, name),
      name.endsWith(".m4a") ? "audio/mp4" : "application/json"
    );
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Missing test server port");
  port = address.port;
});

afterAll(async () => {
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve()))
  );
  fs.rmSync(root, { recursive: true });
});

function request(
  name: string,
  headers: Record<string, string> = {},
  method = "GET"
): Promise<{ status: number; headers: IncomingHttpHeaders; body: Buffer }> {
  return new Promise((resolve, reject) => {
    http
      .request({ hostname: "127.0.0.1", port, path: `/${name}`, headers, method }, (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk: Buffer) => chunks.push(chunk));
        response.on("end", () =>
          resolve({
            status: response.statusCode ?? 0,
            headers: response.headers,
            body: Buffer.concat(chunks),
          })
        );
        response.on("error", reject);
      })
      .on("error", reject)
      .end();
  });
}

describe("release asset HTTP serving", () => {
  const transcript = "releases/baseline/transcripts.json";
  it("negotiates gzip with correct type, length, cache policy and Vary", async () => {
    const response = await request(transcript, { "Accept-Encoding": "br, gzip" });
    expect(response.status).toBe(200);
    expect(response.headers["content-encoding"]).toBe("gzip");
    expect(response.headers["content-type"]).toBe("application/json");
    expect(response.headers.vary).toBe("Accept-Encoding");
    expect(response.headers["cache-control"]).toContain("immutable");
    expect(Number(response.headers["content-length"])).toBe(response.body.length);
    expect(gunzipSync(response.body)).toEqual(content);
  });
  it("honors gzip refusal even with a wildcard and revalidates the pointer", async () => {
    const response = await request(transcript, { "Accept-Encoding": "gzip;q=0, *;q=1" });
    expect(response.headers["content-encoding"]).toBeUndefined();
    expect(response.body).toEqual(content);
    expect((await request("active-release.json")).headers["cache-control"]).toBe("no-cache");
  });
  it("preserves partial audio responses and rejects out-of-range requests", async () => {
    const response = await request("audio.m4a", { Range: "bytes=3-6", "Accept-Encoding": "gzip" });
    expect(response.status).toBe(206);
    expect(response.headers["content-range"]).toBe("bytes 3-6/10");
    expect(response.headers["content-type"]).toBe("audio/mp4");
    expect(response.body.toString()).toBe("3456");
    expect((await request("audio.m4a", { Range: "bytes=10-" })).status).toBe(416);
    expect((await request("audio.m4a", { Range: "bytes=-3" })).body.toString()).toBe("789");
  });
  it("serves HEAD without a body and JSON ranges from the uncompressed representation", async () => {
    const head = await request(transcript, { "Accept-Encoding": "gzip" }, "HEAD");
    expect(head.headers["content-encoding"]).toBe("gzip");
    expect(head.body.length).toBe(0);
    const range = await request(transcript, { Range: "bytes=0-4", "Accept-Encoding": "gzip" });
    expect(range.status).toBe(206);
    expect(range.headers["content-encoding"]).toBeUndefined();
    expect(range.body).toEqual(content.subarray(0, 5));
  });
});
