// Worker auxiliar do POL ADV: envia e-mail por SMTP (TLS) para o Worker Python `pol-adv-web`, cujo runtime
// ainda não faz TLS em sockets. Só é acessível por service binding (RPC); não tem rota pública.
import { connect } from "cloudflare:sockets";
import { WorkerEntrypoint } from "cloudflare:workers";
import { sendMail } from "./smtp.js";

export class Smtp extends WorkerEntrypoint {
  /** @param {{host:string, port:number, user:string, password:string, from:string, to:string[], raw:string}} msg */
  async send(msg) {
    await sendMail(connect, msg);
    return "ok";
  }
}

export default {
  fetch() {
    return new Response("Not Found", { status: 404 });
  },
};
