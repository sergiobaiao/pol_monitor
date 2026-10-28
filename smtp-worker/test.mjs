// Testa o cliente SMTP contra um servidor falso (Node). Uso: node smtp-worker/test.mjs
import net from "node:net";
import { Duplex } from "node:stream";
import assert from "node:assert/strict";
import { sendMail } from "./smtp.js";

function fakeServer(opts = {}) {
  const got = { rcpts: [], starttls: false };
  const server = net.createServer((sock) => {
    let buf = "", inData = false, data = "";
    const say = (t) => sock.write(t + "\r\n");
    say("220 fake ESMTP");
    sock.on("data", (d) => {
      buf += d.toString("latin1");
      for (;;) {
        if (inData) {
          const end = buf.indexOf("\r\n.\r\n");
          if (end < 0) return;
          data = buf.slice(0, end); buf = buf.slice(end + 5); inData = false; got.data = data; say("250 queued"); continue;
        }
        const i = buf.indexOf("\r\n");
        if (i < 0) return;
        const l = buf.slice(0, i); buf = buf.slice(i + 2);
        if (l.startsWith("EHLO")) { say("250-fake"); say("250 AUTH PLAIN"); }
        else if (l === "STARTTLS") { got.starttls = true; say("220 go"); }
        else if (l.startsWith("AUTH PLAIN")) {
          got.auth = Buffer.from(l.split(" ")[2], "base64").toString();
          opts.denyAuth ? say("535-5.7.8 Username and Password not accepted\r\n535 5.7.8 more") : say("235 ok");
        }
        else if (l.startsWith("MAIL FROM")) { got.from = l; say("250 ok"); }
        else if (l.startsWith("RCPT TO")) { got.rcpts.push(l); say("250 ok"); }
        else if (l === "DATA") { inData = true; say("354 go"); }
        else if (l === "QUIT") { say("221 bye"); sock.end(); }
      }
    });
  });
  return new Promise((res) => server.listen(0, "127.0.0.1", () => res({ server, got, port: server.address().port })));
}

// Adapta um net.Socket ao formato de cloudflare:sockets (readable/writable web streams + startTls)
function wrap(port) {
  const sock = net.connect(port, "127.0.0.1");
  const { readable, writable } = Duplex.toWeb(sock);
  const s = { readable, writable, startTls: () => s };
  return s;
}

const mime = ["From: a@x.com", "To: b@y.com", "Subject: Teste", "", "linha 1", ".linha com ponto", "fim", ""].join("\r\n");
const raw = Buffer.from(mime, "latin1").toString("base64");

for (const port of [465, 587]) {
  const { server, got, port: fp } = await fakeServer();
  await sendMail(() => wrap(fp), { host: "x", port, user: "a@x.com", password: "abcdefghijklmnop", from: "a@x.com", to: ["b@y.com", "c@z.com"], raw });
  server.close();
  assert.equal(got.starttls, port === 587, `STARTTLS na porta ${port}`);
  assert.equal(got.auth, "\0a@x.com\0abcdefghijklmnop");
  assert.deepEqual(got.rcpts, ["RCPT TO:<b@y.com>", "RCPT TO:<c@z.com>"]);
  assert.ok(got.data.includes("\r\n..linha com ponto"), "dot-stuffing");
  assert.ok(got.data.endsWith("fim"), "fim da mensagem");
}

const { server, port: fp } = await fakeServer({ denyAuth: true });
await assert.rejects(
  sendMail(() => wrap(fp), { host: "x", port: 465, user: "a@x.com", password: "ruim", from: "a@x.com", to: ["b@y.com"], raw }),
  /^Error: 535/,
);
server.close();
console.log("OK: AUTH PLAIN, MAIL/RCPT/DATA, dot-stuffing, STARTTLS (587), TLS direto (465) e erro 535");
