import { createIngress, readIngressToken } from './ingress.js';
const tokenFile = process.env.FACTORY_INGRESS_TOKEN_FILE;
const port = Number(process.env.FACTORY_INGRESS_GATEWAY_PORT ?? '9081');
const upstreamPort = Number(process.env.FACTORY_RESTATE_INGRESS_PORT ?? '8080');
if (!tokenFile || !Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid ingress configuration');
createIngress(readIngressToken(tokenFile), upstreamPort).listen(port, '127.0.0.1');
