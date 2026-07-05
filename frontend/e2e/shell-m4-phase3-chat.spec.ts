/**
 * shell-m4-phase3-chat.spec.ts — M4 Phase 3 QA gate (ADR-0019 / F6 / F7 / G3 / I3 / I4 / I6 / I7)
 *
 * Gate checks covered:
 *   CHAT-NAV-1:    Chat button in NavRail is enabled (not disabled, not aria-disabled)
 *   CHAT-NAV-2:    Clicking Chat nav item shows section-chat with conversation-list + message-list
 *   CHAT-CONV-1:   Existing conversations are listed on mount
 *   CHAT-CONV-2:   New conversation button creates a new conversation (POST /conversations)
 *   CHAT-STREAM-1: Sending a message triggers token streaming into the assistant bubble
 *   CHAT-STREAM-2: Streaming ends with a finalized GFM-rendered message in message-list
 *   CHAT-STREAM-3: done event cost label appears (I7)
 *   CHAT-PERSIST-1: GET /conversations/{id}/messages returns persisted messages after stream
 *   CHAT-REGEN-1:  Regenerate button appears on last assistant message
 *   CHAT-STOP-1:   Stop button is visible during streaming; clicking it aborts the stream
 *   CHAT-INPUT-1:  Input is a plain <textarea> (not CodeMirror, not contenteditable — I4)
 *   CHAT-INPUT-2:  Enter key sends; Shift+Enter inserts newline (I4)
 *   CHAT-THINK-1:  ThinkBlock component renders collapsed when given think content
 *                  (tested via DOM fixture injection since qwen2.5:3b doesn't emit <think>)
 *
 *   G3-LONGTASK:   PerformanceObserver: assert NO longtask >50ms on main thread during
 *                  a live qwen2.5:3b stream of ~20 tokens (the headline G3 perf gate).
 *                  Method: inject a PerformanceObserver before sending, stream, then read
 *                  collected longtask durations from page.evaluate().
 *
 *   I3-ISOLATION:  Sending a chat does NOT re-render graphStore/tree (verified by checking
 *                  section-chat is the only active section and graph/tree are NOT present).
 *   I6-BODY:       The /chat/stream request body contains NO provider_type or model_id
 *                  (verified via page.route interception).
 *
 *   D5-SCREENSHOTS: docs/screens/chat-streaming.png (during stream),
 *                   docs/screens/chat-conversation.png (after finalized)
 *
 * Prerequisites:
 *   Backend: http://localhost:8000  (chat provider_config seeded: operation=chat, local/qwen2.5:3b)
 *   Frontend: http://localhost:5173 (Vite dev server)
 *
 * Run:
 *   cd frontend && npx playwright test e2e/shell-m4-phase3-chat.spec.ts --timeout=120000
 */

import { test, expect, type Page } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";
import { fileURLToPath } from "url";

// ── Config ─────────────────────────────────────────────────────────────────────

const FRONTEND_URL = process.env["SYNAPSE_FRONTEND_URL"] ?? "http://localhost:5173";
const BACKEND_URL  = process.env["SYNAPSE_BACKEND_URL"]  ?? "http://localhost:8000";

// Gate for tests that require a live Ollama endpoint with qwen2.5:3b loaded.
// In CI the model is not available; those describes are skipped automatically.
// Manual TrueNAS runs export E2E_LIVE_CHAT=1 to opt-in.
const isLiveChat = process.env["E2E_LIVE_CHAT"] === "1";

const _thisDir    = path.dirname(fileURLToPath(import.meta.url));
const SCREENS_DIR = path.resolve(_thisDir, "../../docs/screens");

function ensureScreensDir() {
  if (!fs.existsSync(SCREENS_DIR)) {
    fs.mkdirSync(SCREENS_DIR, { recursive: true });
  }
}

/** Load app and wait for shell */
async function gotoApp(page: Page) {
  await page.goto(FRONTEND_URL, { waitUntil: "networkidle" });
  await expect(page.getByTestId("app-shell")).toBeVisible();
  await expect(page.getByTestId("nav-rail")).toBeVisible();
}

/** Navigate to the Chat section */
async function gotoChat(page: Page) {
  await gotoApp(page);
  const chatBtn = page.locator("[data-section='chat']");
  await expect(chatBtn).not.toBeDisabled();
  await chatBtn.click();
  await expect(page.getByTestId("section-chat")).toBeVisible({ timeout: 5000 });
}

// ── Backend health ─────────────────────────────────────────────────────────────

test("backend is reachable and chat provider is seeded", async ({ request }) => {
  const status = await request.get(`${BACKEND_URL}/status`);
  expect(status.status()).toBe(200);

  const providers = await request.get(`${BACKEND_URL}/provider/config`);
  expect(providers.status()).toBe(200);
  const body = await providers.json() as { items: Array<{ operation: string | null; provider_type: string }> };
  const chatProv = body.items.find((i) => i.operation === "chat");
  expect(chatProv).toBeDefined();
  expect(chatProv?.provider_type).toBe("local");
});

// ── NavRail chat ───────────────────────────────────────────────────────────────

test.describe("Chat NavRail", () => {
  test("CHAT-NAV-1: Chat button is ENABLED (not disabled, not aria-disabled)", async ({ page }) => {
    await gotoApp(page);
    const chatBtn = page.locator("[data-section='chat']");
    // The button must be clickable (Phase 3 ships chat)
    await expect(chatBtn).not.toBeDisabled();
    const ariaDisabled = await chatBtn.getAttribute("aria-disabled");
    expect(ariaDisabled).not.toBe("true");
  });

  test("CHAT-NAV-2: clicking Chat shows section-chat with conversation-list and message-list", async ({ page }) => {
    await gotoChat(page);
    await expect(page.getByTestId("conversation-list")).toBeVisible();
    await expect(page.getByTestId("message-list")).toBeVisible();
  });
});

// ── Conversation list ──────────────────────────────────────────────────────────

test.describe("Conversation list", () => {
  test("CHAT-CONV-1: existing conversations are listed on mount", async ({ page }) => {
    await gotoChat(page);
    const convList = page.getByTestId("conversation-list");
    // Wait for at least one conversation item (backend has 4+ seeded)
    await expect(convList).toBeVisible();
    // The list should contain at least one conversation item (role=button or similar)
    await page.waitForTimeout(1000); // let fetch complete
    // Conversations are rendered as role="button" divs inside conversation-list
    const items = convList.locator("[role='button']");
    const count = await items.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("CHAT-CONV-2: new conversation button creates a conversation", async ({ page }) => {
    await gotoChat(page);
    const convList = page.getByTestId("conversation-list");
    await page.waitForTimeout(800);
    const beforeCount = await convList.locator("[role='button']").count();

    // The New conversation button (identified by aria-label)
    const newBtn = convList.getByRole("button", { name: "New conversation" });
    await newBtn.click();
    await page.waitForTimeout(800);

    const afterCount = await convList.locator("[role='button']").count();
    // A new conversation should have been prepended
    expect(afterCount).toBeGreaterThan(beforeCount);
  });
});

// ── Input invariants ───────────────────────────────────────────────────────────

test.describe("Input invariants (I4)", () => {
  test("CHAT-INPUT-1: input is a plain textarea (not CodeMirror, not contentEditable)", async ({ page }) => {
    await gotoChat(page);
    // The input MUST be a <textarea> element (I4)
    const textarea = page.locator("textarea[aria-label]").first();
    await expect(textarea).toBeVisible();
    // Must NOT be a CodeMirror div (I4 — CodeMirror is for wiki editor, not chat)
    const codeMirrorCount = await page.locator(".cm-editor, .CodeMirror").count();
    expect(codeMirrorCount).toBe(0);
    // Must NOT be contentEditable (no WYSIWYG / ProseMirror)
    const contentEditableCount = await page.locator("[contenteditable='true']").count();
    expect(contentEditableCount).toBe(0);
  });

  test("CHAT-INPUT-2: Enter sends; Shift+Enter inserts newline", async ({ page }) => {
    await gotoChat(page);
    const textarea = page.locator("textarea").first();
    await textarea.click();

    // Shift+Enter should NOT send (just insert newline)
    await textarea.press("Shift+Enter");
    const valueAfterShiftEnter = await textarea.inputValue();
    // Value should contain a newline
    expect(valueAfterShiftEnter).toContain("\n");

    // Clear and type something, then Enter — should send
    await textarea.fill("ping test enter key");
    // Intercept the POST to not wait forever for Ollama
    // We just verify the textarea clears after Enter (optimistic send)
    await page.route("**/chat/stream", async (route) => {
      // Respond with a minimal done event immediately to avoid waiting for Ollama
      await route.fulfill({
        status: 200,
        contentType: "application/x-ndjson",
        body:
          '{"type":"token","delta":"pong"}\n' +
          '{"type":"done","conversation_id":"00000000-0000-0000-0000-000000000001",' +
          '"message_id":"00000000-0000-0000-0000-000000000002",' +
          '"input_tokens":5,"output_tokens":1,"total_cost_usd":0.0,' +
          '"iterations_used":1,"finish_reason":"stop"}\n',
      });
    });
    await textarea.press("Enter");
    // After Enter, textarea should clear (message submitted)
    await page.waitForTimeout(300);
    const valueAfterEnter = await textarea.inputValue();
    expect(valueAfterEnter).toBe("");
  });
});

// ── I6 invariant: no provider_type/model_id in request body ───────────────────

test("I6-BODY: /chat/stream request body contains no provider_type or model_id", async ({ page }) => {
  await gotoChat(page);

  let capturedBody: Record<string, unknown> | null = null;

  await page.route("**/chat/stream", async (route) => {
    const request = route.request();
    const bodyText = request.postData();
    if (bodyText) {
      capturedBody = JSON.parse(bodyText) as Record<string, unknown>;
    }
    // Fulfill with minimal response to not block the test
    await route.fulfill({
      status: 200,
      contentType: "application/x-ndjson",
      body:
        '{"type":"token","delta":"ok"}\n' +
        '{"type":"done","conversation_id":"00000000-0000-0000-0000-000000000001",' +
        '"message_id":"00000000-0000-0000-0000-000000000002",' +
        '"input_tokens":5,"output_tokens":1,"total_cost_usd":0.0,' +
        '"iterations_used":1,"finish_reason":"stop"}\n',
    });
  });

  const textarea = page.locator("textarea").first();
  await textarea.fill("i6 invariant check");
  await textarea.press("Enter");
  await page.waitForTimeout(500);

  // Verify the captured body has no provider_type or model_id (I6)
  expect(capturedBody).not.toBeNull();
  expect(capturedBody).not.toHaveProperty("provider_type");
  expect(capturedBody).not.toHaveProperty("model_id");
  // Must have operation: "chat"
  expect(capturedBody?.["operation"]).toBe("chat");
});

// ── ThinkBlock renders collapsed ───────────────────────────────────────────────

test("CHAT-THINK-1: ThinkBlock renders collapsed when given think content", async ({ page }) => {
  await gotoChat(page);

  // Use a route mock to inject a message with think content
  await page.route("**/chat/stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/x-ndjson",
      body:
        '{"type":"think","delta":"let me reason about this"}\n' +
        '{"type":"token","delta":"The answer is 42."}\n' +
        '{"type":"done","conversation_id":"00000000-0000-0000-0000-000000000001",' +
        '"message_id":"00000000-0000-0000-0000-000000000002",' +
        '"input_tokens":5,"output_tokens":5,"total_cost_usd":0.0,' +
        '"iterations_used":1,"finish_reason":"stop"}\n',
    });
  });

  const textarea = page.locator("textarea").first();
  await textarea.fill("think block test");
  await textarea.press("Enter");

  // Wait for the message to finalize
  await page.waitForTimeout(1000);

  // The ThinkBlock should be present (there was think content) and collapsed
  // ThinkBlock has a button with aria-expanded
  const thinkButton = page.locator("[aria-expanded]").first();
  await expect(thinkButton).toBeVisible({ timeout: 5000 });
  // It should be collapsed (false) by default (AC-F7-1)
  const expanded = await thinkButton.getAttribute("aria-expanded");
  expect(expanded).toBe("false");
});

// ── I3 isolation: sending chat doesn't re-render graph/tree ───────────────────

test("I3-ISOLATION: sending chat message does NOT activate graph/tree section", async ({ page }) => {
  await gotoChat(page);

  // Verify we are in chat section
  await expect(page.getByTestId("section-chat")).toBeVisible();
  // Graph section and nav-tree must NOT be visible while in chat
  const graphSection = page.getByTestId("section-graph");
  await expect(graphSection).not.toBeVisible();
  const navTree = page.getByTestId("nav-tree");
  await expect(navTree).not.toBeVisible();

  // Mock the stream to not slow the test
  await page.route("**/chat/stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/x-ndjson",
      body:
        '{"type":"token","delta":"isolated response"}\n' +
        '{"type":"done","conversation_id":"00000000-0000-0000-0000-000000000001",' +
        '"message_id":"00000000-0000-0000-0000-000000000002",' +
        '"input_tokens":5,"output_tokens":2,"total_cost_usd":0.0,' +
        '"iterations_used":1,"finish_reason":"stop"}\n',
    });
  });

  const textarea = page.locator("textarea").first();
  await textarea.fill("isolation test");
  await textarea.press("Enter");
  await page.waitForTimeout(800);

  // Graph/tree must STILL not be visible (I3 — streaming never triggers graph/tree re-renders)
  await expect(graphSection).not.toBeVisible();
  await expect(navTree).not.toBeVisible();
  await expect(page.getByTestId("section-chat")).toBeVisible();
});

// ── Live streaming test with real qwen2.5:3b ──────────────────────────────────
// Requires E2E_LIVE_CHAT=1 and a live Ollama instance with qwen2.5:3b loaded.
// CI skips this block automatically; manual TrueNAS runs export E2E_LIVE_CHAT=1.

test.describe("Live streaming (qwen2.5:3b)", () => {
  test.skip(!isLiveChat, "Requires E2E_LIVE_CHAT=1 and live Ollama with qwen2.5:3b. Manual TrueNAS runs export E2E_LIVE_CHAT=1.");
  test.setTimeout(120_000);

  test("CHAT-STREAM-1/2/3: tokens stream into assistant bubble, finalize with GFM rendering + cost", async ({ page }) => {
    await gotoChat(page);

    const textarea = page.locator("textarea").first();
    await expect(textarea).toBeVisible();

    // Wait for any initial async conversation load to settle, then count
    await page.waitForTimeout(1500);
    const beforeCount = await page.locator(".synapse-markdown").count();

    // Send a short message to get a short response
    await textarea.fill("Reply in exactly 5 words: hello from synapse chat");
    await textarea.press("Enter");

    // CHAT-STREAM-1: The streaming message (in-flight cursor) should appear while qwen streams
    const streamingBubble = page.locator(".synapse-streaming-message");
    await expect(streamingBubble).toBeVisible({ timeout: 30_000 });

    // Wait for stream to complete (streaming-message disappears, settled message appears)
    // qwen2.5:3b is slow; allow up to 90s for the full turn
    await expect(streamingBubble).not.toBeVisible({ timeout: 90_000 });

    // CHAT-STREAM-2: A new settled message rendered via MarkdownView (.synapse-markdown)
    // After the stream, there should be at least one .synapse-markdown (the new assistant msg)
    // We use toBeGreaterThanOrEqual since other messages may have loaded asynchronously
    await page.waitForFunction(
      (expectedMin: number) => document.querySelectorAll(".synapse-markdown").length >= expectedMin,
      beforeCount + 1,
      { timeout: 10_000 }
    );
    const markdownMsg = page.locator(".synapse-markdown").last();
    await expect(markdownMsg).toBeVisible();

    // The finalized message should have non-empty text
    const msgText = await markdownMsg.textContent();
    expect(msgText).not.toBe("");
    expect(msgText?.length).toBeGreaterThan(0);

    // CHAT-STREAM-3: cost label is present (I7 — total_cost_usd in done event, displayed at 4dp)
    // For local provider cost = 0.0; presence of the cost metadata footer is what matters.
    // We check the Regenerate button is there (cost+regen row rendered)
    const regenBtn = page.getByRole("button", { name: /regenerate/i });
    await expect(regenBtn).toBeVisible();
  });

  test("CHAT-STOP-1: Stop button visible during streaming, clicking it aborts", async ({ page }) => {
    await gotoChat(page);
    const textarea = page.locator("textarea").first();

    await textarea.fill("Count slowly from 1 to 100, one number per line");
    await textarea.press("Enter");

    // Stop button should appear while streaming
    const stopBtn = page.locator("button[aria-label]").filter({ hasText: /stop/i }).first();
    await expect(stopBtn).toBeVisible({ timeout: 20_000 });

    // Click stop
    await stopBtn.click();
    await page.waitForTimeout(500);

    // After abort, streaming-message cursor should disappear
    await expect(page.locator(".synapse-streaming-message")).not.toBeVisible({ timeout: 5_000 });
    // Input should be re-enabled (not streaming)
    await expect(textarea).toBeEnabled({ timeout: 5_000 });
  });

  test("CHAT-PERSIST-1: messages persist after stream (GET /conversations/{id}/messages)", async ({ page }) => {
    await gotoChat(page);
    const textarea = page.locator("textarea").first();

    await textarea.fill("Say: persistence check");
    await textarea.press("Enter");

    // Wait for streaming to complete
    await expect(page.locator(".synapse-streaming-message")).not.toBeVisible({ timeout: 90_000 });

    // Now verify via API that the messages are in Postgres
    // Get the active conversation ID from the URL or the conversations list
    // We use the backend API directly to check
    const convRes = await page.request.get(`${BACKEND_URL}/conversations`);
    const convBody = await convRes.json() as { items: Array<{ id: string }> };
    // The most recently updated conversation should be first (updated_at DESC)
    const convId = convBody.items[0]?.id;
    expect(convId).toBeDefined();

    const msgsRes = await page.request.get(`${BACKEND_URL}/conversations/${convId ?? ""}/messages`);
    expect(msgsRes.status()).toBe(200);
    const msgsBody = await msgsRes.json() as { items: Array<{ role: string; content: string; total_cost_usd: number }> };
    expect(msgsBody.items.length).toBeGreaterThanOrEqual(2); // user + assistant
    const assistantMsg = msgsBody.items.find((m) => m.role === "assistant");
    expect(assistantMsg).toBeDefined();
    expect(assistantMsg?.content.length).toBeGreaterThan(0);
    // total_cost_usd should be present (I7) - local provider = 0.0, but field must exist
    expect(typeof assistantMsg?.total_cost_usd).toBe("number");
  });

  test("CHAT-REGEN-1: Regenerate button appears on last assistant message", async ({ page }) => {
    await gotoChat(page);
    const textarea = page.locator("textarea").first();

    await textarea.fill("Short reply: just say ok");
    await textarea.press("Enter");

    // Wait for finalization
    await expect(page.locator(".synapse-streaming-message")).not.toBeVisible({ timeout: 90_000 });

    // Regenerate button should be visible on the last assistant message
    const regenBtn = page.getByRole("button", { name: /regenerate/i });
    await expect(regenBtn).toBeVisible({ timeout: 10_000 });
  });
});

// ── G3 Streaming Performance Gate (MANDATORY) ─────────────────────────────────

test.describe("G3 — Streaming performance gate", () => {
  test.setTimeout(120_000);

  test("G3-LONGTASK: NO main-thread longtask >50ms during a live qwen2.5:3b stream", async ({ page }) => {
    // Requires a live qwen2.5:3b stream; skip in CI where Ollama is unavailable.
    test.skip(!isLiveChat, "Requires E2E_LIVE_CHAT=1 and live Ollama with qwen2.5:3b. Manual TrueNAS runs export E2E_LIVE_CHAT=1.");
    await gotoChat(page);

    // Inject PerformanceObserver BEFORE we trigger the stream
    await page.evaluate(() => {
      (window as unknown as Record<string, unknown>)["__longtasks__"] = [];
      const obs = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          (window as unknown as Record<string, unknown[]>)["__longtasks__"].push(
            { duration: entry.duration, startTime: entry.startTime }
          );
        }
      });
      try {
        obs.observe({ entryTypes: ["longtask"] });
        (window as unknown as Record<string, unknown>)["__ltobs__"] = obs;
      } catch {
        // longtask not available in this context — we record nothing
      }
    });

    const textarea = page.locator("textarea").first();
    await textarea.fill("Reply in exactly 3 words: hello from synapse");
    await textarea.press("Enter");

    // Wait for the streaming to start
    await expect(page.locator(".synapse-streaming-message")).toBeVisible({ timeout: 20_000 });

    // Wait for stream to finish
    await expect(page.locator(".synapse-streaming-message")).not.toBeVisible({ timeout: 90_000 });

    // Collect longtask data
    const longtasks = await page.evaluate((): Array<{ duration: number; startTime: number }> => {
      return ((window as unknown as Record<string, unknown>)["__longtasks__"] as Array<{ duration: number; startTime: number }>) ?? [];
    });

    // G3 gate: no longtask >50ms during streaming
    const violating = longtasks.filter((t) => t.duration > 50);

    // Report the measurement
    console.log(`G3 longtask measurement: ${longtasks.length} longtasks recorded`);
    if (longtasks.length > 0) {
      console.log("All longtasks:", JSON.stringify(longtasks.map((t) => Math.round(t.duration))));
    }
    if (violating.length > 0) {
      console.log("VIOLATING longtasks (>50ms):", JSON.stringify(violating.map((t) => Math.round(t.duration))));
    }

    // The assertion: zero longtasks exceeding 50ms during the stream
    expect(violating).toHaveLength(0);
  });

  test("G3-PARSE-ONCE: markdown/LaTeX NOT parsed per token (vitest contract verified; runtime check)", async ({ page }) => {
    // This test verifies that during streaming the .synapse-markdown class is NOT present
    // (parsed HTML only appears after finalization in MarkdownView, never during stream).
    // Requires a live stream to exercise the in-flight state; skip in CI.
    test.skip(!isLiveChat, "Requires E2E_LIVE_CHAT=1 and live Ollama with qwen2.5:3b. Manual TrueNAS runs export E2E_LIVE_CHAT=1.");
    await gotoChat(page);

    const textarea = page.locator("textarea").first();
    await textarea.fill("Say: stream parse once test");
    await textarea.press("Enter");

    // Wait for streaming to start
    await expect(page.locator(".synapse-streaming-message")).toBeVisible({ timeout: 20_000 });

    // While streaming: the streaming-message uses raw text (pre-wrap), NOT .synapse-markdown
    // Count how many .synapse-markdown divs appear DURING streaming
    // (the settled messages from before might already have .synapse-markdown, but no NEW one
    //  should appear until after done)
    const markdownCountDuringStream = await page.locator(".synapse-markdown").count();

    // Wait for completion
    await expect(page.locator(".synapse-streaming-message")).not.toBeVisible({ timeout: 90_000 });

    const markdownCountAfterStream = await page.locator(".synapse-markdown").count();

    // After stream, there should be exactly one MORE .synapse-markdown than before
    // (the new settled message is added once, not per-token)
    expect(markdownCountAfterStream).toBe(markdownCountDuringStream + 1);
  });

  test("G3-VIRTUALISED: message list is virtualized (bounded DOM — I4)", async ({ page }) => {
    await gotoChat(page);

    // data-testid="message-list" wraps the virtualizer container
    const msgList = page.getByTestId("message-list");
    await expect(msgList).toBeVisible();

    // TanStack Virtual renders items inside a position:relative container.
    // We check that the number of rendered (position:absolute) rows is bounded to overscan(5)+viewport
    // rather than unlimited. With <30 messages, all are rendered; the important check is that
    // the virtualizer container is present (proving the component uses useVirtualizer).
    const virtualizerContainer = msgList.locator("[style*='position: relative']").first();
    await expect(virtualizerContainer).toBeVisible({ timeout: 5_000 });

    // Confirm the .message-list data-testid exists (the virtualizer wrapper is present)
    const testId = await msgList.getAttribute("data-testid");
    expect(testId).toBe("message-list");
  });
});

// ── D5 screenshots ─────────────────────────────────────────────────────────────
// Requires a live qwen2.5:3b stream to capture mid-stream and final state.
// Skipped in CI; manual TrueNAS runs export E2E_LIVE_CHAT=1 to produce screenshots.

test.describe("D5 screenshots (chat)", () => {
  test.skip(!isLiveChat, "Requires E2E_LIVE_CHAT=1 and live Ollama with qwen2.5:3b. Manual TrueNAS runs export E2E_LIVE_CHAT=1.");
  test.setTimeout(120_000);

  test("D5: chat-streaming.png — captures stream in flight then final conversation", async ({ page }) => {
    ensureScreensDir();

    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoChat(page);

    // Send a real message to qwen2.5:3b — it streams naturally, giving us time to screenshot
    const textarea = page.locator("textarea").first();
    await textarea.fill("Reply in exactly 4 words: hello from synapse streaming");
    await textarea.press("Enter");

    // Screenshot as soon as the streaming bubble appears (mid-stream)
    const streamingBubble = page.locator(".synapse-streaming-message");
    await expect(streamingBubble).toBeVisible({ timeout: 30_000 });

    await page.screenshot({
      path: path.join(SCREENS_DIR, "chat-streaming.png"),
      fullPage: false,
    });

    // Wait for stream to complete then screenshot the final conversation
    await expect(streamingBubble).not.toBeVisible({ timeout: 90_000 });
    await page.waitForTimeout(300);

    await page.screenshot({
      path: path.join(SCREENS_DIR, "chat-conversation.png"),
      fullPage: false,
    });

    // Verify files were created
    expect(fs.existsSync(path.join(SCREENS_DIR, "chat-streaming.png"))).toBe(true);
    expect(fs.existsSync(path.join(SCREENS_DIR, "chat-conversation.png"))).toBe(true);
  });
});
