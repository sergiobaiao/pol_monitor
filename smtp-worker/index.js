// Worker auxiliar do POL ADV: envia e-mail por SMTP (TLS) para o Worker Python `pol-adv-web`, cujo runtime
// ainda não faz TLS em sockets. Só é acessível por service binding (RPC); não tem rota pública.
import { connect } from "cloudflare:sockets";
import { WorkerEntrypoint } from "cloudflare:workers";
import { sendMail } from "./smtp.js";

export class Smtp extends WorkerEntrypoint {
  /** @param {string|object} arg JSON {host, port, user, password, from, to[], raw(base64)}. O Python manda texto JSON:
   *  evita a conversão de objetos Python→JS (to_js), que falhou com "Result should have type object not function". */
  async send(arg) {
    const msg = typeof arg === "string" ? JSON.parse(arg) : arg;
    await sendMail(connect, msg);
    return "ok";
  }

  /** Mesmo envio por HTTP via service binding (alternativa ao RPC): POST /send com o JSON no corpo. */
  async fetch(request) {
    if (request.method === "POST" && new URL(request.url).pathname === "/send") {
      try {
        await sendMail(connect, await request.json());
        return new Response("ok");
      } catch (e) {
        return new Response(String((e && e.message) || e), { status: 502 });
      }
    }
    return new Response("Not Found", { status: 404 });
  }
}

export default {
  fetch() {
    return new Response("Not Found", { status: 404 });
  },
};
