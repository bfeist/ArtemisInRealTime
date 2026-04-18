export async function setup(): Promise<void> {
  console.log("vitest globalSetup");
}

export async function teardown(): Promise<void> {
  console.log("vitest globalTeardown");
}
