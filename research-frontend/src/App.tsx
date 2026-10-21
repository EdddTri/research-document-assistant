import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowDown, ArrowRight, ArrowUp, BookOpen, Check, ChevronRight, FileText, Layers,
  LogOut, MessageSquare, Plus, RefreshCw, Search, Settings, ShieldCheck, Trash2, Upload, X } from "lucide-react";
import { api, clearIdentity, refresh, request, signIn, signOut } from "./api";
import type { Answer, Citation, Collection, Conversation, LoginSession, Message, Paper, User } from "./types";

const errorText = (error: unknown) => error instanceof Error ? error.message : "Something went wrong.";
const isAbort = (error: unknown) => error instanceof DOMException && error.name === "AbortError";
// Matches ChatInput.document_ids in the backend; this does not limit collection size.
const MAX_CONTEXT_PAPERS = 20;

function Brand() {
  return <div className="brand"><span className="brand-mark"><BookOpen size={23} strokeWidth={1.5} /></span>margin<span className="brand-dot">.</span></div>;
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [register, setRegister] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { onLogin(await signIn(email, password, register)); }
    catch (error) { if (!isAbort(error)) setError(errorText(error)); }
    finally { setBusy(false); }
  }
  return <div className="login">
    <div className="login-story"><Brand /><div><span className="eyebrow">YOUR RESEARCH, IN CONVERSATION</span>
      <h1>Find the answer.<br /><em>Keep the evidence.</em></h1>
      <p>A quieter place to explore your papers, connect ideas, and follow every answer back to its source.</p>
      <div className="specimen"><span>01 / A BETTER READING COMPANION</span><div className="specimen-line" /><BookOpen size={58} strokeWidth={.8} />
        <p>From a question in the margin<br />to a clearer understanding.</p><div className="specimen-line" /><small>TEXT · TABLES · FIGURES</small></div>
    </div><small>Built for careful questions and verifiable answers.</small></div>
    <main className="login-form"><div className="login-box"><span className="eyebrow">RESEARCH WORKSPACE</span>
      <h2>{register ? "Make room for discovery." : "Welcome back."}</h2>
      <p>{register ? "Create a reader account to explore the shared collection." : "Sign in to pick up where you left off."}</p>
      <form onSubmit={submit}>
        <label>Email address<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@university.edu" /></label>
        <label>Password<input type="password" minLength={6} maxLength={256} autoComplete={register ? "new-password" : "current-password"} required value={password} onChange={e => setPassword(e.target.value)} placeholder="At least 6 characters" /></label>
        {error && <div className="error" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? "Connecting…" : register ? "Create account" : "Open workspace"}<ArrowRight size={17} /></button>
      </form>
      <p className="account-toggle">{register ? "Already have an account?" : "New to Margin?"} <button onClick={() => { setRegister(!register); setError(""); }}>{register ? "Sign in" : "Create an account"}</button></p>
      <div className="login-note"><ShieldCheck size={16} />Private conversations. A shared research collection.</div>
    </div></main>
  </div>;
}

function ProtectedImage({ path, caption }: { path: string; caption: string }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true, objectUrl = "";
    request(path).then(r => r.blob()).then(blob => { if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } })
      .catch(e => { if (active && !isAbort(e)) setError("Source image is no longer available."); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [path]);
  return <figure>{url ? <img src={url} alt={caption || "Original research figure"} /> : <p>{error || "Loading original image…"}</p>}
    {caption && <figcaption>{caption}</figcaption>}</figure>;
}

function AnswerView({ answer, inspect }: { answer: Partial<Answer>; inspect: (citation: Citation) => void }) {
  const citations = answer.citations || [];
  return <><div className="answer-label"><span className="mini-mark"><BookOpen size={15} /></span>MARGIN <span className={`answer-state ${answer.answerability}`}>{answer.answerability === "full" ? "Supported answer" : answer.answerability === "partial" ? "Partial evidence" : "Insufficient evidence"}</span></div>
    {answer.blocks?.map((block, index) => <div key={index} className={`answer-block ${block.type}`}>
      {block.type === "text" && <p>{block.text} <span className="inline-citations">{block.evidence_ids?.map(id => {
        const at = citations.findIndex(c => c.evidence_id === id);
        return at >= 0 ? <button key={id} title={`View ${citations[at].title}, page ${citations[at].page}`} onClick={() => inspect(citations[at])}>{at + 1}</button> : null;
      })}</span></p>}
      {block.type === "limitation" && <p><ShieldCheck size={17} />{block.text}</p>}
      {block.type === "table" && <div className="table-scroll"><table><caption>{block.text || "Extracted source table"}</caption><thead><tr>{block.headers?.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>{block.rows?.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell ?? <em>Unreadable</em>}</td>)}</tr>)}</tbody></table></div>}
      {block.type === "image" && block.image_url && <ProtectedImage path={block.image_url} caption={block.text || ""} />}
    </div>)}
    {citations.length > 0 && <div className="sources"><span className="eyebrow">SOURCES</span>{citations.map((citation, i) => <button key={citation.evidence_id} onClick={() => inspect(citation)}>
      <span className="source-number">{i + 1}</span><span>{citation.title}<small>Page {citation.page} · version {citation.version}</small></span><ChevronRight size={16} /></button>)}</div>}
    {answer.usage && <p className="answer-usage" title="Estimated OpenAI cost for this question, including follow-up resolution and answer attempts. Excludes document ingestion and local computing costs.">
      {answer.usage.estimated_cost_usd != null ? `Estimated API cost $${answer.usage.estimated_cost_usd.toFixed(6)}` : "API cost unavailable"}
      {` · ${answer.usage.input_tokens + answer.usage.output_tokens} ${answer.usage.usage_complete ? "tokens" : "known tokens"} · ${answer.usage.calls?.length ?? 0} model calls`}
    </p>}
  </>;
}

function Workspace({ user }: { user: User }) {
  const [collection, setCollection] = useState<Collection>({ documents: [], used_pages: 0 });
  const [selected, setSelected] = useState<string[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [uploads, setUploads] = useState<{ name: string; status: string; completed?: boolean }[]>([]);
  const [uploadTimes, setUploadTimes] = useState<Record<string, string>>({});
  const [uploading, setUploading] = useState(false);
  const uploadLock = useRef(false);
  const [source, setSource] = useState<{ citation: Citation; url: string } | null>(null);
  const [sessions, setSessions] = useState<LoginSession[] | null>(null);
  const [sidebar, setSidebar] = useState(false);
  const mounted = useRef(true);
  const viewVersion = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const replacementInput = useRef<HTMLInputElement>(null);
  const replacementTarget = useRef("");
  const bottom = useRef<HTMLDivElement>(null);
  const ready = collection.documents.filter(d => d.status === "ready");
  const isAdmin = user.role === "admin";
  const pendingUploads = uploads.filter(file => !file.completed);
  const report = (e: unknown) => { if (mounted.current && !isAbort(e)) setError(errorText(e)); };

  async function load() {
    const [papers, chats] = await Promise.all([api<Collection>("/documents"), api<Conversation[]>("/conversations")]);
    if (!mounted.current) return;
    setCollection(papers); setConversations(chats);
    setSelected(ids => ids.filter(id => papers.documents.some(p => p.id === id && p.status === "ready")));
  }
  useEffect(() => {
    mounted.current = true;
    load().catch(report);
    const timer = window.setInterval(() => { load().catch(report); }, 5000);
    return () => { mounted.current = false; viewVersion.current++; window.clearInterval(timer); };
  }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, busy]);
  useEffect(() => () => { if (source) URL.revokeObjectURL(source.url); }, [source]);
  useEffect(() => {
    if (!source && !sessions) return;
    const previous = document.activeElement as HTMLElement | null;
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setSource(null); setSessions(null); }
      if (event.key === "Tab") {
        const nodes = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"] button, [role="dialog"] a, [role="dialog"] iframe'));
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.removeEventListener("keydown", keydown); previous?.focus(); };
  }, [source, sessions]);

  async function openConversation(id: string) {
    const version = ++viewVersion.current;
    setLoadingConversation(true); setConversationId(""); setMessages([]); setQuestion("");
    setBusy(false); setSidebar(false); setError("");
    try {
      const data = await api<{ messages: Message[] }>(`/conversations/${id}`);
      if (mounted.current && viewVersion.current === version) { setConversationId(id); setMessages(data.messages); }
    } catch (error) { if (viewVersion.current === version) report(error); }
    finally { if (mounted.current && viewVersion.current === version) setLoadingConversation(false); }
  }
  function newConversation() {
    viewVersion.current++; setConversationId(""); setMessages([]); setQuestion(""); setBusy(false); setLoadingConversation(false); setError(""); setSidebar(false);
  }
  async function ask(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || !selected.length || busy || loadingConversation) return;
    const version = ++viewVersion.current;
    const text = question.trim();
    setBusy(true); setError(""); setQuestion("");
    setMessages(items => [...items, { id: crypto.randomUUID(), role: "user", content: { text } }]);
    try {
      let id = conversationId;
      if (!id) {
        const chat = await api<Conversation>("/conversations", { method: "POST" });
        id = chat.id;
        if (viewVersion.current === version) setConversationId(id);
      }
      const answer = await api<Answer>("/chat", { method: "POST", body: JSON.stringify({ conversation_id: id, question: text, document_ids: selected }) });
      if (mounted.current && viewVersion.current === version) setMessages(items => [...items, { id: crypto.randomUUID(), role: "assistant", content: answer }]);
      // A sidebar refresh failure must not roll back an answer already saved by the API.
      await load().catch(report);
    } catch (error) {
      if (viewVersion.current === version) {
        report(error); setQuestion(text);
        setMessages(items => items.slice(0, -1));
      }
    } finally { if (mounted.current && viewVersion.current === version) setBusy(false); }
  }
  async function upload(files: FileList | null, replacesId?: string) {
    if (!files?.length || uploadLock.current) return;
    uploadLock.current = true; setUploading(true);
    const list = Array.from(files);
    setUploads(list.map(f => ({ name: f.name, status: "Waiting" })));
    let cursor = 0;
    function update(index: number, status: string, completed = false) { if (mounted.current) setUploads(items => items.map((item, i) => i === index ? { ...item, status, completed } : item)); }
    await Promise.all(Array.from({ length: Math.min(3, list.length) }, async () => {
      while (cursor < list.length && mounted.current) {
        const index = cursor++, file = list[index];
        update(index, "Uploading");
        const data = new FormData(); data.append("file", file);
        const started = performance.now();
        const elapsed = () => ((performance.now() - started) / 1000).toFixed(1);
        try {
          const paper = await api<Paper>(`/documents${replacesId ? `?replaces_id=${encodeURIComponent(replacesId)}` : ""}`, { method: "POST", body: data });
          const seconds = elapsed();
          if (mounted.current) setUploadTimes(times => ({ ...times, [paper.id]: seconds }));
          update(index, `Uploaded in ${seconds}s`, true);
          await load().catch(report);
        }
        catch (error) { update(index, `Failed after ${elapsed()}s · ${errorText(error)}`); }
      }
    }));
    await load().catch(report);
    if (fileInput.current) fileInput.current.value = "";
    if (replacementInput.current) replacementInput.current.value = "";
    uploadLock.current = false;
    if (mounted.current) setUploading(false);
  }
  async function inspect(citation: Citation) {
    const version = viewVersion.current;
    try {
      const response = await request(citation.source_url.split("#")[0]);
      const blob = await response.blob();
      if (mounted.current && viewVersion.current === version) setSource({ citation, url: URL.createObjectURL(blob) });
    } catch (error) { report(error); }
  }
  async function documentAction(id: string, action: "retry" | "reprocess" | "delete") {
    try {
      await api(`/documents/${id}${action === "delete" ? "" : `/${action}`}`, { method: action === "delete" ? "DELETE" : "POST" });
      await load();
    } catch (error) { report(error); }
  }
  return <div className="workspace">
    <aside className={`sidebar ${sidebar ? "mobile-open" : ""}`}>
      <div className="sidebar-brand"><Brand /><button className="icon-button mobile-close" aria-label="Close navigation" onClick={() => setSidebar(false)}><X size={20} /></button></div>
      <button className="new-chat" onClick={newConversation}><Plus size={18} />New conversation<span>↗</span></button>
      <div className="section-label collection-heading"><span>RESEARCH COLLECTION</span><span>{collection.documents.length} {collection.documents.length === 1 ? "paper" : "papers"} · {collection.used_pages} {collection.used_pages === 1 ? "page" : "pages"}</span></div>
      <div className="search-box"><Search size={15} /><input aria-label="Find a paper" placeholder="Find a paper…" value={query} onChange={e => setQuery(e.target.value)} /></div>
      <div className="select-row"><span>{selected.length} selected{ready.length > MAX_CONTEXT_PAPERS ? ` (max ${MAX_CONTEXT_PAPERS})` : ""}</span><button onClick={() => setSelected(selected.length === Math.min(ready.length, MAX_CONTEXT_PAPERS) ? [] : ready.slice(0, MAX_CONTEXT_PAPERS).map(p => p.id))}>{selected.length === Math.min(ready.length, MAX_CONTEXT_PAPERS) && ready.length ? "Clear" : "Select ready"}</button></div>
      <div className="paper-list">
        {collection.documents.filter(p => p.title.toLowerCase().includes(query.toLowerCase())).map(paper => <div className={`paper ${selected.includes(paper.id) ? "selected" : ""}`} key={paper.id}>
          <label><input type="checkbox" disabled={paper.status !== "ready" || (!selected.includes(paper.id) && selected.length >= MAX_CONTEXT_PAPERS)} checked={selected.includes(paper.id)} onChange={e => setSelected(ids => e.target.checked ? (ids.length < MAX_CONTEXT_PAPERS ? [...ids, paper.id] : ids) : ids.filter(id => id !== paper.id))} />
            <FileText size={18} /><span className="paper-title">{paper.title}<small>{paper.page_count} {paper.page_count === 1 ? "page" : "pages"} <span>·</span> {paper.status === "ready" ? "Ready" : paper.status === "failed" ? "Indexing failed" : paper.status === "queued" ? "Queued for indexing" : paper.job?.stage || paper.status}</small>
              {uploadTimes[paper.id] && <small>Uploaded in {uploadTimes[paper.id]}s</small>}</span></label>
          {isAdmin && <div className="paper-actions">
            {paper.can_retry && <button onClick={() => documentAction(paper.id, "retry")} title={paper.job?.error || "Retry"}>Retry</button>}
            {paper.status === "ready" && <button onClick={() => documentAction(paper.id, "reprocess")} title="Reprocess as a new version"><RefreshCw size={12} />Reprocess</button>}
            {paper.status === "ready" && <button disabled={uploading} onClick={() => { replacementTarget.current = paper.id; replacementInput.current?.click(); }}>Replace</button>}
            <button aria-label={`Delete ${paper.title}`} onClick={() => documentAction(paper.id, "delete")}><Trash2 size={12} /></button>
          </div>}
          {isAdmin && paper.job?.error && <small className="paper-error">{paper.job.error}</small>}
        </div>)}
        {!collection.documents.length && <div className="empty-papers"><Layers size={28} strokeWidth={1} /><p>Your collection starts here.</p><small>{isAdmin ? "Upload a PDF to begin exploring." : "An administrator can add papers to the collection."}</small></div>}
      </div>
      {isAdmin && <><input ref={fileInput} type="file" accept="application/pdf,.pdf" multiple hidden onChange={e => upload(e.target.files)} />
        <input ref={replacementInput} type="file" accept="application/pdf,.pdf" hidden onChange={e => upload(e.target.files, replacementTarget.current)} />
        <button className="upload-button" disabled={uploading} onClick={() => fileInput.current?.click()}><Upload size={16} />{uploading ? "Uploading papers…" : "Upload papers"}</button></>}
      {pendingUploads.length > 0 && <div className="upload-list" aria-live="polite">{pendingUploads.map((file, i) => <div key={i}><span>{file.name}</span><small>{file.status}</small></div>)}</div>}
      <div className="section-label history-label"><span>YOUR CONVERSATIONS</span><MessageSquare size={13} /></div>
      <nav className="history">{conversations.map(c => <div className={c.id === conversationId ? "active" : ""} key={c.id}><button onClick={() => openConversation(c.id)}>{c.title}</button>
        <button className="delete-chat" aria-label={`Delete conversation ${c.title}`} onClick={async () => { try { await api(`/conversations/${c.id}`, { method: "DELETE" }); if (c.id === conversationId) newConversation(); await load(); } catch (e) { report(e); } }}><X size={12} /></button></div>)}
        {!conversations.length && <p>Your conversations will appear here.</p>}</nav>
      <div className="account"><span className="avatar">{user.email[0].toUpperCase()}</span><span className="account-info">{user.email}<small>{isAdmin ? "Administrator" : "Research reader"}</small></span>
        <button className="icon-button" aria-label="Manage login sessions" onClick={() => api<LoginSession[]>("/auth/sessions").then(setSessions).catch(report)}><Settings size={16} /></button>
        <button className="icon-button" aria-label="Sign out or switch account" onClick={() => signOut().catch(report)}><LogOut size={16} /></button></div>
    </aside>
    <main className="main-panel">
      <header className="topbar"><div><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setSidebar(true)}><Layers size={20} /></button><span>Workspace</span><ChevronRight size={13} /><strong>Research desk</strong></div><span className="private-badge"><span />Private conversation</span></header>
      <div className="chat-scroll">
        {loadingConversation ? <section className="message-list"><p role="status">Loading conversation…</p></section> : !messages.length ? <section className="welcome"><span className="eyebrow"><span className="tiny-rule" />A LITTLE CURIOSITY GOES A LONG WAY</span>
          <h1>What will you<br /><em>discover today?</em></h1>
          <p>Ask a question. Connect ideas across papers.<br />Find answers with the evidence right beside them.</p>
          <div className="starter-grid">{[
            ["Understand the essentials", "What are the main findings and limitations?", BookOpen],
            ["Connect the dots", "How do the methods in these papers compare?", Layers],
            ["Look a little closer", "Explain the key figures and what they show.", Search],
          ].map(([title, prompt, Icon]) => { const Symbol = Icon as typeof BookOpen; return <button key={String(title)} onClick={() => setQuestion(String(prompt))}><Symbol size={20} strokeWidth={1.5} /><strong>{String(title)}</strong><span>{String(prompt)}</span><ArrowRight size={16} /></button>; })}</div>
          <div className="evidence-note"><ShieldCheck size={16} /><span>Grounded in your selected papers. Honest about what’s missing.</span></div>
        </section> : <section className="message-list">{messages.map(message => <article key={message.id} className={`message ${message.role}`}>
          {message.role === "user" ? <><span className="eyebrow">YOU ASKED</span><h2>{message.content.text}</h2></> : <AnswerView answer={message.content} inspect={inspect} />}
        </article>)}
          {busy && <div className="thinking" role="status"><span /><span /><span />Reading the selected evidence…</div>}<div ref={bottom} /></section>}
      </div>
      <div className="composer-area">
        {error && <div className="error dismissible" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError("")}><X size={16} /></button></div>}
        <form className="composer" onSubmit={ask}><textarea aria-label="Ask a research question" disabled={loadingConversation} maxLength={4000} placeholder={selected.length ? "Ask anything about your selected papers…" : "Select ready papers in the sidebar to get started…"} value={question} onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} rows={2} />
          <div className="composer-footer"><span><FileText size={14} />{selected.length ? `${selected.length} ${selected.length === 1 ? "paper" : "papers"} in context` : "No papers selected"}</span><button type="submit" aria-label="Send question" disabled={busy || loadingConversation || !selected.length || !question.trim()}><ArrowUp size={19} /></button></div>
        </form><p className="composer-hint">Check cited sources for important details.<span>Enter to send · Shift + Enter for a new line</span></p>
      </div>
    </main>
    {source && <div className="modal-backdrop" onClick={() => setSource(null)}><section className="source-modal" role="dialog" aria-modal="true" aria-label="Original PDF source" onClick={e => e.stopPropagation()}>
      <header><div><span className="eyebrow">ORIGINAL SOURCE · PAGE {source.citation.page}</span><h3>{source.citation.title}</h3></div><button autoFocus aria-label="Close source" onClick={() => setSource(null)}><X size={20} /></button></header>
      <iframe title={`Source: ${source.citation.title}`} src={`${source.url}#page=${source.citation.page}`} /><footer><a href={source.url} download={`${source.citation.title}.pdf`}><ArrowDown size={14} />Download authorized PDF</a></footer>
    </section></div>}
    {sessions && <div className="modal-backdrop" onClick={() => setSessions(null)}><section className="sessions-modal" role="dialog" aria-modal="true" aria-label="Login sessions" onClick={e => e.stopPropagation()}>
      <header><h2>Your login sessions</h2><button autoFocus aria-label="Close sessions" onClick={() => setSessions(null)}><X size={20} /></button></header>
      {sessions.map(s => <div className="session" key={s.id}><span><strong>{s.current ? "This browser" : "Another device"}</strong><small>{s.user_agent || "Unknown browser"}</small><small>Signed in {new Date(s.created_at * 1000).toLocaleString()}</small></span>
        {s.current ? <Check size={16} /> : <button onClick={async () => { try { await api(`/auth/sessions/${s.id}`, { method: "DELETE" }); setSessions(items => items?.filter(item => item.id !== s.id) || []); } catch (e) { report(e); } }}>Revoke</button>}</div>)}
      <button className="secondary" onClick={async () => { try { await api("/auth/logout-all", { method: "POST" }); clearIdentity(); } catch (e) { report(e); } }}>Sign out on all devices</button>
    </section></div>}
  </div>;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    const clear = () => { setUser(null); setLoading(false); };
    window.addEventListener("research-identity-cleared", clear);
    refresh().then(user => { if (active) setUser(user); }).catch(() => {}).finally(() => { if (active) setLoading(false); });
    return () => { active = false; window.removeEventListener("research-identity-cleared", clear); };
  }, []);
  if (loading) return <div className="boot"><Brand /><p>Opening your research desk…</p></div>;
  return user ? <Workspace key={user.id} user={user} /> : <Login onLogin={setUser} />;
}
