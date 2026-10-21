import { expect, test, type Page } from "@playwright/test";

const backend = "http://localhost:18010";
const password = "test-password-123";

async function login(page: Page, role = "admin", papers = 2) {
  await page.goto("/");
  await page.getByLabel("Email address").fill(`${role}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(page.getByRole("button", { name: "New conversation" })).toBeVisible();
  await expect(page.locator(".paper")).toHaveCount(papers);
}

async function ask(page: Page, question = "How many participants were included?") {
  await page.getByLabel("Ask a research question").fill(question);
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".message.assistant").last()).toBeVisible();
  await expect(page.getByLabel("Ask a research question")).toHaveValue("");
}

test.beforeEach(async ({ request }) => {
  expect((await request.post(`${backend}/__test__/reset`)).ok()).toBeTruthy();
});

test("login errors, reader restrictions, reload and logout", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("reader@example.com");
  await page.getByLabel("Password", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(page.locator(".paper")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Upload papers" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Send question" })).toBeDisabled();
  await page.reload();
  await expect(page.getByText("Research reader", { exact: true })).toBeVisible();
  await page.getByLabel("Sign out or switch account").click();
  await expect(page.getByRole("button", { name: "Open workspace" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Open workspace" })).toBeVisible();
});

test("registration creates a reader and duplicate email shows an error", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Create an account" }).click();
  await page.getByLabel("Email address").fill("reader@example.com");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("already exists");
  await page.getByLabel("Email address").fill("new-reader@example.com");
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page.getByText("Research reader", { exact: true })).toBeVisible();
});

test("selected PDF chat renders a protected figure, citation and saved history", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await login(page);
  await page.locator(".paper input[type=checkbox]").first().check();
  await ask(page);
  await expect(page.getByText("The study included 42 participants.", { exact: false })).toBeVisible();
  await expect(page.getByAltText("Study flow")).toBeVisible();
  expect(await page.getByAltText("Study flow").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await page.locator(".sources button").first().click();
  await expect(page.getByRole("dialog", { name: "Original PDF source" })).toBeVisible();
  await expect(page.locator(".source-modal iframe")).toHaveAttribute("src", /#page=1$/);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.reload();
  await page.locator(".history>div>button:first-child").first().click();
  await expect(page.locator(".message.assistant")).toHaveCount(1);
  const citation = await page.locator(".sources button").first().textContent();
  expect(citation).toContain("Study 1.pdf");
  expect((await request.get(`${backend}/documents`)).status()).toBe(401);
  expect(errors).toEqual([]);
});

test("upload, duplicate/invalid PDF errors, reprocess and delete", async ({ page, request }) => {
  await login(page);
  const pdf = await (await request.get(`${backend}/__test__/pdf`)).body();
  const input = page.locator('input[type="file"][multiple]');
  await input.setInputFiles({ name: "Browser upload.pdf", mimeType: "application/pdf", buffer: pdf });
  const paper = page.locator(".paper").filter({ hasText: "Browser upload.pdf" });
  await expect(paper).toContainText("Ready");
  await expect(paper).toContainText("Uploaded in");
  await expect(page.locator(".upload-list")).toHaveCount(0);
  await input.setInputFiles({ name: "Duplicate.pdf", mimeType: "application/pdf", buffer: pdf });
  await expect(page.locator(".upload-list")).toContainText("already exists");
  await input.setInputFiles({ name: "Invalid.pdf", mimeType: "application/pdf", buffer: Buffer.from("not a PDF") });
  await expect(page.locator(".upload-list")).toContainText("valid PDF");
  await paper.getByRole("button", { name: "Reprocess" }).click();
  // Reprocessing replaces the document ID; wait for that new version before deleting.
  await expect(paper).not.toContainText("Uploaded in");
  await expect(paper).toHaveCount(1);
  await expect(paper).toContainText("Ready");
  await paper.getByRole("button", { name: "Delete Browser upload.pdf" }).click();
  await expect(paper).toHaveCount(0);
});

test("unsupported questions show insufficient evidence", async ({ page }) => {
  await login(page);
  await page.locator(".paper input[type=checkbox]").first().check();
  await ask(page, "What unavailable information is provided?");
  await expect(page.getByText("Insufficient evidence", { exact: true })).toBeVisible();
  await expect(page.locator(".answer-block.limitation")).toContainText("do not provide");
});

test("successful answers survive a later sidebar refresh failure", async ({ page }) => {
  await login(page);
  await page.locator(".paper input[type=checkbox]").first().check();
  let answered = false;
  await page.route(`${backend}/chat`, async route => {
    const response = await route.fetch();
    answered = true;
    await route.fulfill({ response });
  });
  await page.route(`${backend}/conversations`, async route => {
    if (answered && route.request().method() === "GET") {
      await route.fulfill({ status: 503, json: { detail: "Sidebar temporarily unavailable" } });
    } else await route.continue();
  });
  await page.getByLabel("Ask a research question").fill("How many participants?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.getByRole("alert")).toContainText("Sidebar temporarily unavailable");
  await expect(page.locator(".message.assistant")).toHaveCount(1);
  await expect(page.locator(".message.user")).toHaveCount(1);
  await expect(page.getByLabel("Ask a research question")).toHaveValue("");
});

test("chat failure restores the draft without removing previous messages", async ({ page }) => {
  await login(page);
  await page.locator(".paper input[type=checkbox]").first().check();
  await ask(page);
  await page.route(`${backend}/chat`, route => route.fulfill({ status: 502, json: { detail: "Please retry" } }));
  await page.getByLabel("Ask a research question").fill("A second question");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.getByRole("alert")).toContainText("Please retry");
  await expect(page.getByLabel("Ask a research question")).toHaveValue("A second question");
  await expect(page.locator(".message.user")).toHaveCount(1);
  await expect(page.locator(".message.assistant")).toHaveCount(1);
});

test("conversation navigation prevents submitting into the previous conversation", async ({ page }) => {
  await login(page);
  await page.locator(".paper input[type=checkbox]").first().check();
  await ask(page, "First conversation question");
  await page.getByRole("button", { name: "New conversation" }).click();
  await ask(page, "Second conversation question");
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let started!: () => void;
  const waiting = new Promise<void>(resolve => { started = resolve; });
  await page.route(`${backend}/conversations/*`, async route => {
    started();
    await gate;
    await route.continue();
  });
  await page.locator(".history").getByRole("button", { name: "First conversation question", exact: true }).click();
  await waiting;
  try {
    await expect(page.getByLabel("Ask a research question")).toBeDisabled();
    await expect(page.getByRole("button", { name: "Send question" })).toBeDisabled();
  } finally { release(); }
  await expect(page.locator(".message.user")).toHaveText(/First conversation question/);
  await expect(page.getByLabel("Ask a research question")).toBeEnabled();
});

test("large collections only select the number of papers the chat API accepts", async ({ page, request }) => {
  expect((await request.post(`${backend}/__test__/reset?papers=21`)).ok()).toBeTruthy();
  await login(page, "admin", 21);
  await page.getByRole("button", { name: "Select ready" }).click();
  await expect(page.locator(".paper input:checked")).toHaveCount(20);
  await expect(page.locator(".paper input:not(:checked)")).toBeDisabled();
  await ask(page);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.locator(".paper input:checked").first().uncheck();
  await expect(page.locator(".paper input:not(:checked)").last()).toBeEnabled();
  await page.locator(".paper input:not(:checked)").last().check();
  await expect(page.locator(".paper input:checked")).toHaveCount(20);
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await expect(page.locator(".paper input:checked")).toHaveCount(0);
});

test("expired access tokens refresh and retry without losing the workspace", async ({ page }) => {
  await login(page);
  let expired = true;
  await page.route(`${backend}/auth/sessions`, async route => {
    if (expired) {
      expired = false;
      await route.fulfill({ status: 401, json: { detail: "Token expired" } });
    } else await route.continue();
  });
  const refreshed = page.waitForResponse(response => response.url() === `${backend}/auth/refresh` && response.status() === 200);
  await page.getByLabel("Manage login sessions").click();
  await refreshed;
  await expect(page.getByRole("dialog", { name: "Login sessions" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("signing out clears another open tab", async ({ page, context }) => {
  await login(page);
  const other = await context.newPage();
  await other.goto("/");
  await expect(other.getByRole("button", { name: "New conversation" })).toBeVisible();
  await page.getByLabel("Sign out or switch account").click();
  await expect(page.getByRole("button", { name: "Open workspace" })).toBeVisible();
  await expect(other.getByRole("button", { name: "Open workspace" })).toBeVisible();
});

test("late answers do not populate a new conversation", async ({ page }) => {
  await login(page);
  await page.locator(".paper input[type=checkbox]").first().check();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let started!: () => void;
  const waiting = new Promise<void>(resolve => { started = resolve; });
  await page.route(`${backend}/chat`, async route => {
    const response = await route.fetch();
    started();
    await gate;
    await route.fulfill({ response });
  });
  await page.getByLabel("Ask a research question").fill("Slow question");
  await page.getByRole("button", { name: "Send question" }).click();
  await waiting;
  await page.getByRole("button", { name: "New conversation" }).click();
  const complete = page.waitForResponse(`${backend}/chat`);
  release();
  await complete;
  await expect(page.locator(".message")).toHaveCount(0);
  await expect(page.getByLabel("Ask a research question")).toHaveValue("");
});

test("sessions dialog and sign out on all devices", async ({ page }) => {
  await login(page);
  await page.getByLabel("Manage login sessions").click();
  await expect(page.getByRole("dialog", { name: "Login sessions" })).toBeVisible();
  await expect(page.getByText("This browser", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Sign out on all devices" }).click();
  await expect(page.getByRole("button", { name: "Open workspace" })).toBeVisible();
});

test("mobile navigation and checkbox sizes remain usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@example.com");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Open workspace" }).click();
  await page.getByLabel("Open navigation").click();
  await expect(page.getByLabel("Close navigation")).toBeVisible();
  const sizes = await page.locator(".paper input[type=checkbox]").evaluateAll(inputs => inputs.map(input => {
    const rect = input.getBoundingClientRect(); return [rect.width, rect.height];
  }));
  expect(sizes).toEqual([[16, 16], [16, 16]]);
  await page.locator(".paper input[type=checkbox]").first().check();
  await page.getByLabel("Close navigation").click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "test-results/mobile-workspace.png", fullPage: true });
});
