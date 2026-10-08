'use strict';

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const BODY_LIMIT = 64 * 1024;
const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
  'Content-Security-Policy': "default-src 'self'; connect-src 'self' https://api.github.com; img-src 'self' data: https:; style-src 'self'; script-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; upgrade-insecure-requests"
};
const MIME = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8', '.xml': 'application/xml; charset=utf-8',
  '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.webp': 'image/webp', '.avif': 'image/avif',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf'
};

class RequestError extends Error {
  constructor(status, message) {super(message); this.status = status;}
}

function send(req, res, status, body, headers = {}, multiValueHeaders = {}) {
  res.statusCode = status;
  for (const [name, value] of Object.entries(headers)) {
    if (value !== undefined && value !== null) res.setHeader(name, value);
  }
  for (const [name, values] of Object.entries(multiValueHeaders)) {
    if (!Array.isArray(values)) continue;
    const existing = res.getHeader(name);
    const combined = [...(existing === undefined ? [] : Array.isArray(existing) ? existing : [existing]), ...values];
    res.setHeader(name, combined);
  }
  // An adapter cannot relax the site's browser security policy.
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) res.setHeader(name, value);
  const payload = Buffer.isBuffer(body) ? body : Buffer.from(String(body ?? ''));
  if (![204, 304].includes(status) && (req.method !== 'HEAD' || !res.hasHeader('Content-Length'))) res.setHeader('Content-Length', payload.length);
  if (req.method === 'HEAD' || [204, 304].includes(status)) res.end();
  else res.end(payload);
}

function fail(req, res, status, message) {
  send(req, res, status, JSON.stringify({error: message}), {
    'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store'
  });
}

function readBody(req) {
  const declared = Number(req.headers['content-length'] || 0);
  if (declared > BODY_LIMIT) {
    req.resume();
    return Promise.reject(new RequestError(413, 'O pedido excede o limite de 64 KB.'));
  }
  return new Promise((resolve, reject) => {
    let length = 0, chunks = [], rejected = false;
    req.on('data', chunk => {
      length += chunk.length;
      if (length > BODY_LIMIT) {
        chunks = [];
        if (!rejected) {rejected = true; reject(new RequestError(413, 'O pedido excede o limite de 64 KB.'));}
      } else if (!rejected) chunks.push(chunk);
    });
    req.on('end', () => {
      if (rejected) return;
      const body = Buffer.concat(chunks).toString('utf8');
      if (body) {
        if (!/^application\/json(?:\s*;|$)/i.test(req.headers['content-type'] || '')) {
          reject(new RequestError(415, 'Envie o corpo do pedido como application/json.')); return;
        }
        try {JSON.parse(body);} catch {reject(new RequestError(400, 'Corpo JSON inválido.')); return;}
      }
      resolve(body);
    });
    req.on('aborted', () => reject(new RequestError(400, 'O pedido foi interrompido.')));
    req.on('error', reject);
  });
}

function requestPath(target) {
  if (!target.startsWith('/') || target.startsWith('//')) throw new RequestError(400, 'Endereço inválido.');
  let pathname;
  try {pathname = decodeURIComponent(target.split('?')[0]);}
  catch {throw new RequestError(400, 'Endereço inválido.');}
  if (pathname.includes('\\') || /[\x00-\x1f\x7f]/.test(pathname) || pathname.split('/').some(part => part.startsWith('.'))) {
    throw new RequestError(403, 'Arquivo não disponível.');
  }
  return pathname;
}

function inside(root, filename) {
  const relative = path.relative(root, filename);
  return relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)
    && !relative.split(path.sep).some(part => part.startsWith('.'));
}

function createServer({handler, siteRoot = path.join(__dirname, 'site'), trustProxy = false} = {}) {
  const root = fs.realpathSync(siteRoot);
  const apiHandler = handler || require('./netlify/functions/api.js').handler;
  if (typeof apiHandler !== 'function') throw new TypeError('O handler da API precisa ser uma função.');

  const server = http.createServer(async (req, res) => {
    try {
      const pathname = requestPath(req.url || '/');
      if (pathname === '/health') {
        if (!['GET', 'HEAD'].includes(req.method)) {
          send(req, res, 405, '', {Allow: 'GET, HEAD'}); return;
        }
        send(req, res, 200, JSON.stringify({status: 'ok'}), {
          'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store'
        }); return;
      }
      if (pathname.startsWith('/api/')) {
        const body = await readBody(req);
        const params = new URL(req.url, 'http://localhost').searchParams;
        const queryStringParameters = Object.fromEntries(params);
        const headers = {...req.headers};
        delete headers['x-forwarded-host'];
        delete headers.forwarded;
        const forwarded = String(req.headers['x-forwarded-proto'] || '').split(',')[0].trim().toLowerCase();
        headers['x-forwarded-proto'] = trustProxy && ['http', 'https'].includes(forwarded)
          ? forwarded : req.socket.encrypted ? 'https' : 'http';
        const response = await apiHandler({
          path: pathname, httpMethod: req.method, headers, queryStringParameters,
          multiValueQueryStringParameters: Object.fromEntries([...new Set(params.keys())].map(key => [key, params.getAll(key)])),
          body, isBase64Encoded: false
        });
        if (!response || !Number.isInteger(response.statusCode) || response.statusCode < 200 || response.statusCode > 599) {
          throw new Error('Resposta inválida da API.');
        }
        const payload = response.isBase64Encoded ? Buffer.from(response.body || '', 'base64') : response.body || '';
        send(req, res, response.statusCode, payload, response.headers, response.multiValueHeaders); return;
      }
      if (!['GET', 'HEAD'].includes(req.method)) {
        req.resume(); send(req, res, 405, '', {Allow: 'GET, HEAD'}); return;
      }
      const candidate = path.resolve(root, `.${pathname === '/' ? '/index.html' : pathname}`);
      if (!inside(root, candidate)) throw new RequestError(403, 'Arquivo não disponível.');
      let file, stat;
      try {
        file = await fs.promises.realpath(candidate);
        if (!inside(root, file)) throw new RequestError(403, 'Arquivo não disponível.');
        stat = await fs.promises.stat(file);
      } catch (error) {
        if (error instanceof RequestError) throw error;
        if (['ENOENT', 'ENOTDIR', 'EACCES', 'ELOOP'].includes(error.code)) throw new RequestError(404, 'Arquivo não encontrado.');
        throw error;
      }
      const type = MIME[path.extname(file).toLowerCase()];
      if (!stat.isFile() || !type) throw new RequestError(404, 'Arquivo não encontrado.');
      const cache = path.extname(file) === '.js' ? 'public, max-age=300'
        : pathname === '/styles.css' ? 'public, max-age=3600'
        : pathname === '/favicon.svg' ? 'public, max-age=86400' : 'no-cache';
      const body = req.method === 'HEAD' ? '' : await fs.promises.readFile(file);
      send(req, res, 200, body, {'Content-Type': type, 'Cache-Control': cache, 'Content-Length': stat.size});
    } catch (error) {
      if (!res.headersSent && !res.destroyed) {
        fail(req, res, error instanceof RequestError ? error.status : 500,
          error instanceof RequestError ? error.message : 'O servidor encontrou um erro inesperado.');
      } else if (!res.destroyed) res.destroy();
    }
  });
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  server.keepAliveTimeout = 5000;
  server.maxHeadersCount = 100;
  return server;
}

if (require.main === module) {
  if (Number(process.versions.node.split('.')[0]) < 22) {
    console.error('RHYFT Web precisa de Node.js 22 ou superior.'); process.exitCode = 1;
  } else {
    const port = Number(process.env.PORT || 8787), host = process.env.HOST || '127.0.0.1';
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      console.error('PORT precisa ser um número entre 1 e 65535.'); process.exitCode = 1;
    } else {
      const server = createServer({trustProxy: process.env.TRUST_PROXY === '1'});
      server.on('error', () => {console.error('Não foi possível iniciar o RHYFT Web. Confira HOST e PORT.'); process.exitCode = 1;});
      server.listen(port, host, () => console.log(`RHYFT Web: http://${host.includes(':') ? `[${host}]` : host}:${port}`));
      const shutdown = () => {
        server.close(() => process.exit(0));
        const timeout = setTimeout(() => {server.closeAllConnections(); process.exit(0);}, 10000);
        timeout.unref();
      };
      process.once('SIGINT', shutdown); process.once('SIGTERM', shutdown);
    }
  }
}

module.exports = {createServer};
