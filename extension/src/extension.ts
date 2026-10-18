import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { expandHome, isAllowed } from './allowlist';
import { Command, DaemonClient } from './daemonClient';
import { TutorPanel } from './panel';
import { redact } from './redact';

// Positions are 1-based (line and col) on the wire; see README "Contract addenda".
type Pos = { line: number; col: number };
interface TutorEvent {
  type: 'edit' | 'focus' | 'cursor' | 'diagnostics' | 'test_run' | 'file_open';
  ts: number; file: string; language: string; student_id: string;
  content?: string; cursor?: Pos | null; selection?: { start: Pos; end: Pos } | null;
  diagnostics?: { line: number; severity: string; message: string }[];
  action?: 'open' | 'close';
}

const SEVERITY = ['error', 'warning', 'info', 'hint'];
const cfg = () => vscode.workspace.getConfiguration('tutor');
const folders = () => {
  const configured = cfg().get<string[]>('allowlistedFolders', ['~/tutor-lessons']);
  const ws = (vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath);
  const wsLessons = ws.map((f) => path.join(f, 'lessons'));
  return [...configured, ...ws, ...wsLessons];
};

export function activate(ctx: vscode.ExtensionContext): void {
  const out = vscode.window.createOutputChannel('Tutor');
  const log = (m: string) => out.appendLine(`[${new Date().toISOString()}] ${m}`);
  let paused = ctx.workspaceState.get<boolean>('tutor.paused', cfg().get<boolean>('pauseOnStart', false));
  const coalesce = new Map<string, NodeJS.Timeout>();

  // ---- UI bits
  const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  bar.command = 'tutor.togglePause';
  const topicBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99.5);
  topicBar.command = 'tutor.chooseLesson';
  topicBar.text = '$(book) Topic';
  topicBar.tooltip = 'Click to choose a lesson topic and open in sandbox';
  topicBar.show();
  const hintBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99);
  hintBar.command = 'tutor.reply';
  const renderBar = () => {
    if (paused) {
      bar.text = '$(debug-pause) Tutor paused';
      bar.tooltip = 'Nothing is being sent. Click to resume.';
      bar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    } else {
      bar.text = client.connected ? '$(mortar-board) Tutor' : '$(mortar-board) Tutor (offline)';
      bar.tooltip = client.connected ? 'Watching allowlisted lesson folders. Click to pause.' : 'Daemon not reachable; retrying...';
      bar.backgroundColor = undefined;
    }
    bar.show();
  };
  const sid = () => cfg().get<string>('studentId', 'local-user');

  const panel = new TutorPanel(
    (t) => void sendReply(t),
    () => void chooseLesson(),
  );
  const client: DaemonClient = new DaemonClient(
    () => cfg().get<string>('daemonUrl', 'http://localhost:7777'),
    (c) => void onCommand(c),
    () => { renderBar(); if (client.connected && !paused) { resync(); } },
    log,
  );

  // ---- the single gate every outgoing event passes through
  function emit(ev: TutorEvent): void {
    if (paused) { return; }
    if (!isAllowed(ev.file, folders())) { return; }
    if (ev.content !== undefined) { ev.content = redact(ev.content); }
    ev.diagnostics?.forEach((d) => (d.message = redact(d.message).slice(0, 300)));
    client.send(ev);
  }

  function base(doc: vscode.TextDocument, type: TutorEvent['type']): TutorEvent {
    const ed = vscode.window.visibleTextEditors.find((e) => e.document === doc);
    const sel = ed?.selection;
    return {
      type, ts: Math.floor(Date.now() / 1000), file: doc.uri.fsPath, language: doc.languageId, student_id: sid(),
      cursor: sel ? { line: sel.active.line + 1, col: sel.active.character + 1 } : null,
      selection: sel && !sel.isEmpty
        ? { start: { line: sel.start.line + 1, col: sel.start.character + 1 }, end: { line: sel.end.line + 1, col: sel.end.character + 1 } }
        : null,
    };
  }
  const watched = (doc: vscode.TextDocument) => doc.uri.scheme === 'file';

  function resync(): void {
    const ed = vscode.window.activeTextEditor;
    if (ed && watched(ed.document)) { emit({ ...base(ed.document, 'focus'), content: ed.document.getText() }); }
  }

  // ---- editor listeners (all early-out when paused; pausing also cancels pending timers)
  ctx.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (paused || !watched(e.document) || e.contentChanges.length === 0) { return; }
      const key = e.document.uri.toString();
      clearTimeout(coalesce.get(key));
      coalesce.set(key, setTimeout(() => {
        coalesce.delete(key);
        emit({ ...base(e.document, 'edit'), content: e.document.getText() });
      }, cfg().get<number>('editCoalesceMs', 250)));
    }),
    vscode.window.onDidChangeActiveTextEditor((ed) => {
      if (ed && watched(ed.document)) { emit({ ...base(ed.document, 'focus'), content: ed.document.getText() }); }
    }),
    vscode.window.onDidChangeTextEditorSelection((e) => {
      if (watched(e.textEditor.document)) { emit(base(e.textEditor.document, 'cursor')); }
    }),
    vscode.languages.onDidChangeDiagnostics((e) => {
      if (paused) { return; }
      for (const uri of e.uris) {
        if (uri.scheme !== 'file') { continue; }
        const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === uri.toString());
        const diags = vscode.languages.getDiagnostics(uri).slice(0, 50).map((d) => ({
          line: d.range.start.line + 1, severity: SEVERITY[d.severity] ?? 'info', message: d.message }));
        emit({ type: 'diagnostics', ts: Math.floor(Date.now() / 1000), file: uri.fsPath,
               language: doc?.languageId ?? '', student_id: sid(), diagnostics: diags });
      }
    }),
    vscode.workspace.onDidOpenTextDocument((d) => { if (watched(d)) { emit({ ...base(d, 'file_open'), action: 'open' }); } }),
    vscode.workspace.onDidCloseTextDocument((d) => { if (watched(d)) { emit({ ...base(d, 'file_open'), action: 'close' }); } }),
    vscode.workspace.onDidChangeConfiguration((e) => { if (e.affectsConfiguration('tutor.daemonUrl')) { client.reconnect(); } }),
  );

  // ---- commands from the user
  async function setPaused(p: boolean): Promise<void> {
    paused = p;
    await ctx.workspaceState.update('tutor.paused', p);
    if (p) { coalesce.forEach(clearTimeout); coalesce.clear(); }
    renderBar();
    try { await client.postJson('/api/pause', { paused: p }, 3000); } catch { /* daemon also drops nothing it never receives */ }
    if (!p) { resync(); }
    vscode.window.setStatusBarMessage(p ? 'Tutor paused: nothing is sent' : 'Tutor resumed', 3000);
  }

  async function sendReply(text: string): Promise<void> {
    if (paused) { vscode.window.showInformationMessage('Tutor is paused.'); return; }
    try {
      const payload: Record<string, any> = {
        text: redact(text),
        student_id: sid(),
      };
      const ed = vscode.window.activeTextEditor;
      if (ed && watched(ed.document)) {
        payload.file = ed.document.uri.fsPath;
        payload.language = ed.document.languageId;
        payload.content = redact(ed.document.getText());
        if (ed.selection) {
          payload.cursor = { line: ed.selection.active.line + 1, col: ed.selection.active.character + 1 };
          if (!ed.selection.isEmpty) {
            payload.selection = {
              start: { line: ed.selection.start.line + 1, col: ed.selection.start.character + 1 },
              end: { line: ed.selection.end.line + 1, col: ed.selection.end.character + 1 },
            };
          }
        }
      }
      const res = await client.postJson<{ question?: string }>('/api/reply', payload);
      if (res && res.question) {
        panel.show(res.question);
      }
    } catch (e) {
      panel.showError(String(e));
      vscode.window.showWarningMessage(`Tutor: could not send reply (${e})`);
    }
  }

  async function promptReply(): Promise<void> {
    const text = await vscode.window.showInputBox({ prompt: 'Reply to the pet', ignoreFocusOut: true });
    if (text) { await sendReply(text); }
  }

  async function runTests(): Promise<void> {
    const ed = vscode.window.activeTextEditor;
    if (!ed || !watched(ed.document)) { vscode.window.showWarningMessage('Open a lesson file first.'); return; }
    if (!isAllowed(ed.document.uri.fsPath, folders())) {
      vscode.window.showWarningMessage('This file is outside your allowlisted lessons folder.'); return;
    }
    await ed.document.save();
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'Tutor: running tests...' }, async () => {
      try {
        const r = await client.postJson('/api/run_tests',
          { file: ed.document.uri.fsPath, language: ed.document.languageId, student_id: sid() });
        const results: any[] = r.results ?? [];
        const passed = results.filter((x) => x.passed).length;
        out.clear();
        out.appendLine(`${r.lesson_id} (${r.language}) ${passed}/${results.length} passed in ${r.duration_ms} ms`);
        for (const t of results) {
          out.appendLine(`${t.passed ? 'PASS' : 'FAIL'}  ${t.name}`);
          if (!t.passed) { out.appendLine(`      expected ${t.expected}, got ${t.actual}${t.error_type ? ` (${t.error_type})` : ''}`); }
        }
        out.show(true);
        const msg = `Tutor: ${passed}/${results.length} tests passed`;
        if (r.all_passed) { vscode.window.showInformationMessage(msg + ' 🎉'); } else { vscode.window.showWarningMessage(msg); }
      } catch (e) {
        vscode.window.showErrorMessage(`Tutor: test run failed (${e})`);
      }
    });
  }

  // ---- commands received from the daemon (allowlist enforced again here)
  async function onCommand(c: Command): Promise<void> {
    log(`command ${c.command}`);
    switch (c.command) {
      case 'show_hint': {
        const text = String(c.text ?? '');
        hintBar.text = `$(comment) ${text.length > 60 ? text.slice(0, 57) + '...' : text}`;
        hintBar.tooltip = `${text}\n\n(click to reply)`;
        hintBar.show();
        panel.show(text);
        vscode.window.setStatusBarMessage(`Tutor: ${text}`, 20000);
        break;
      }
      case 'open_file': {
        const f = String(c.file ?? '');
        if (!isAllowed(f, folders())) { return; }
        const doc = await vscode.workspace.openTextDocument(f);
        const ed = await vscode.window.showTextDocument(doc);
        const line = Math.max(0, Number(c.line ?? 1) - 1);
        const pos = new vscode.Position(line, 0);
        ed.selection = new vscode.Selection(pos, pos);
        ed.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
        break;
      }
      case 'open_lesson': {
        const p = String(c.path ?? '');
        if (!isAllowed(p, folders())) { return; }
        const uri = vscode.Uri.file(p);
        if (vscode.workspace.workspaceFolders?.length) {
          vscode.workspace.updateWorkspaceFolders(vscode.workspace.workspaceFolders.length, 0, { uri, name: path.basename(p) });
        } else {
          await vscode.commands.executeCommand('vscode.openFolder', uri);
        }
        break;
      }
      case 'accept_reply':
        await promptReply();
        break;
      default:
        log(`unknown command ${c.command}`);
    }
  }

  async function chooseLesson(): Promise<void> {
    const wsFolders = vscode.workspace.workspaceFolders || [];
    let lessonsDir: string | null = null;
    for (const f of wsFolders) {
      const candidate = path.join(f.uri.fsPath, 'lessons');
      if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) {
        lessonsDir = candidate;
        break;
      }
      // Or if current folder is itself inside ps5 or is lessons
      const candidatePs5 = path.join(f.uri.fsPath, '..', 'lessons');
      if (fs.existsSync(candidatePs5) && fs.statSync(candidatePs5).isDirectory()) {
        lessonsDir = path.resolve(candidatePs5);
        break;
      }
    }

    if (!lessonsDir) {
      // Default fallback location for the project
      const fallback = 'C:\\Users\\D Venkata Abhishek\\Desktop\\ps5\\lessons';
      if (fs.existsSync(fallback)) {
        lessonsDir = fallback;
      }
    }

    if (!lessonsDir || !fs.existsSync(lessonsDir)) {
      vscode.window.showErrorMessage('Could not find the lessons/ directory.');
      return;
    }

    // Discover topics from lessons/
    const entries = fs.readdirSync(lessonsDir, { withFileTypes: true });
    interface LessonItem extends vscode.QuickPickItem {
      dirName: string;
      fullPath: string;
    }

    const items: LessonItem[] = [];
    for (const ent of entries) {
      if (ent.isDirectory() && !ent.name.startsWith('_') && !ent.name.startsWith('.')) {
        const lessonYamlPath = path.join(lessonsDir, ent.name, 'lesson.yaml');
        let title = ent.name.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
        let track = 'dsa';
        if (fs.existsSync(lessonYamlPath)) {
          try {
            const raw = fs.readFileSync(lessonYamlPath, 'utf8');
            const titleMatch = raw.match(/^title:\s*["']?([^"'\r\n]+)/m);
            if (titleMatch) title = titleMatch[1].trim();
            const trackMatch = raw.match(/^track:\s*["']?([^"'\r\n]+)/m);
            if (trackMatch) track = trackMatch[1].trim();
          } catch { /* use defaults */ }
        }
        items.push({
          label: `$(book) ${title}`,
          description: track.toUpperCase(),
          detail: `Folder: lessons/${ent.name}`,
          dirName: ent.name,
          fullPath: path.join(lessonsDir, ent.name),
        });
      }
    }

    if (items.length === 0) {
      vscode.window.showWarningMessage('No lessons found in ' + lessonsDir);
      return;
    }

    const pick = await vscode.window.showQuickPick(items, {
      placeHolder: 'Select a lesson topic to open in your sandbox...',
      matchOnDescription: true,
      matchOnDetail: true,
    });

    if (!pick) { return; }

    const lessonUri = vscode.Uri.file(pick.fullPath);
    vscode.window.showInformationMessage(`Opening topic: ${pick.label.replace('$(book) ', '')}`);

    // If already in a workspace, open the starter file or open the folder
    const starterDir = path.join(pick.fullPath, 'starter');
    let targetFile: string | null = null;
    if (fs.existsSync(starterDir)) {
      const candidates = ['solution.py', 'solution.js', 'solution.go', 'Solution.java', 'design.md'];
      for (const c of candidates) {
        const full = path.join(starterDir, c);
        if (fs.existsSync(full)) {
          targetFile = full;
          break;
        }
      }
    }

    // Open the folder in the workspace and reveal the starter solution
    const isInsideChosenFolder = wsFolders.some(f => f.uri.fsPath.toLowerCase() === pick.fullPath.toLowerCase());
    if (isInsideChosenFolder) {
      if (targetFile) {
        const doc = await vscode.workspace.openTextDocument(targetFile);
        await vscode.window.showTextDocument(doc);
      }
    } else {
      // If we are in a multi-root or single folder workspace, switch or open folder
      await vscode.commands.executeCommand('vscode.openFolder', lessonUri, { forceNewWindow: false });
    }
  }

  ctx.subscriptions.push(
    vscode.commands.registerCommand('tutor.chooseLesson', chooseLesson),
    vscode.commands.registerCommand('tutor.runTests', runTests),
    vscode.commands.registerCommand('tutor.togglePause', () => setPaused(!paused)),
    vscode.commands.registerCommand('tutor.reply', promptReply),
    vscode.window.registerWebviewViewProvider('tutor.panel', panel, { webviewOptions: { retainContextWhenHidden: true } }),
    bar, topicBar, hintBar, out, { dispose: () => client.dispose() },
  );

  renderBar();
  client.connect();
  if (paused) { void client.postJson('/api/pause', { paused: true }, 3000).catch(() => undefined); }
  log(`activated; allowlist=${folders().map(expandHome).join(', ')}; paused=${paused}`);

  // If no editor file is currently open or outside lessons, prompt user to pick a topic
  setTimeout(() => {
    const activeEd = vscode.window.activeTextEditor;
    if (!activeEd || !isAllowed(activeEd.document.uri.fsPath, folders())) {
      void chooseLesson();
    }
  }, 1000);
}

export function deactivate(): void { /* disposables handle cleanup */ }
