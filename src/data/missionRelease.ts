/** Pin a mission's release for the life of this page, including later audio/crew requests. */
export function createReleaseResolver() {
  const releases = new Map<string, Promise<string>>();
  return function resolve(base: string): Promise<string> {
    const root = base.replace(/\/+$/, "");
    let pending = releases.get(root);
    if (!pending) {
      pending = (async () => {
        const response = await fetch(`${root}/active-release.json`, { cache: "no-cache" });
        // Installations without versioned publication keep their existing enriched assets.
        if (response.status === 404) return root;
        if (!response.ok) throw new Error(`active-release.json HTTP ${response.status}`);
        const pointer = await response.json();
        if (
          pointer?.schema_version !== 1 ||
          typeof pointer.release_id !== "string" ||
          !/^[a-zA-Z0-9_-]{1,128}$/.test(pointer.release_id) ||
          pointer.base_url !== `releases/${pointer.release_id}/` ||
          pointer.manifest !== "release-manifest.json"
        ) {
          throw new Error("Invalid mission release pointer");
        }
        return `${root}/${pointer.base_url.replace(/\/$/, "")}`;
      })();
      releases.set(root, pending);
      pending.catch(() => releases.delete(root));
    }
    return pending;
  };
}

export const resolveMissionRelease = createReleaseResolver();
