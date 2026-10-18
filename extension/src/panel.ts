import * as vscode from 'vscode';

/** Side panel: shows the pet's Socratic questions and lets the student type replies. */
export class TutorPanel implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private history: { text: string; isUser: boolean; time: string }[] = [];

  constructor(
    private onReply: (text: string) => void,
    private onChooseLesson?: () => void,
  ) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    const nonce = Math.random().toString(36).slice(2);

    view.webview.html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
  <style>
    :root {
      --bg: var(--vscode-sideBar-background, #1e1e1e);
      --fg: var(--vscode-sideBar-foreground, #cccccc);
      --widget-bg: var(--vscode-editorWidget-background, #252526);
      --user-bg: var(--vscode-button-secondaryBackground, #313131);
      --user-fg: var(--vscode-button-secondaryForeground, #ffffff);
      --accent: var(--vscode-activityBarBadge-background, #7c9cff);
      --btn-bg: var(--vscode-button-background, #0e639c);
      --btn-fg: var(--vscode-button-foreground, #ffffff);
      --btn-hover: var(--vscode-button-hoverBackground, #1177bb);
      --input-bg: var(--vscode-input-background, #3c3c3c);
      --input-fg: var(--vscode-input-foreground, #cccccc);
      --input-border: var(--vscode-input-border, rgba(255, 255, 255, 0.12));
      --focus-border: var(--vscode-focusBorder, #007fd4);
      --badge-bg: var(--vscode-badge-background, #4d4d4d);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--vscode-font-family, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif);
      font-size: var(--vscode-font-size, 13px);
      color: var(--fg);
      background: var(--bg);
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    /* Header */
    .header {
      padding: 10px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--input-border);
      background: rgba(0, 0, 0, 0.1);
    }
    .header-left {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .pet-icon {
      font-size: 18px;
      line-height: 1;
    }
    .header-title {
      font-weight: 600;
      font-size: 12px;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      opacity: 0.9;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .btn-topic {
      background: rgba(255, 255, 255, 0.08);
      color: var(--fg);
      border: 1px solid var(--input-border);
      border-radius: 4px;
      padding: 3px 8px;
      font-size: 11px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      transition: all 0.15s ease;
    }
    .btn-topic:hover {
      background: var(--btn-bg);
      color: var(--btn-fg);
      border-color: var(--btn-bg);
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 11px;
      padding: 2px 7px;
      border-radius: 10px;
      background: rgba(76, 175, 80, 0.15);
      color: #81c784;
    }
    .status-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #4caf50;
      box-shadow: 0 0 6px rgba(76, 175, 80, 0.8);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(0.85); }
    }
    /* Messages */
    #log {
      flex: 1;
      overflow-y: auto;
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    #log::-webkit-scrollbar { width: 6px; }
    #log::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.15); border-radius: 3px; }
    .msg-wrap {
      display: flex;
      flex-direction: column;
      max-width: 92%;
      animation: fadeIn 0.18s ease-out;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(4px); }
      to { opacity: 1; transform: none; }
    }
    .msg-wrap.tutor {
      align-self: flex-start;
    }
    .msg-wrap.user {
      align-self: flex-end;
    }
    .msg-meta {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 10px;
      opacity: 0.65;
      margin-bottom: 4px;
      padding: 0 2px;
    }
    .msg-wrap.user .msg-meta {
      justify-content: flex-end;
    }
    .msg-bubble {
      padding: 10px 12px;
      border-radius: 8px;
      line-height: 1.45;
      font-size: 12.5px;
      word-break: break-word;
    }
    .msg-wrap.tutor .msg-bubble {
      background: var(--widget-bg);
      border-left: 3px solid var(--accent);
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
    }
    .msg-wrap.user .msg-bubble {
      background: var(--user-bg);
      color: var(--user-fg);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }
    /* Typing indicator */
    .typing-indicator {
      display: none;
      align-self: flex-start;
      align-items: center;
      gap: 5px;
      padding: 8px 12px;
      background: var(--widget-bg);
      border-radius: 8px;
      font-size: 11px;
      opacity: 0.75;
      border-left: 3px solid var(--accent);
    }
    .typing-dots {
      display: flex;
      gap: 3px;
    }
    .dot {
      width: 4px;
      height: 4px;
      background: var(--accent);
      border-radius: 50%;
      animation: bounce 1.2s infinite ease-in-out;
    }
    .dot:nth-child(2) { animation-delay: 0.2s; }
    .dot:nth-child(3) { animation-delay: 0.4s; }
    @keyframes bounce {
      0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
      40% { transform: scale(1.1); opacity: 1; }
    }
    /* Quick chips */
    .quick-chips {
      display: flex;
      gap: 6px;
      overflow-x: auto;
      padding: 6px 14px 2px;
      scrollbar-width: none;
    }
    .quick-chips::-webkit-scrollbar { display: none; }
    .chip {
      white-space: nowrap;
      font-size: 11px;
      padding: 4px 9px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--input-border);
      border-radius: 12px;
      cursor: pointer;
      opacity: 0.8;
      transition: all 0.15s ease;
    }
    .chip:hover {
      opacity: 1;
      background: rgba(255, 255, 255, 0.1);
      border-color: var(--focus-border);
    }
    /* Footer input */
    .footer {
      padding: 10px 14px 12px;
      border-top: 1px solid var(--input-border);
      background: rgba(0, 0, 0, 0.15);
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .input-box {
      width: 100%;
      background: var(--input-bg);
      color: var(--input-fg);
      border: 1px solid var(--input-border);
      border-radius: 6px;
      padding: 8px 10px;
      font-family: inherit;
      font-size: 12.5px;
      line-height: 1.4;
      resize: none;
      min-height: 56px;
      outline: none;
      transition: border-color 0.15s;
    }
    .input-box:focus {
      border-color: var(--focus-border);
    }
    .input-box::placeholder {
      opacity: 0.5;
    }
    .action-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .key-hint {
      font-size: 10.5px;
      opacity: 0.5;
    }
    .btn-send {
      background: var(--btn-bg);
      color: var(--btn-fg);
      border: none;
      border-radius: 5px;
      padding: 6px 14px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      transition: background 0.15s;
    }
    .btn-send:hover {
      background: var(--btn-hover);
    }
    .btn-send:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="header-left">
      <span class="pet-icon">🐾</span>
      <span class="header-title">Socratic Tutor</span>
    </div>
    <div class="header-actions">
      <button id="btnTopic" class="btn-topic" title="Choose a lesson topic">
        <span>📚 Topic</span>
      </button>
      <div class="status-badge">
        <span class="status-dot"></span>
        <span>Live</span>
      </div>
    </div>
  </div>

  <div id="log"></div>

  <div class="typing-indicator" id="typing">
    <div class="typing-dots">
      <div class="dot"></div>
      <div class="dot"></div>
      <div class="dot"></div>
    </div>
    <span>Pet is thinking...</span>
  </div>

  <div class="quick-chips">
    <button class="chip" data-msg="Can you guide me on the loop condition?">Loop bounds?</button>
    <button class="chip" data-msg="What edge cases should I check for?">Edge cases?</button>
    <button class="chip" data-msg="How does time complexity scale here?">Complexity?</button>
  </div>

  <div class="footer">
    <textarea id="t" class="input-box" rows="2" placeholder="Explain your thinking or ask a guiding question..."></textarea>
    <div class="action-row">
      <span class="key-hint">↵ to send · Shift+↵ for newline</span>
      <button id="s" class="btn-send">
        <span>Reply</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
        </svg>
      </button>
    </div>
  </div>

  <script nonce="${nonce}">
    const vs = acquireVsCodeApi();
    const log = document.getElementById('log');
    const input = document.getElementById('t');
    const btn = document.getElementById('s');
    const typing = document.getElementById('typing');

    function getTime() {
      const now = new Date();
      return now.getHours().toString().padStart(2, '0') + ':' + now.getMinutes().toString().padStart(2, '0');
    }

    function addMessage(text, isUser, time) {
      typing.style.display = 'none';
      const wrap = document.createElement('div');
      wrap.className = 'msg-wrap ' + (isUser ? 'user' : 'tutor');

      const meta = document.createElement('div');
      meta.className = 'msg-meta';
      meta.textContent = (isUser ? '👤 You' : '🐾 Tutor') + ' · ' + (time || getTime());

      const bubble = document.createElement('div');
      bubble.className = 'msg-bubble';
      bubble.textContent = text;

      wrap.appendChild(meta);
      wrap.appendChild(bubble);
      log.appendChild(wrap);
      wrap.scrollIntoView({ behavior: 'smooth' });
    }

    function doSend(msgText) {
      const val = (msgText || input.value).trim();
      if (!val) return;
      addMessage(val, true);
      typing.style.display = 'flex';
      typing.scrollIntoView({ behavior: 'smooth' });
      vs.postMessage({ type: 'reply', text: val });
      if (!msgText) input.value = '';
    }

    const btnTopic = document.getElementById('btnTopic');
    if (btnTopic) {
      btnTopic.onclick = () => vs.postMessage({ type: 'chooseLesson' });
    }

    btn.onclick = () => doSend();

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        doSend();
      }
    });

    document.querySelectorAll('.chip').forEach(c => {
      c.onclick = () => doSend(c.getAttribute('data-msg'));
    });

    window.addEventListener('message', e => {
      if (e.data.type === 'hint') {
        addMessage(e.data.text, !!e.data.isUser, e.data.time);
      } else if (e.data.type === 'error') {
        typing.style.display = 'none';
        addMessage('⚠️ ' + e.data.text, false);
      }
    });

    vs.postMessage({ type: 'ready' });
  </script>
</body>
</html>`;

    view.webview.onDidReceiveMessage((m) => {
      if (m.type === 'reply') {
        this.history.push({ text: String(m.text), isUser: true, time: this.formatTime() });
        this.onReply(String(m.text));
      }
      if (m.type === 'chooseLesson') {
        if (this.onChooseLesson) {
          this.onChooseLesson();
        }
      }
      if (m.type === 'ready') {
        this.history.forEach((h) =>
          view.webview.postMessage({ type: 'hint', text: h.text, isUser: h.isUser, time: h.time }),
        );
      }
    });
  }

  show(text: string): void {
    if (this.history.length > 0 && this.history[this.history.length - 1].text === text && !this.history[this.history.length - 1].isUser) {
      return;
    }
    const time = this.formatTime();
    this.history = [...this.history.slice(-29), { text, isUser: false, time }];
    this.view?.webview.postMessage({ type: 'hint', text, time });
  }

  showError(text: string): void {
    this.view?.webview.postMessage({ type: 'error', text });
  }

  private formatTime(): string {
    const d = new Date();
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
  }
}
