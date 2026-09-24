import { afterEach, describe, expect, it, vi } from "vitest";

import { imageClassify, imageDetectGrid } from "./api";

type Call = { path: string; body: FormData };

function mockServer(handler: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const call = { path: new URL(url).pathname, body: init.body as FormData };
      calls.push(call);
      return handler(call);
    })
  );
  return calls;
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const gridParams = (file: File) => ({ file, threshold: 230, lineThreshold: 0.6, invert: false });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("image uploads", () => {
  it("uploads a file once and sends only its id to later stages", async () => {
    const file = new File(["photo-bytes"], "photo.jpg", { type: "image/jpeg" });
    const calls = mockServer(({ path }) =>
      path === "/image/uploads" ? json({ upload_id: "a".repeat(64) }) : json({ ok: true })
    );

    await imageDetectGrid(gridParams(file));
    await imageClassify({ file, threshold: 230, lineThreshold: 0.6, invert: false });

    expect(calls.map((call) => call.path)).toEqual(["/image/uploads", "/image/grid/detect", "/image/classify"]);
    for (const call of calls.slice(1)) {
      expect(call.body.get("file")).toBeNull();
      expect(call.body.get("upload_id")).toBe("a".repeat(64));
      expect(call.body.get("threshold")).toBe("230");
    }
  });

  it("re-uploads once when the server copy has expired", async () => {
    const file = new File(["photo-bytes"], "photo.jpg", { type: "image/jpeg" });
    let uploads = 0;
    let expired = true;
    const calls = mockServer(({ path }) => {
      if (path === "/image/uploads") {
        uploads += 1;
        return json({ upload_id: String(uploads).repeat(64) });
      }
      if (expired) {
        expired = false;
        return json({ detail: "upload_expired: upload the image again" }, 410);
      }
      return json({ ok: true });
    });

    await expect(imageDetectGrid(gridParams(file))).resolves.toEqual({ ok: true });

    expect(uploads).toBe(2);
    expect(calls[calls.length - 1]?.body.get("upload_id")).toBe("2".repeat(64));
  });

  it("falls back to sending the file when the upload endpoint fails", async () => {
    const file = new File(["photo-bytes"], "photo.jpg", { type: "image/jpeg" });
    const calls = mockServer(({ path }) => (path === "/image/uploads" ? json({ detail: "nope" }, 404) : json({ ok: true })));

    await imageDetectGrid(gridParams(file));

    const stage = calls.find((call) => call.path === "/image/grid/detect");
    expect(stage?.body.get("file")).toBeInstanceOf(File);
    expect(stage?.body.get("upload_id")).toBeNull();
  });

  it("surfaces server errors from the stage itself", async () => {
    const file = new File(["photo-bytes"], "photo.jpg", { type: "image/jpeg" });
    mockServer(({ path }) =>
      path === "/image/uploads" ? json({ upload_id: "b".repeat(64) }) : json({ detail: "Grid detection failed" }, 400)
    );

    await expect(imageDetectGrid(gridParams(file))).rejects.toThrow("Grid detection failed");
  });
});
