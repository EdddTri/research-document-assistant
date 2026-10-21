export type User = { id: string; email: string; role: "reader" | "admin" };
export type Paper = { id: string; title: string; version: number; page_count: number; status: string; can_retry: boolean;
  job: { id: string; status: string; stage: string; error: string | null } | null };
export type Collection = { documents: Paper[]; used_pages: number };
export type Conversation = { id: string; title: string };
// Answer contracts are generated from the public backend OpenAPI schema.
export type { components } from "./generated-api";
import type { components } from "./generated-api";
export type Citation = components["schemas"]["Citation"];
export type Block = components["schemas"]["Block"];
export type Answer = components["schemas"]["Answer"];
export type Message = { id: string; role: "user" | "assistant"; content: { text?: string } & Partial<Answer> };
export type LoginSession = { id: string; created_at: number; user_agent: string; current: boolean };
