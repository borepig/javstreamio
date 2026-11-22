// Module hook: stub "next/server" so the catch-all route handler can run
// under plain Node (next/server is only resolvable inside the Next runtime).
export function resolve(specifier, ctx, nextResolve) {
  if (specifier === "next/server") {
    return { url: "stub:next/server", shortCircuit: true };
  }
  return nextResolve(specifier, ctx);
}

export function load(url, ctx, nextLoad) {
  if (url === "stub:next/server") {
    return {
      format: "module",
      source: `
        class NextResponse extends Response {
          static json(body, init = {}) {
            const headers = new Headers(init.headers);
            headers.set("content-type", "application/json");
            return new NextResponse(JSON.stringify(body), {
              status: init.status,
              headers,
            });
          }
        }
        export { NextResponse };
      `,
      shortCircuit: true,
    };
  }
  return nextLoad(url, ctx);
}
