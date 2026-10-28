// Cliente SMTP mínimo sobre sockets TCP. `connectFn` é o `connect` de "cloudflare:sockets" (injetado para poder
// testar em Node com um servidor falso). Suporta TLS direto (465) e STARTTLS (587).

class Conn {
  constructor(socket) {
    this.enc = new TextEncoder();
    this.dec = new TextDecoder();
    this.buf = "";
    this.attach(socket);
  }

  attach(socket) {
    this.socket = socket;
    this.reader = socket.readable.getReader();
    this.writer = socket.writable.getWriter();
  }

  async line() {
    for (;;) {
      const i = this.buf.indexOf("\r\n");
      if (i >= 0) {
        const l = this.buf.slice(0, i);
        this.buf = this.buf.slice(i + 2);
        return l;
      }
      const { value, done } = await this.reader.read();
      if (done) throw new Error("conexão encerrada pelo servidor");
      this.buf += this.dec.decode(value, { stream: true });
    }
  }

  async reply() {
    const lines = [];
    for (;;) {
      const l = await this.line();
      lines.push(l);
      if (l.length < 4 || l[3] !== "-") return { code: parseInt(l.slice(0, 3), 10), text: lines.join(" ") };
    }
  }

  async cmd(text, ok) {
    if (text !== null) await this.writer.write(this.enc.encode(text + "\r\n"));
    const r = await this.reply();
    if (!ok.includes(r.code)) throw new Error(`${r.code} ${r.text}`);
    return r;
  }

  /** Depois do 220 do STARTTLS: troca o socket por um com TLS. */
  upgrade() {
    this.reader.releaseLock();
    this.writer.releaseLock();
    this.buf = "";
    this.attach(this.socket.startTls());
  }

  async close() {
    try { await this.writer.close(); } catch { /* já fechado */ }
  }
}

/** `raw` = mensagem RFC 5322 em base64 (CRLF). Lança Error com o código/resposta do servidor se algo falhar. */
export async function sendMail(connectFn, { host, port, user, password, from, to, raw }) {
  const direct = port === 465;
  const socket = connectFn({ hostname: host, port }, { secureTransport: direct ? "on" : "starttls", allowHalfOpen: false });
  const c = new Conn(socket);
  try {
    await c.cmd(null, [220]);
    await c.cmd("EHLO pol-adv-web", [250]);
    if (!direct) {
      await c.cmd("STARTTLS", [220]);
      c.upgrade();
      await c.cmd("EHLO pol-adv-web", [250]);
    }
    await c.cmd("AUTH PLAIN " + btoa("\0" + user + "\0" + password), [235]);
    await c.cmd(`MAIL FROM:<${from}>`, [250]);
    for (const r of to) await c.cmd(`RCPT TO:<${r}>`, [250, 251]);
    await c.cmd("DATA", [354]);
    // atob devolve string binária (1 char = 1 byte): dot-stuffing e volta para bytes sem mexer na codificação
    const bin = atob(raw).replace(/\r?\n$/, "").replace(/^\./gm, "..");
    const bytes = Uint8Array.from(bin + "\r\n.\r\n", (ch) => ch.charCodeAt(0));
    await c.writer.write(bytes);
    await c.cmd(null, [250]);
    try { await c.cmd("QUIT", [221]); } catch { /* o Google às vezes fecha antes */ }
  } finally {
    await c.close();
  }
}
